"""Bounded pre-outcome code and controls for separately collected 2022/23 labels."""
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import json

import backfill_adapter_2024_2026 as b
import deepen_adapter_evidence as deep


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-network',action='store_true')
    parser.add_argument('--source',type=Path)
    parser.add_argument('--run-id',default='earlier_sparse_history_2022_2023_v1')
    args = parser.parse_args()
    source = args.source.resolve() if args.source else b.OUT.parent/'earlier_sparse_outcomes_2022_2023_v1'
    if not source.is_relative_to(b.ROOT) or not args.run_id.replace('_','').isalnum():
        parser.error('Source must stay in project; simple run ID required')
    outcomes = b.rows(source/'verified_outcomes.jsonl')
    groups = defaultdict(list)
    for row in outcomes:
        groups[row['github_numeric_id']].append(row)
    windows = []
    for identity, rows in sorted(groups.items()):
        by_cutoff = {}
        for row in sorted(rows,key=lambda r:(r['profile_as_of'],r['repository'])):
            by_cutoff.setdefault(row['profile_as_of'],row)
        for cutoff,row in list(sorted(by_cutoff.items()))[-3:]:
            windows.append(dict(github_numeric_id=identity, login=row['developer_key'],
                repository=row['repository'],as_of=cutoff,known_commits=[],
                identity_evidence=row['seed_identity']['identity_evidence']))
    history_start=f'{min((b.dt(w["as_of"]).year for w in windows),default=2022)-1}-01-01T00:00:00Z'
    b.OUT = deep.OUT = source.parent/args.run_id
    b.OUT.mkdir(exist_ok=True)
    b.save('collection_plan.json',dict(source_root=source.relative_to(b.ROOT).as_posix(),
        source_manifest_sha256=b.digest((source/'manifest.json').read_bytes()),
        history_start=history_start,max_cutoffs_per_account=3,
        max_commits_per_window=1,windows=windows,training_ready=False))
    api = b.API(args.allow_network)
    def collect(window):
        key = b.digest(b.canonical(window))
        path = b.OUT/'windows'/f'{key}.json'
        if path.exists():
            return b.read(path)
        result = deep.collect(api,window,history_pages=1,include_controls=True,
                              history_start=history_start,commit_page_size=1)
        b.save(f'windows/{key}.json',result)
        return result
    results, failures = [], []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(collect,w):w for w in windows}
        for done,future in enumerate(as_completed(futures),1):
            try:
                results.append(future.result())
            except Exception as exc:
                failures.append(dict(window=futures[future],reason=type(exc).__name__))
            if done%10==0 or done==len(windows):
                print(json.dumps(dict(done=done,total=len(windows),failed=len(failures))),flush=True)
    contributions = [c for r in results for c in r['contributions']]
    controls = {(c['issue_key'],c['as_of']):c for r in results for c in r['controls']}
    comparisons, available = [], {(c['github_numeric_id'],c['as_of']) for c in contributions}
    positives = {(r['github_numeric_id'],r['issue_key']) for r in outcomes}
    for row in outcomes:
        keys = [c['issue_key'] for c in controls.values() if c['repository']==row['repository']
                and c['as_of']==row['profile_as_of'] and (row['github_numeric_id'],c['issue_key']) not in positives]
        if keys and (row['github_numeric_id'],row['profile_as_of']) in available:
            comparisons.append(dict(outcome_id=row['outcome_id'],github_numeric_id=row['github_numeric_id'],
                as_of=row['profile_as_of'],repository=row['repository'],comparison_issue_keys=keys,
                outcome_source_root=source.relative_to(b.ROOT).as_posix(),training_ready=False,
                profile_state='not_built_pending_B_review'))
    b.lines('historical_contributions.jsonl',contributions)
    b.lines('historical_controls.jsonl',list(controls.values()))
    b.lines('comparison_groups.jsonl',comparisons)
    b.lines('failures.jsonl',failures)
    b.lines('exclusions.jsonl',[e for r in results for e in r['exclusions']])
    summary = dict(windows=len(windows),successful_windows=len(results),failed_windows=len(failures),
        window_file_contributions=len(contributions),historical_candidate_snapshots=len(controls),
        outcomes_with_history_and_controls=len(comparisons),training_ready=False,
        note='At most 3 cutoffs/account and 1 sampled commit/window; not full history. B/C review required.')
    b.save('summary.json',summary)
    b.save('manifest.json',dict(collector_sha256=b.digest(Path(__file__).read_bytes()),
        history_collector_sha256=b.digest(Path(deep.__file__).read_bytes()),
        files_sha256={p.relative_to(b.OUT).as_posix():b.digest(p.read_bytes())
                      for p in b.OUT.rglob('*') if p.is_file() and p.name!='manifest.json'}))
    deep.verify()
    print(json.dumps(summary),flush=True)


if __name__ == '__main__':
    main()
