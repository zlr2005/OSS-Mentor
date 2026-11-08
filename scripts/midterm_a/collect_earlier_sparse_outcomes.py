"""Collect separate 2022/2023 closing outcomes; never promote to frozen X/Y."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path

import collect_targeted_adapter as t


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-network', action='store_true')
    parser.add_argument('--cohort',type=Path)
    parser.add_argument('--run-id',default='earlier_sparse_outcomes_2022_2023_v1')
    parser.add_argument('--start-year',type=int,default=2022)
    parser.add_argument('--end-year',type=int,default=2023)
    parser.add_argument('--pre-interaction',action='store_true')
    args = parser.parse_args()
    base = t.PARENT.parent
    cohort = args.cohort or base/'temporal_xy_2024_2026_v6/low_history_gaps.jsonl'
    if not 2008<=args.start_year<=args.end_year<=2023 or not args.run_id.replace('_','').isalnum():
        parser.error('Earlier years must be 2008-2023; simple run ID required')
    years = list(range(args.start_year,args.end_year+1))
    accounts = [dict(a, search_years=years, expanded_linked_only=True) for a in t.b.rows(cohort)
                if a.get('missing_before_three',1)>0]
    for account in accounts:
        account['current_train_outcomes'] += account.get('new_outcomes',0)
    t.OUT = t.b.OUT = base/args.run_id
    t.OUT.mkdir(exist_ok=True)
    api = t.b.API(args.allow_network)
    if args.cohort:
        for account in accounts:
            payload,_ = api.call(query='query { user(login:'+json.dumps(account['developer_key'])
                +') { databaseId createdAt } }')
            user = payload['data']['user']
            if not user or user['databaseId']!=account['github_numeric_id']:
                raise ValueError('Earlier cohort identity mismatch')
            account['search_years'] = [y for y in years if y>=t.b.dt(user['createdAt']).year]
    indexed, coverage = [], []
    if not (t.OUT/'selected_prs.json').exists():
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(t.discover_expanded, api, a) for a in accounts]
            for done, future in enumerate(as_completed(futures),1):
                records, pages = future.result()
                indexed.extend(records)
                coverage.extend(pages)
                if done%20==0 or done==len(accounts):
                    print(json.dumps(dict(searches_done=done,total=len(accounts))),flush=True)
        t.b.lines('discovery_index.jsonl',indexed)
        t.b.save('search_coverage.json',coverage)
        eligible = [r for r in indexed if t.b.quarter(r['created_at']) < t.b.dt(r['created_at'])
            <= t.b.dt(r['merged_at']) <= t.b.quarter(r['created_at'])+t.b.timedelta(days=90)]
        selected = t.selector.choose(eligible,[],t.b.read(t.b.SEEDS),len(eligible),years=years)
        t.b.save('selected_prs.json',selected)
    t.b.prefetch_details(api,{})
    known = {}
    for path in (t.OUT/'api_cache').glob('*.json'):
        payload = t.b.read(path)['payload']
        for value in payload.get('data',{}).values():
            if isinstance(value,dict) and value.get('pullRequest'):
                known[value['pullRequest']['url']] = value['pullRequest'],path.relative_to(t.OUT).as_posix()
    # A deleted/inaccessible repository can fail a whole GraphQL batch.
    # Retry its members individually so valid neighbours are not discarded.
    read_exclusions = []
    for seed in t.b.read(t.OUT/'selected_prs.json'):
        if seed['url'] in known:
            continue
        owner, repo = seed['repository'].split('/')
        query = ('query { q0: repository(owner:'+json.dumps(owner)+',name:'+json.dumps(repo)
                 +f') {{ pullRequest(number:{seed["number"]}) {{ number url createdAt mergedAt merged '
                 +t.b.AUTHOR+' closingIssuesReferences(first:10) { '+t.b.PAGE+' nodes { '
                 +t.b.ISSUE_FIELDS+' } } } } }')
        try:
            payload, ref = api.call(query=query)
            pr = (payload['data']['q0'] or {}).get('pullRequest')
            if pr:
                known[seed['url']] = pr, ref
            else:
                read_exclusions.append(dict(pr_url=seed['url'],reason='individual_PR_unavailable'))
        except ValueError:
            read_exclusions.append(dict(pr_url=seed['url'],reason='individual_read_failed_after_retries'))
    t.b.lines('read_exclusions.jsonl',read_exclusions)
    targets = dict(sparse_ids={a['github_numeric_id'] for a in accounts},training_years=years,
                   training_repositories=set(),allow_test=False)
    if args.pre_interaction:
        targets['profile_cutoff_policy'] = 'pre_interaction'
    accepted, excluded = {}, []
    for seed in t.b.read(t.OUT/'selected_prs.json'):
        if seed['url'] not in known:
            continue
        pr, ref = known[seed['url']]
        rows, reasons = t.outcome_rows(pr,seed,ref,targets,set())
        for row in rows:
            row.pop('new_repository_basis',None)
            row.update(intended_split='earlier_history_pending_contract_review',training_ready=False)
            accepted[row['outcome_id']] = row
        excluded.extend(reasons)
    t.b.lines('verified_outcomes.jsonl',list(accepted.values()))
    t.b.lines('outcome_exclusions.jsonl',excluded)
    counts, cutoffs = {}, {}
    for row in accepted.values():
        identity = row['github_numeric_id']
        counts[identity] = counts.get(identity,0)+1
        cutoffs.setdefault(identity,set()).add(row['profile_as_of'])
    t.b.lines('account_coverage.jsonl',[dict(a,earlier_verified_outcomes=counts.get(a['github_numeric_id'],0),
        earlier_cutoffs=sorted(cutoffs.get(a['github_numeric_id'],set())),
        missing_before_three=max(0,3-a['current_train_outcomes']-counts.get(a['github_numeric_id'],0))) for a in accounts])
    summary = dict(verified_outcomes=len(accepted),developers_with_earlier_outcomes=len(counts),
        accounts_reaching_three_if_admitted=sum(a['current_train_outcomes']+counts.get(a['github_numeric_id'],0)>=3 for a in accounts),
        selected_prs=len(t.b.read(t.OUT/'selected_prs.json')),unavailable_prs=len(read_exclusions),training_ready=False,
        note='Earlier outcomes require pre-cutoff history collection and B/C time-contract review before admission.')
    t.b.save('summary.json',summary)
    t.b.save('manifest.json',dict(cohort_sha256=t.b.digest(cohort.read_bytes()),
        collector_sha256=t.b.digest(Path(__file__).read_bytes()),
        targeted_collector_sha256=t.b.digest(Path(t.__file__).read_bytes()),
        files_sha256={p.relative_to(t.OUT).as_posix():t.b.digest(p.read_bytes()) for p in t.OUT.rglob('*')
                      if p.is_file() and p.name!='manifest.json'}))
    print(json.dumps(summary),flush=True)


if __name__ == '__main__':
    main()
