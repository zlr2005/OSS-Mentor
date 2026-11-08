"""Build chronological X/Y groups from the merged working dataset."""
import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'data/midterm/working/merged_ab_history_2024_2026_v1'
OUT = SOURCE.parent / 'temporal_xy_2024_2026_v1'
VALIDATION_START = '2026-01-01T00:00:00Z'
TEST_START = '2026-07-01T00:00:00Z'


def rows(name):
    with (SOURCE / name).open(encoding='utf-8') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def dt(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def main():
    if OUT.exists():
        raise FileExistsError(OUT)
    history = defaultdict(list)
    for row in rows('file_contributions.jsonl'):
        account = row['github_numeric_id']
        if account is not None and row['content_available'] and row['event_at']:
            history[account].append((dt(row['event_at']), [row['repository'], row['commit_sha'], row['path']]))
    for records in history.values():
        records.sort()
    dates = {account: [event for event, _ in records] for account, records in history.items()}
    groups = {}
    exclusions = Counter()
    for outcome in rows('verified_outcomes.jsonl'):
        account = outcome['github_numeric_id']
        cutoff = dt(outcome['profile_as_of'])
        interaction = dt(outcome['interaction_at'])
        closed = dt(outcome['outcome_at'])
        if outcome['verification_status'] != 'verified' or not cutoff < interaction <= closed <= cutoff + timedelta(days=90):
            raise ValueError(f"Unverified or out-of-window outcome {outcome['outcome_id']}")
        if cutoff < dt(VALIDATION_START):
            split, end = 'train', dt(VALIDATION_START)
        elif cutoff < dt(TEST_START):
            split, end = 'validation', dt(TEST_START)
        else:
            split, end = 'test', None
        if end is not None and closed >= end:
            exclusions['label_crosses_split_boundary'] += 1
            continue
        records = history.get(account, [])
        eligible = bisect_left(dates.get(account, []), cutoff)
        if eligible == 0:
            exclusions['no_pre_cutoff_available_code'] += 1
            continue
        group = groups.setdefault((account, cutoff), dict(
            github_numeric_id=account, cutoff_at=outcome['profile_as_of'], split=split,
            x_contribution_keys=[key for _, key in records[:eligible]], y=[]))
        group['y'].append({key: outcome[key] for key in
                           ('issue_key', 'outcome_id', 'interaction_at', 'outcome_at', 'pr_url')})
    OUT.mkdir()
    stats = {}
    for split in ('train', 'validation', 'test'):
        selected = [group for _, group in sorted(groups.items()) if group['split'] == split]
        with (OUT / f'{split}.jsonl').open('w', encoding='utf-8', newline='\n') as stream:
            for group in selected:
                group['y'].sort(key=lambda label: (label['outcome_at'], label['outcome_id']))
                stream.write(json.dumps(group, ensure_ascii=False, sort_keys=True) + '\n')
        stats[split] = dict(groups=len(selected), developers=len({g['github_numeric_id'] for g in selected}),
                            labels=sum(len(g['y']) for g in selected))
    summary = dict(status='working', training_ready=False, splits=stats, exclusions=dict(exclusions),
                   validation_start=VALIDATION_START, test_start=TEST_START,
                   source=SOURCE.relative_to(ROOT).as_posix(),
                   source_manifest_sha256=hashlib.sha256((SOURCE/'manifest.json').read_bytes()).hexdigest(),
                   x_time_basis='Commit event timestamp. First public availability was not independently verified.',
                   y_definition='Verified Issue closing outcomes within 90 days after the developer cutoff.',
                   evaluation='Forward-time split. Developers may recur across splits.',
                   test_observation='Positive outcomes observed to collection date. 2026 Q4 has an incomplete follow-up window.')
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', type=int, choices=range(1, 100), default=1)
    args = parser.parse_args()
    SOURCE = SOURCE.with_name(f'merged_ab_history_2024_2026_v{args.version}')
    OUT = OUT.with_name(f'temporal_xy_2024_2026_v{args.version}')
    main()
