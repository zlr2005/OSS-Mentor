"""独立补采低历史训练结果和 v4 训练输入未见仓库的 Q3 测试结果。"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import shutil

import backfill_adapter_2024_2026 as b
import continue_adapter_backfill as selector
import deepen_adapter_evidence as deep

PARENT = b.OUT
OUT = PARENT.parent / 'advanced_adapter_targeted_20261104_v1'
TARGETS = b.ROOT / 'tmp/adapter_targeted_20261104_v1/target_accounts.json'
COHORT = b.ROOT / 'tmp/adapter_sparse_20261104_v1/c_experiment_cohort.json'
PRIOR = []


class API(b.API):
    def call(self, *, query=None, endpoint=None):
        key = b.digest(b.canonical({'query': query, 'endpoint': endpoint})) + '.json'
        source = next((root / 'api_cache' / key for root in [PARENT, *PRIOR]
                       if (root / 'api_cache' / key).exists()), PARENT / 'api_cache' / key)
        target = OUT / 'api_cache' / key
        if not target.exists() and source.exists():
            with b.LOCK:
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
        return super().call(query=query, endpoint=endpoint)


def discover_one(api, account, split):
    if split == 'train' and account.get('expanded_search'):
        return discover_expanded(api, account)
    dates = ('created:2024-04-01..2025-12-31 merged:<2026-01-01' if split == 'train'
             else 'linked:issue created:2026-07-01..2026-09-29 merged:<2026-10-01')
    if split == 'train' and account.get('linked_only'):
        dates = 'linked:issue ' + dates
    search = f'author:{account["developer_key"]} is:pr is:merged {dates} sort:created-asc'
    cursor, records, coverage = None, [], []
    for page in range(1, 4):
        after = ',after:' + json.dumps(cursor) if cursor else ''
        query = ('query { q0: search(query:' + json.dumps(search) + ',type:ISSUE,first:100' + after
                 + ') { issueCount ' + b.PAGE + ' nodes { ... on PullRequest { number url createdAt mergedAt '
                 + b.AUTHOR + ' } } } }')
        payload, ref = api.call(query=query)
        result = payload['data']['q0']
        coverage.append({'account': account['github_numeric_id'], 'split': split, 'page': page,
                         'evidence_ref': ref, 'query': search, **result['pageInfo']})
        for node in result['nodes']:
            if node and (node.get('author') or {}).get('databaseId') == account['github_numeric_id']:
                records.append(dict(pr_url=node['url'], repository=node['url'].split('github.com/')[1].split('/pull/')[0],
                    pr_number=node['number'], created_at=node['createdAt'], merged_at=node['mergedAt'],
                    github_numeric_id=account['github_numeric_id'], github_login=node['author']['login'],
                    verified_seed_author=True, evidence_refs=[ref], split=split))
        if not result['pageInfo']['hasNextPage']:
            break
        cursor = result['pageInfo']['endCursor']
        if not cursor:
            raise ValueError('Missing search cursor')
    return records, coverage


def discover_expanded(api, account):
    """Search all merged PRs by quarter, splitting GitHub's 1,000-result cap.

    Linked-issue search misses manually referenced or unindexed closing links.
    This discovers candidates only; actual closing events remain mandatory.
    """
    records, coverage = {}, []

    def visit(start, end):
        search = (f'author:{account["developer_key"]} is:pr is:merged '
                  f'created:{start}..{end} merged:<2026-01-01 sort:created-desc')
        if account.get('expanded_linked_only'):
            search += ' linked:issue'
        cursor = None
        for page in range(1, 11):
            after = ',after:' + json.dumps(cursor) if cursor else ''
            query = ('query { q0: search(query:' + json.dumps(search) + ',type:ISSUE,first:100' + after
                     + ') { issueCount ' + b.PAGE + ' nodes { ... on PullRequest { number url createdAt mergedAt '
                     + b.AUTHOR + ' } } } }')
            payload, ref = api.call(query=query)
            result = payload['data']['q0']
            saturated = result['issueCount'] > 1000
            coverage.append(dict(account=account['github_numeric_id'], split='train', page=page,
                start=str(start), end=str(end), query=search, evidence_ref=ref,
                issue_count=result['issueCount'], partition_split=saturated, **result['pageInfo']))
            if saturated:
                if start == end:
                    raise ValueError('Single day exceeds GitHub search limit; coverage incomplete')
                middle = start + (end - start) // 2
                visit(start, middle)
                visit(middle + b.timedelta(days=1), end)
                return
            for node in result['nodes']:
                if node and (node.get('author') or {}).get('databaseId') == account['github_numeric_id']:
                    records[node['url'].casefold()] = dict(pr_url=node['url'],
                        repository=node['url'].split('github.com/')[1].split('/pull/')[0],
                        pr_number=node['number'], created_at=node['createdAt'], merged_at=node['mergedAt'],
                        github_numeric_id=account['github_numeric_id'], github_login=node['author']['login'],
                        verified_seed_author=True, evidence_refs=[ref], split='train')
            if not result['pageInfo']['hasNextPage']:
                return
            cursor = result['pageInfo']['endCursor']
            if not cursor:
                raise ValueError('Missing expanded search cursor')
        raise ValueError('Expanded search partition not exhausted')

    windows = [(year, month) for year in account.get('search_years', [2024, 2025])
               for month in (1, 4, 7, 10) if (year, month) != (2024, 1)]
    for year, month in windows:
        start = b.dt(f'{year}-{month:02}-01T00:00:00Z').date()
        end = b.dt(f'{year + (month == 10)}-{1 if month == 10 else month + 3:02}-01T00:00:00Z').date() - b.timedelta(days=1)
        visit(start, end)
    return list(records.values()), coverage


def outcome_rows(pr, seed, ref, targets, existing):
    if not pr or not pr['merged'] or (pr.get('author') or {}).get('databaseId') != seed['author']['databaseId']:
        return [], [{'pr_url': seed['url'], 'reason': 'identity_or_merge_mismatch'}]
    cutoff = b.quarter(pr['createdAt'])
    if targets.get('profile_cutoff_policy') == 'pre_interaction':
        cutoff = b.dt(pr['createdAt']) - b.timedelta(seconds=1)
    accepted, excluded = [], []
    for issue in pr['closingIssuesReferences']['nodes']:
        repo = issue['repository']['nameWithOwner']
        identity = pr['author']['databaseId']
        proofs = [e for e in issue['timelineItems']['nodes'] if e['__typename'] == 'ClosedEvent'
                  and (e.get('closer') or {}).get('url') == pr['url']]
        if not proofs:
            excluded.append({'pr_url': pr['url'], 'issue_url': issue['url'], 'reason': 'no_actual_closing_event'})
            continue
        closed = max(b.dt(pr['mergedAt']), b.dt(proofs[0]['createdAt']))
        train = cutoff.year in targets.get('training_years', (2024, 2025)) and identity in targets['sparse_ids'] and closed < b.dt('2026-01-01T00:00:00Z')
        test = targets.get('allow_test', True) and cutoff == b.dt('2026-07-01T00:00:00Z') and repo.casefold() not in targets['training_repositories']
        if not (train or test) or not cutoff < b.dt(pr['createdAt']) <= closed <= cutoff + b.timedelta(days=90):
            excluded.append({'pr_url': pr['url'], 'issue_url': issue['url'], 'reason': 'outside_target_or_temporal_scope'})
            continue
        candidate, reason = b.issue_at(issue, cutoff)
        if reason:
            excluded.append({'pr_url': pr['url'], 'issue_url': issue['url'], 'reason': reason})
            continue
        outcome_id = pr['url'] + '->' + candidate['issue_key']
        if outcome_id in existing:
            continue
        accepted.append(dict(outcome_id=outcome_id, year=cutoff.year, repository=repo,
            pr_repository=seed['repository'], developer_key=pr['author']['login'].casefold(),
            github_numeric_id=identity, seed_identity=seed['seed_identity'], profile_as_of=b.iso(cutoff),
            interaction_at=pr['createdAt'], outcome_at=b.iso(closed), outcome_type='verified_closing_pr',
            verification_status='verified', pr_url=pr['url'], issue_key=candidate['issue_key'],
            issue_content=candidate, evidence_ref=ref, intended_split='train' if train else 'test',
            new_repository_basis='Absent from v4 training X repositories and Y repositories. Base model mappings require C audit.'))
    if pr['closingIssuesReferences']['pageInfo']['hasNextPage']:
        excluded.append({'pr_url': pr['url'], 'reason': 'closing_issue_list_truncated'})
    if not pr['closingIssuesReferences']['nodes']:
        excluded.append({'pr_url': pr['url'], 'reason': 'no_closing_issue_reference'})
    return accepted, excluded


def import_history():
    windows = {(r['github_numeric_id'], r['profile_as_of']) for r in b.rows(OUT / 'verified_outcomes.jsonl')}
    collected = b.rows(OUT / 'historical_contributions.jsonl')
    seen = {(r['github_numeric_id'], r['as_of'], r['repository'].casefold(), r['commit_sha'], r['path']) for r in collected}
    for source in [PARENT, PARENT.parent / 'advanced_adapter_depth_20261104_v1', PARENT.parent / 'advanced_adapter_depth_20261104_v2', *PRIOR]:
        for row in b.rows(source / 'historical_contributions.jsonl'):
            key = row['github_numeric_id'], row['as_of'], row['repository'].casefold(), row['commit_sha'], row['path']
            if (row['github_numeric_id'], row['as_of']) not in windows or key in seen:
                continue
            for field in ('content_location', 'evidence_ref', 'blob_evidence_ref', 'listing_evidence_ref'):
                relative = row[field].replace('\\', '/')
                src, dest = (source / relative).resolve(), (OUT / relative).resolve()
                if not src.is_relative_to(source.resolve()) or not dest.is_relative_to(OUT.resolve()):
                    raise ValueError('Evidence path outside source root')
                if dest.exists() and b.digest(src.read_bytes()) != b.digest(dest.read_bytes()):
                    raise ValueError('Evidence content conflict')
                if not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
            collected.append(row)
            seen.add(key)
    b.lines('historical_contributions.jsonl', collected)


def verify():
    for name, expected in b.read(OUT / 'manifest.json')['files_sha256'].items():
        path = (OUT / name).resolve()
        if not path.is_relative_to(OUT.resolve()) or b.digest(path.read_bytes()) != expected:
            raise ValueError('Targeted package checksum mismatch')
    # 原校验器的逐条历史回放、作者、时间和内容检查也用于这个独立包。
    b.verify()


def repair_global_history(api):
    deep.OUT = OUT
    contributions = b.rows(OUT / 'historical_contributions.jsonl')
    covered = {(r['github_numeric_id'], r['as_of']) for r in contributions}
    missing = {}
    for row in b.rows(OUT / 'verified_outcomes.jsonl'):
        key = row['github_numeric_id'], row['profile_as_of']
        if key not in covered:
            missing.setdefault(key, row)
    def collect_window(item):
        (identity, as_of), row = item
        key = b.digest(b.canonical([identity, as_of]))
        path = OUT / 'global_history_windows' / (key + '.json')
        if path.exists():
            return b.read(path)
        search = (f'author:{row["developer_key"]} is:pr is:merged '
                  f'merged:2024-01-01..{as_of[:10]} sort:updated-desc')
        query = ('query { q0: search(query:' + json.dumps(search) + ',type:ISSUE,first:10) { '
                 + b.PAGE + ' nodes { ... on PullRequest { url mergedAt repository { nameWithOwner } '
                 + b.AUTHOR + ' } } } }')
        payload, ref = api.call(query=query)
        nodes = [n for n in payload['data']['q0']['nodes'] if n and (n.get('author') or {}).get('databaseId') == identity
                 and n.get('mergedAt') and b.dt(n['mergedAt']) < b.dt(as_of)]
        records = []
        tried = set()
        for node in nodes:
            repo = node['repository']['nameWithOwner']
            if repo.casefold() in tried:
                continue
            tried.add(repo.casefold())
            window = dict(github_numeric_id=identity, login=row['developer_key'], repository=repo,
                          as_of=as_of, known_commits=[], identity_evidence=row['seed_identity']['identity_evidence'])
            result = deep.collect(api, window, history_pages=1, include_controls=False)
            for c in result['contributions']:
                c.update(schema_version='advanced-pilot-file-contribution-v0.1', developer_key=c['login'],
                    source_developer_key=row['seed_identity']['developer_key'],
                    file_version_key='history-file:' + b.digest(b.canonical([c['repository'], c['path'], c['commit_sha']])) )
                records.append(c)
            if records or len(tried) == 2:
                break
        result = dict(github_numeric_id=identity, as_of=as_of, contributions=records,
                      discovery_evidence_ref=ref, repositories_tried=sorted(tried))
        b.save('global_history_windows/' + key + '.json', result)
        return result
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(collect_window, item) for item in missing.items()]
        for done, future in enumerate(as_completed(futures), 1):
            contributions.extend(future.result()['contributions'])
            print(json.dumps({'global_history_done': done, 'total': len(missing)}), flush=True)
    unique = {(r['github_numeric_id'], r['as_of'], r['repository'].casefold(), r['commit_sha'], r['path']): r for r in contributions}
    b.lines('historical_contributions.jsonl', list(unique.values()))


def repository_novelty(outcomes, contributions, frozen_repositories, training_cutoffs):
    cutoffs = dict(training_cutoffs)
    repositories = set(frozen_repositories)
    for row in outcomes:
        if row['intended_split'] == 'train':
            repositories.add(row['repository'].casefold())
            cutoff = b.dt(row['profile_as_of'])
            cutoffs[row['github_numeric_id']] = max(cutoffs.get(row['github_numeric_id'], cutoff), cutoff)
    for row in contributions:
        cutoff = cutoffs.get(row['github_numeric_id'])
        if cutoff and b.dt(row['event_at']) < cutoff:
            repositories.add(row['repository'].casefold())
    return [row for row in outcomes if row['intended_split'] == 'test'
            and row['repository'].casefold() not in repositories]


def main():
    global OUT, PRIOR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-network', action='store_true')
    parser.add_argument('--run-id', default='advanced_adapter_targeted_20261104_v1')
    parser.add_argument('--test-cohort', type=Path)
    parser.add_argument('--test-only', action='store_true')
    parser.add_argument('--prior-supplement', type=Path, action='append', default=[])
    parser.add_argument('--test-prs', type=int, default=500)
    parser.add_argument('--global-history-repair', action='store_true')
    parser.add_argument('--targets', type=Path, default=TARGETS)
    parser.add_argument('--train-only', action='store_true')
    parser.add_argument('--train-prs', type=int, default=200)
    parser.add_argument('--include-new-training-developers', action='store_true')
    args = parser.parse_args()
    if args.test_only and args.train_only or not 1 <= args.train_prs <= 1000:
        parser.error('Choose one split policy and 1-1000 training PRs')
    if not args.run_id.replace('_', '').replace('-', '').isalnum() or not 1 <= args.test_prs <= 1000:
        parser.error('Simple run ID and 1-1000 test PRs required')
    OUT = PARENT.parent / args.run_id
    PRIOR = [path.resolve() for path in args.prior_supplement]
    if OUT.resolve() in PRIOR:
        parser.error('Output cannot be its own prior supplement')
    OUT.mkdir(parents=True, exist_ok=True)
    b.OUT = OUT
    selector.b = b
    settings = b.read(PARENT / 'collection_plan.json')
    settings = dict(settings, control_candidates_per_repo_cutoff=5)
    raw = b.read(args.targets)
    targets = dict(sparse_ids=set() if args.test_only else {a['github_numeric_id'] for a in raw['accounts']},
                   training_repositories=set(raw['training_repositories']), allow_test=not args.train_only)
    api = API(args.allow_network)
    state_path = OUT / 'status.json'
    state = b.read(state_path) if state_path.exists() else {'completed': []}
    state['status'] = 'running'
    if 'discover' in state['completed']:
        saved = b.read(OUT / 'collection_plan.json')
        if (saved.get('test_only', False) != args.test_only or saved.get('test_prs', 500) != args.test_prs
                or saved.get('train_only', False) != args.train_only or saved.get('train_prs', 200) != args.train_prs
                or saved.get('targets_sha256', b.digest(TARGETS.read_bytes())) != b.digest(args.targets.read_bytes())
                or saved.get('include_new_training_developers', False) != args.include_new_training_developers
                or saved.get('test_cohort_sha256', b.digest(COHORT.read_bytes())) != b.digest((args.test_cohort or COHORT).read_bytes())
                or saved.get('prior_supplements', {}) != {str(path): b.digest((path / 'manifest.json').read_bytes()) for path in PRIOR}):
            raise ValueError('Resume selection inputs differ from saved plan')
    def checkpoint(stage):
        state['stage'] = stage
        b.save('status.json', state)
        print(json.dumps({'stage': stage, 'completed': state['completed']}), flush=True)
    if 'discover' not in state['completed']:
        checkpoint('discover')
        cohort = b.read(args.test_cohort or COHORT)
        jobs = ([] if args.test_only else [(a, 'train') for a in raw['accounts']]) + ([] if args.train_only else [(a, 'test') for a in cohort['accounts']])
        index, coverage = [], []
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(discover_one, api, a, split) for a, split in jobs]
            for done, future in enumerate(as_completed(futures), 1):
                records, pages = future.result()
                index.extend(records)
                coverage.extend(pages)
                if done % 20 == 0 or done == len(futures):
                    print(json.dumps({'searches_done': done, 'searches_total': len(futures)}), flush=True)
        b.lines('discovery_index.jsonl', index)
        b.save('target_search_coverage.json', coverage)
        existing_selected = b.read(PARENT / 'selected_prs.json')
        prior_urls = {s['url'].casefold() for source in PRIOR for s in b.read(source / 'selected_prs.json')}
        existing_selected += [s for source in PRIOR for s in b.read(source / 'selected_prs.json')]
        seeds = b.read(b.SEEDS)
        train_index = [r for r in index if r['split'] == 'train']
        test_index = [r for r in index if r['split'] == 'test' and r['repository'].casefold() not in targets['training_repositories']]
        if args.test_only:
            selected = []
        elif args.include_new_training_developers:
            low_ids = raw['low_history_account_ids']
            new_ids = raw['new_training_account_ids']
            selected = selector.choose_training_accounts(train_index, existing_selected, seeds, low_ids, min(200, args.train_prs))
            selected += selector.choose_training_accounts(train_index, existing_selected + selected, seeds, new_ids, args.train_prs - len(selected))
        else:
            selected = selector.choose_training_cohort(train_index, existing_selected, seeds, raw['accounts'], args.train_prs)
        if not args.train_only:
            selected += selector.choose(test_index, existing_selected + selected, seeds, args.test_prs, years=(2026,))
        cross = {url.casefold() for url in raw['cross_repo_prs']}
        selected += [s for s in existing_selected if s['url'].casefold() in cross and s['url'].casefold() not in prior_urls
                     and (not args.test_only or s['createdAt'].startswith(('2026-07', '2026-08', '2026-09')))
                     and (not args.train_only or s['year'] in (2024, 2025))]
        b.save('selected_prs.json', list({s['url'].casefold(): s for s in selected}.values()))
        b.save('collection_plan.json', dict(settings, targets=raw,
            parent_manifest_sha256=b.digest((PARENT / 'manifest.json').read_bytes()),
            limitations=settings['limitations'] + ['Cross-repository closing PRs accepted only with actual closing-event evidence.'],
            test_only=args.test_only, test_prs=args.test_prs,
            train_only=args.train_only, train_prs=args.train_prs, targets_sha256=b.digest(args.targets.read_bytes()),
            include_new_training_developers=args.include_new_training_developers,
            test_cohort_sha256=b.digest((args.test_cohort or COHORT).read_bytes()),
            prior_supplements={str(path): b.digest((path / 'manifest.json').read_bytes()) for path in PRIOR}))
        state['completed'].append('discover')
    if 'details' not in state['completed']:
        checkpoint('details')
        b.prefetch_details(api, settings)
        if b.rows(OUT / 'prefetch_failures.jsonl'):
            raise RuntimeError('Unfinished PR detail reads')
        known = {}
        for path in (OUT / 'api_cache').glob('*.json'):
            envelope = b.read(path)
            payload = envelope['payload']
            if isinstance(payload, dict):
                for value in payload.get('data', {}).values():
                    if isinstance(value, dict) and value.get('pullRequest'):
                        known[value['pullRequest']['url'].casefold()] = (value['pullRequest'], path.relative_to(OUT).as_posix())
        existing = {r['outcome_id'] for r in b.rows(PARENT / 'verified_outcomes.jsonl')}
        existing.update(r['outcome_id'] for source in PRIOR for r in b.rows(source / 'verified_outcomes.jsonl'))
        accepted, excluded = {}, []
        for seed in b.read(OUT / 'selected_prs.json'):
            pr, ref = known.get(seed['url'].casefold(), (None, None))
            rows, reasons = outcome_rows(pr, seed, ref, targets, existing)
            accepted.update({r['outcome_id']: r for r in rows})
            excluded.extend(reasons)
        b.lines('verified_outcomes.jsonl', list(accepted.values()))
        b.lines('outcome_exclusions.jsonl', excluded)
        state['completed'].append('details')
        print(json.dumps({'accepted_outcomes': len(accepted)}), flush=True)
    # A valid search can yield no unseen outcomes. Keep an auditable empty
    # package rather than asking downstream history/profile stages to read
    # tables which the no-op controls stage never created.
    if not b.rows(OUT / 'verified_outcomes.jsonl'):
        for name in ('historical_controls.jsonl', 'historical_contributions.jsonl',
                     'history_exclusions.jsonl'):
            if not (OUT / name).exists():
                b.lines(name, [])
    for stage, operation in [('controls', lambda: b.controls(api, settings)),
                             ('history', lambda: b.history(api, settings)),
                             ('repair', lambda: b.history(api, settings, repair=True)),
                             ('import_history', import_history)]:
        if stage not in state['completed']:
            checkpoint(stage)
            operation()
            state['completed'].append(stage)
    checkpoint('report')
    if args.global_history_repair and 'global_history_repair' not in state['completed']:
        checkpoint('global_history_repair')
        repair_global_history(api)
        state['completed'].append('global_history_repair')
        checkpoint('report')
    b.build_profiles()
    outcomes = b.rows(OUT / 'verified_outcomes.jsonl')
    contributions = b.rows(OUT / 'historical_contributions.jsonl')
    training_cutoffs = {}
    for group in b.rows(PARENT.parent / 'temporal_xy_2024_2026_v4/train.jsonl'):
        identity, cutoff = group['github_numeric_id'], b.dt(group['cutoff_at'])
        training_cutoffs[identity] = max(training_cutoffs.get(identity, cutoff), cutoff)
    prior_outcomes = [r for source in PRIOR for r in b.rows(source / 'verified_outcomes.jsonl')]
    prior_contributions = [r for source in PRIOR for r in b.rows(source / 'historical_contributions.jsonl')]
    current_ids = {r['outcome_id'] for r in outcomes}
    strict_test = [r for r in repository_novelty(outcomes + prior_outcomes, contributions + prior_contributions,
                   targets['training_repositories'], training_cutoffs) if r['outcome_id'] in current_ids]
    b.lines('new_repository_test_outcomes.jsonl', strict_test)
    b.lines('training_outcomes.jsonl', [r for r in outcomes if r['intended_split'] == 'train'])
    joined, exclusions = b.join_samples(outcomes, b.rows(OUT / 'historical_controls.jsonl'), b.rows(OUT / 'profile_previews.jsonl'))
    parent_positives = {(r['github_numeric_id'], r['issue_key']) for r in b.rows(PARENT / 'verified_outcomes.jsonl')}
    parent_positives.update((r['github_numeric_id'], r['issue_key']) for r in prior_outcomes)
    for row in joined:
        row['comparison_issue_keys'] = [key for key in row['comparison_issue_keys'] if (row['github_numeric_id'], key) not in parent_positives]
    b.lines('joined_samples.jsonl', [r for r in joined if r['comparison_issue_keys']])
    b.lines('join_exclusions.jsonl', exclusions)
    summary = dict(status='working', training_ready=False, joined_samples=sum(bool(r['comparison_issue_keys']) for r in joined),
        historical_file_contributions=len(contributions), unique_indexed_prs=0, indexed_prs_by_verified_seed_authors=0,
        train_outcomes=sum(r['intended_split'] == 'train' for r in outcomes),
        test_outcomes=sum(r['intended_split'] == 'test' for r in outcomes),
        new_repository_test_outcomes=len(strict_test),
        verified_temporal_outcomes=len(outcomes), selected_prs=len(b.read(OUT / 'selected_prs.json')))
    b.save('summary.json', summary)
    shutil.copy2(Path(__file__), OUT / 'targeted_collector_source.py')
    b.save('manifest.json', dict(script_sha256=b.digest(Path(b.__file__).read_bytes()), B_rule_sha256={},
        files_sha256={p.relative_to(OUT).as_posix(): b.digest(p.read_bytes()) for p in OUT.rglob('*')
                      if p.is_file() and p.name not in ('manifest.json', 'status.json')}))
    verify()
    state['status'] = 'complete'
    checkpoint('verified')
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
