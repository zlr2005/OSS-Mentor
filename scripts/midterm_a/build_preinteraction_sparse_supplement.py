"""Re-audit cached PRs at pre-interaction cutoffs in an independent dataset."""
from collections import defaultdict
import json
from pathlib import Path

import collect_targeted_adapter as t


def main():
    base = t.PARENT.parent
    roots = [t.PARENT]+[base/f'advanced_adapter_targeted_20261104_v{i}' for i in (1,2,3,4,6)]
    roots += [base/'advanced_adapter_expanded_20261104_v1',base/'earlier_sparse_outcomes_2022_2023_v1']
    cohort = t.b.rows(base/'temporal_xy_2024_2026_v6/low_history_gaps.jsonl')
    ids = {r['github_numeric_id'] for r in cohort}
    official = t.b.rows(base/'merged_ab_history_2024_2026_v6/verified_outcomes.jsonl')
    existing = {r['outcome_id'] for r in official}
    existing_issues = {(r['github_numeric_id'],r['issue_key']) for r in official}
    seeds = {}
    for root in roots:
        for seed in t.b.read(root/'selected_prs.json'):
            if seed['author']['databaseId'] in ids and t.b.quarter(seed['createdAt']).year in (2022,2023,2024,2025):
                seeds[seed['url']] = seed
    targets = dict(sparse_ids=ids,training_years=(2022,2023,2024,2025),training_repositories=set(),
                   allow_test=False,profile_cutoff_policy='pre_interaction')
    candidates, exclusions, seen = defaultdict(list), [], set()
    for root in roots:
        detail_paths = set()
        for request in (root/'requests').glob('*.json'):
            query = t.b.read(request).get('query','')
            if 'pullRequest(number:' in query:
                cached = root/'api_cache'/request.name
                if cached.exists():
                    detail_paths.add(cached)
        # Include imported evidence whose request file remains in its source.
        for row in t.b.rows(root/'verified_outcomes.jsonl'):
            if row.get('evidence_ref'):
                detail_paths.add(root/row['evidence_ref'])
        for path in sorted(detail_paths):
            payload = t.b.read(path)['payload']
            if not isinstance(payload,dict):
                continue
            for value in payload.get('data',{}).values():
                pr = value.get('pullRequest') if isinstance(value,dict) else None
                if not pr or pr['url'] not in seeds or pr['url'] in seen:
                    continue
                seen.add(pr['url'])
                rows,reasons = t.outcome_rows(pr,seeds[pr['url']],path.relative_to(root).as_posix(),targets,existing)
                exclusions.extend(dict(r,evidence_source_root=root.relative_to(t.b.ROOT).as_posix()) for r in reasons)
                for row in rows:
                    row.pop('new_repository_basis',None)
                    row.update(evidence_source_root=root.relative_to(t.b.ROOT).as_posix(),
                        intended_split='preinteraction_training_pending_contract_review',training_ready=False,
                        cutoff_policy='one_second_before_PR_created_at')
                    candidates[row['github_numeric_id']].append(row)
        print(json.dumps(dict(source_done=root.name,candidate_outcomes=sum(map(len,candidates.values())))),flush=True)
    selected, account_report = [], []
    for account in cohort:
        identity = account['github_numeric_id']
        # Prefer recent training years and distinct interaction times. No test outcomes.
        ordered = sorted(candidates[identity],key=lambda r:(-r['year'],r['profile_as_of'],r['outcome_id']))
        rows, issue_keys = [], set()
        for row in ordered:
            if (identity,row['issue_key']) in existing_issues:
                continue
            if row['issue_key'] in issue_keys:
                continue
            issue_keys.add(row['issue_key'])
            rows.append(row)
            if len(rows)>=5-account['current_train_outcomes']:
                break
        selected.extend(rows)
        account_report.append(dict(account,new_outcomes=len(rows),
            new_cutoffs=sorted({r['profile_as_of'] for r in rows}),
            total_if_admitted=account['current_train_outcomes']+len(rows),
            missing_before_three=max(0,3-account['current_train_outcomes']-len(rows))))
    t.b.OUT = base/'preinteraction_sparse_supplement_20261104_v1'
    t.b.OUT.mkdir(exist_ok=True)
    t.b.lines('verified_outcomes.jsonl',selected)
    t.b.lines('account_coverage.jsonl',account_report)
    t.b.lines('outcome_exclusions.jsonl',exclusions)
    t.b.save('source_manifest.json',dict(source_roots={r.name:r.relative_to(t.b.ROOT).as_posix() for r in roots},
        source_manifest_sha256={r.name:t.b.digest((r/'manifest.json').read_bytes()) for r in roots}))
    summary = dict(new_verified_outcomes=len(selected),covered_accounts=sum(bool(r['new_outcomes']) for r in account_report),
        accounts_reaching_three_if_admitted=sum(not r['missing_before_three'] for r in account_report),
        remaining_under_three_if_admitted=sum(bool(r['missing_before_three']) for r in account_report),
        new_outcomes_from_2024_2025=sum(r['year']>=2024 for r in selected),training_ready=False,
        note='Different cutoff policy. Requires new pre-cutoff history, B frozen profiles, and C contract audit. Do not add overlapping earlier outcomes twice.')
    t.b.save('summary.json',summary)
    t.b.save('manifest.json',dict(collector_sha256=t.b.digest(Path(__file__).read_bytes()),
        targeted_collector_sha256=t.b.digest(Path(t.__file__).read_bytes()),
        files_sha256={p.name:t.b.digest(p.read_bytes()) for p in t.b.OUT.iterdir() if p.is_file() and p.name!='manifest.json'}))
    print(json.dumps(summary),flush=True)


if __name__ == '__main__':
    main()
