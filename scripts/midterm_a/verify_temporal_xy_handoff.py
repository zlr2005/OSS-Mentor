"""核验时序 X/Y 和候选联接，导出 B/C 工作包入口与逐集排序样本。"""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dt(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('Naive timestamp')
    return result


def write_rows(path, values):
    path.write_text(''.join(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n' for row in values), encoding='utf-8')


def main(version):
    base = ROOT / 'data/midterm/working'
    source = base / f'merged_ab_history_2024_2026_v{version}'
    output = base / f'temporal_xy_2024_2026_v{version}'
    manifest = read(source / 'manifest.json')
    for name, expected in manifest['files_sha256'].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source.resolve()) or digest(path) != expected:
            raise ValueError('Merged table checksum mismatch')
    for name, relative in manifest['source_roots'].items():
        if digest(ROOT / relative / 'manifest.json') != manifest['source_manifest_sha256'][name]:
            raise ValueError('Source manifest changed')
    contributions = {(r['repository'], r['commit_sha'], r['path']): r for r in rows(source / 'file_contributions.jsonl')}
    outcomes = {r['outcome_id']: r for r in rows(source / 'verified_outcomes.jsonl')}
    candidates = {(r['issue_key'], r['as_of']): r for r in rows(source / 'historical_controls.jsonl')}
    comparisons = {r['outcome_id']: r for r in rows(source / 'comparison_groups.jsonl')}
    positives = {}
    for row in outcomes.values():
        positives.setdefault(row['github_numeric_id'], set()).add(row['issue_key'])
    seen_groups, seen_outcomes = set(), set()
    audit = {}
    for split in ('train', 'validation', 'test'):
        groups = rows(output / f'{split}.jsonl')
        rankings, missing = [], 0
        unique_labels = set()
        for group in groups:
            identity, cutoff = group['github_numeric_id'], dt(group['cutoff_at'])
            key = identity, cutoff
            if key in seen_groups or group['split'] != split:
                raise ValueError('Duplicate or mismatched temporal group')
            seen_groups.add(key)
            if len({tuple(k) for k in group['x_contribution_keys']}) != len(group['x_contribution_keys']):
                raise ValueError('Repeated X contribution')
            for triple in group['x_contribution_keys']:
                row = contributions[tuple(triple)]
                if row['github_numeric_id'] != identity or not row['content_available'] or dt(row['event_at']) >= cutoff:
                    raise ValueError('X identity/time/content mismatch')
            for label in group['y']:
                row = outcomes[label['outcome_id']]
                if label['outcome_id'] in seen_outcomes:
                    raise ValueError('Outcome repeated across groups')
                seen_outcomes.add(label['outcome_id'])
                if row['github_numeric_id'] != identity or dt(row['profile_as_of']) != cutoff or not cutoff < dt(row['interaction_at']) <= dt(row['outcome_at']):
                    raise ValueError('Y identity or temporal mismatch')
                if (split == 'train' and dt(row['outcome_at']) >= dt('2026-01-01T00:00:00Z')) or (split == 'validation' and dt(row['outcome_at']) >= dt('2026-07-01T00:00:00Z')):
                    raise ValueError('Label crosses split boundary')
                unique_labels.add((identity, cutoff, row['issue_key']))
                comparison = comparisons[row['outcome_id']]
                for issue_key in comparison['comparison_issue_keys']:
                    candidate = candidates[issue_key, row['profile_as_of']]
                    if candidate['repository'].casefold() != row['repository'].casefold() or issue_key in positives[identity]:
                        raise ValueError('Cross-repository or positive comparison')
                if comparison['comparison_issue_keys']:
                    rankings.append(dict(comparison, split=split, outcome_at=row['outcome_at']))
                else:
                    missing += 1
        write_rows(output / f'ranking_{split}.jsonl', rankings)
        audit[split] = dict(groups=len(groups), outcome_labels=sum(len(g['y']) for g in groups),
                            unique_issue_labels=len(unique_labels), ranking_outcomes=len(rankings),
                            outcomes_without_controls=missing,
                            same_repository_pairs=sum(len(r['comparison_issue_keys']) for r in rankings),
                            outcomes_with_five_controls=sum(len(r['comparison_issue_keys']) >= 5 for r in rankings))
    shutil.copyfile(source / 'file_contributions.jsonl', output / 'file_contributions.jsonl')
    shutil.copyfile(source / 'manifest.json', output / 'source_manifest.json')
    audit_path = output / 'audit.json'
    audit_path.write_text(json.dumps(dict(splits=audit, training_ready=False,
        label_policy='Retain all outcome IDs; C should group repeated Issue keys as one relevant Issue.',
        profile_policy='Regenerate B profiles from this X version before freezing.'), ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    package = {'version': version, 'source_manifest_sha256': digest(source / 'manifest.json'),
               'files_sha256': {p.name: digest(p) for p in output.iterdir() if p.is_file() and p.name != 'manifest.json'}}
    (output / 'manifest.json').write_text(json.dumps(package, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(audit))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', type=int, required=True)
    main(parser.parse_args().version)
