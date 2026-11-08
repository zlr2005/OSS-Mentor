"""Report low-history and unseen-repository coverage for an audited X/Y package."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def main(version):
    base = ROOT / 'data/midterm/working'
    package = base / f'temporal_xy_2024_2026_v{version}'
    source = base / f'merged_ab_history_2024_2026_v{version}'
    cohort_path = ROOT / 'tmp/adapter_sparse_20261104_v1/c_experiment_cohort.json'
    cohort = json.loads(cohort_path.read_text(encoding='utf-8'))['accounts']
    contributions = {tuple(r[k] for k in ('repository', 'commit_sha', 'path')): r
                     for r in rows(source / 'file_contributions.jsonl')}
    outcomes = {r['outcome_id']: r for r in rows(source / 'verified_outcomes.jsonl')}
    groups = {s: rows(package / f'{s}.jsonl') for s in ('train', 'validation', 'test')}
    known_repositories, labels, cutoffs = set(), defaultdict(set), defaultdict(set)
    for group in groups['train']:
        identity = group['github_numeric_id']
        for key in group['x_contribution_keys']:
            known_repositories.add(contributions[tuple(key)]['repository'].casefold())
        for label in group['y']:
            outcome = outcomes[label['outcome_id']]
            known_repositories.add(outcome['repository'].casefold())
            labels[identity].add(label['outcome_id'])
            cutoffs[identity].add(group['cutoff_at'])
    gaps = []
    for account in cohort:
        identity = account['github_numeric_id']
        if 1 <= account['developer_train_outcomes'] <= 2 and len(labels[identity]) < 3:
            gaps.append(dict(github_numeric_id=identity, developer_key=account['developer_key'],
                original_train_outcomes=account['developer_train_outcomes'],
                current_train_outcomes=len(labels[identity]), train_cutoffs=sorted(cutoffs[identity])))
    ready = [r for r in rows(package / 'ranking_test.jsonl')
             if outcomes[r['outcome_id']]['repository'].casefold() not in known_repositories]
    def write(name, records):
        (package / name).write_text(''.join(json.dumps(r, ensure_ascii=False, sort_keys=True)+'\n'
                                           for r in records), encoding='utf-8')
    write('low_history_gaps.jsonl', sorted(gaps, key=lambda r: r['developer_key']))
    write('new_repository_test_ready.jsonl', ready)
    coverage = dict(training_repositories_from_X_and_Y=len(known_repositories),
        new_repository_test_ready=len(ready),
        new_test_repositories=len({outcomes[r['outcome_id']]['repository'].casefold() for r in ready}),
        original_low_history_accounts_under_three=len(gaps),
        developers_with_XY=len({g['github_numeric_id'] for gs in groups.values() for g in gs}),
        novelty_basis='Absent from this version training X/Y repositories; base checkpoint mapping pending C review.',
        cohort_sha256=hashlib.sha256(cohort_path.read_bytes()).hexdigest())
    (package / 'coverage.json').write_text(json.dumps(coverage, indent=2)+'\n', encoding='utf-8')
    manifest_path = package / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['files_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in package.iterdir() if p.is_file() and p.name != 'manifest.json'}
    manifest_path.write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(coverage))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', type=int, required=True)
    main(parser.parse_args().version)
