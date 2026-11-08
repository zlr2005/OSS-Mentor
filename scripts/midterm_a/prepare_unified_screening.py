"""Offline union of teacher discovery batches, with identity conflict quarantine."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re


def prepare(root, output):
    if output.exists():
        raise FileExistsError('Use a new screening directory')
    rows, inputs = {}, []
    for directory in sorted(root.iterdir()):
        if not directory.is_dir() or not re.fullmatch(r'(?:expanded|round\d+)_\d{8}T\d{6}Z', directory.name):
            continue
        path = directory/'issues_discovered.json'
        if not path.is_file():
            continue
        status = json.loads((directory/'status.json').read_text(encoding='utf-8-sig'))
        if status['status'] not in {'finished_unverified', 'stopped_unverified', 'paused_budget_unverified'}:
            raise ValueError('Collection still active: ' + directory.name)
        raw = path.read_bytes()
        source = json.loads(raw.decode('utf-8-sig'))
        inputs.append(dict(batch=directory.name, path=str(path), sha256=hashlib.sha256(raw).hexdigest(),
                           rows=len(source), failed=status.get('failed_batches'), saturated=status.get('saturated_batches'),
                           status=status['status'],pending_batches=status.get('pending_batches'),
                           processed_batches=status.get('processed_batches'),observed_at=status.get('observed_at')))
        for item in source:
            if item.get('platform') != 'GitHub' or not re.fullmatch(r'[\w.-]+/[\w.-]+', item['repo_name']) or int(item['issue_number']) <= 0:
                raise ValueError('Invalid teacher discovery identity')
            key = (int(item['repo_id']), int(item['issue_number']))
            if key not in rows:
                rows[key] = dict(item, source_batches=[], observed_issue_ids=[], observed_repository_names=[])
            merged = rows[key]
            merged['source_batches'].append(directory.name)
            merged['observed_issue_ids'] = sorted(set(merged['observed_issue_ids']) | {str(item['issue_id'])})
            merged['observed_repository_names'] = sorted(set(merged['observed_repository_names']) | {item['repo_name']})
    records = sorted(rows.values(), key=lambda r:(r['repo_name'].casefold(), int(r['issue_number'])))
    conflicts = [r for r in records if len(r['observed_issue_ids']) != 1 or len({n.casefold() for n in r['observed_repository_names']}) != 1]
    name_keys = [(r['repo_name'].casefold(), int(r['issue_number'])) for r in records]
    if len(name_keys) != len(set(name_keys)):
        raise ValueError('Repository name maps to conflicting repository IDs')
    output.mkdir(parents=True)
    def dump(name, value):
        (output/name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    dump('source_issues.json', records)
    dump('source_identity_conflicts.json', conflicts)
    dump('source_manifest.json', inputs)
    summary = dict(prepared_at=datetime.now(timezone.utc).isoformat(), source='teacher_clickhouse_only',
                   batches=len(inputs), distinct_issues=len(records), identity_conflicts=len(conflicts),
                   repositories=dict(Counter(r['repo_name'] for r in records)), frozen=False, remote_requests=0)
    dump('corpus_summary.json', summary)
    return summary


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    print(json.dumps(prepare(**vars(p.parse_args())), ensure_ascii=False))
