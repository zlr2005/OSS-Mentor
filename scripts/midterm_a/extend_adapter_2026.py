"""独立三年工作版本：保留旧批次，追加 2026 季度检索和三年均衡核验。"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
import json
from pathlib import Path
import shutil

import backfill_adapter_2024_2025 as old
import backfill_adapter_2024_2026 as b
from continue_adapter_backfill import choose


def jobs(settings, end):
    stop = date.fromisoformat(end)
    if stop.year != 2026:
        raise ValueError('2026 observation date required')
    ranges = [('2026-01-01', '2026-03-31'), ('2026-04-01', '2026-06-30'),
              ('2026-07-01', '2026-09-30'), ('2026-10-01', '2026-12-31')]
    return [(r['name'], start, min(finish, end)) for r in settings['repositories']
            for start, finish in ranges if start <= end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-network', action='store_true')
    args = parser.parse_args()
    run = b.ROOT / 'tmp/adapter_extend_20261103_v1'
    run.mkdir(parents=True, exist_ok=True)
    state_path = run / 'status.json'
    state = b.read(state_path) if state_path.exists() else {'completed': []}

    def checkpoint(stage):
        state.update(stage=stage, updated_at=b.iso(b.datetime.now(b.timezone.utc)))
        temp = state_path.with_suffix('.tmp')
        temp.write_bytes(b.canonical(state))
        temp.replace(state_path)
        print(json.dumps(state), flush=True)

    if 'prepare' not in state['completed']:
        old.verify()
        checkpoint('prepare')
        # Full copies keep all earlier mutable outputs and provenance independent.
        shutil.copytree(old.OUT, b.OUT, dirs_exist_ok=True)
        settings = b.read(b.OUT / 'collection_plan.json')
        settings['parent_plan_sha256'] = b.digest(b.canonical(settings))
        settings['years'] = [2024, 2025, 2026]
        settings['limitations'] = [x for x in settings['limitations'] if 'No 2026' not in x]
        settings['limitations'].append('2026 positive outcomes observed through collection; Q4 is right censored, no unobserved outcomes used as negatives.')
        settings['observation_end_date'] = '2026-11-02'
        b.save('collection_plan.json', settings)
        shutil.copy2(old.ROOT / 'scripts/midterm_a/backfill_adapter_2024_2025.py', b.OUT / 'parent_collector_source.py')
        shutil.copy2(Path(__file__), b.OUT / 'extension_source.py')
        state['completed'].append('prepare')
        checkpoint('prepared')
    settings = b.read(b.OUT / 'collection_plan.json')
    b.SEEDS = b.OUT / 'verified_seed_identities.json'
    if b.digest(b.SEEDS.read_bytes()) != settings['source_seed_sha256']:
        raise ValueError('Copied seed cohort changed')
    api = b.API(args.allow_network)
    if 'discover' not in state['completed']:
        checkpoint('discover')
        prepared = jobs(settings, settings['observation_end_date'])
        chunks = [prepared[i:i+4] for i in range(0, len(prepared), 4)]

        def collect(chunk):
            fields = []
            for i, (repo, start, end) in enumerate(chunk):
                q = f'repo:{repo} is:pr is:merged linked:issue created:{start}..{end} merged:2026-01-01..{settings["observation_end_date"]} sort:created-asc'
                fields.append(f'q{i}: search(query:{json.dumps(q)},type:ISSUE,first:100) {{ issueCount {b.PAGE} nodes {{ ... on PullRequest {{ number url createdAt mergedAt {b.AUTHOR} }} }} }}')
            payload, ref = api.call(query='query {\n' + '\n'.join(fields) + '\n}')
            return [{'repository': repo, 'year': 2026, 'start': start, 'end': end,
                     'returned': len(payload['data'][f'q{i}']['nodes']),
                     'total_matches': payload['data'][f'q{i}']['issueCount'],
                     'search_truncated': payload['data'][f'q{i}']['pageInfo']['hasNextPage'],
                     'continuation_cursor': payload['data'][f'q{i}']['pageInfo']['endCursor'],
                     'evidence_ref': ref} for i, (repo, start, end) in enumerate(chunk)]

        coverage = []
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(collect, chunk) for chunk in chunks]
            for done, future in enumerate(as_completed(futures), 1):
                coverage.extend(future.result())
                b.save('extended_2026_search_coverage.json', sorted(coverage, key=lambda r: (r['repository'], r['start'])))
                if done % 10 == 0 or done == len(chunks):
                    print(json.dumps({'stage': '2026_discover', 'done': done, 'total': len(chunks)}), flush=True)
        b.build_pr_index()
        state['completed'].append('discover')
        checkpoint('discovered')
    if 'select' not in state['completed']:
        selected = b.read(b.OUT / 'selected_prs.json')
        index = b.rows(b.OUT / 'verified_seed_pr_index.jsonl')
        seeds = b.read(b.SEEDS)
        added = []
        for year in settings['years']:
            added.extend(choose(index, selected + added, seeds, 400, years=(year,)))
        b.save('three_year_selection.json', {'parent_selected_count': len(selected),
            'added': added, 'selection': 'Up to 400 additional verified-seed PRs per year, rotating quarter/repository/author.'})
        b.save('selected_prs.json', selected + added)
        state['completed'].append('select')
        checkpoint('selected')
    stages = [('prefetch', lambda: b.prefetch_details(api, settings)),
              ('details', lambda: b.details(api, settings)), ('controls', lambda: b.controls(api, settings)),
              ('history', lambda: b.history(api, settings)),
              ('repair', lambda: b.history(api, settings, repair=True)),
              ('report', lambda: (b.build_profiles(), b.report(settings))), ('verify', b.verify)]
    try:
        for stage, operation in stages:
            if stage in state['completed']:
                continue
            checkpoint(stage)
            operation()
            if stage == 'prefetch' and b.rows(b.OUT / 'prefetch_failures.jsonl'):
                raise RuntimeError('Incomplete PR reads; resume cached stage')
            state['completed'].append(stage)
        state['status'] = 'complete'
        checkpoint('complete')
    except Exception as exc:
        state['status'] = 'failed'
        state['error_type'] = type(exc).__name__
        checkpoint(stage)
        raise


if __name__ == '__main__':
    main()
