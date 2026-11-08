"""有界采集精确 commit 的关联 PR；关联本身不是历史公开证明。"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess


def query(targets):
    fields = []
    for i, t in enumerate(targets):
        if not re.fullmatch(r'[\w.-]+/[\w.-]+', t['repository']) or not re.fullmatch('[0-9a-f]{40}', t['commit']):
            raise ValueError('Invalid target')
        owner, name = t['repository'].split('/')
        fields.append('q%d: repository(owner:%s,name:%s) { nameWithOwner object(oid:%s) { ... on Commit { oid associatedPullRequests(first:20) { pageInfo { hasNextPage } nodes { number url createdAt mergedAt mergeCommit { oid } baseRepository { nameWithOwner } } } } } }' % (i, json.dumps(owner), json.dumps(name), json.dumps(t['commit'])))
    return 'query { rateLimit { remaining resetAt } ' + ' '.join(fields) + ' }'


def run(checks_path, output):
    checks = json.loads(checks_path.read_text(encoding='utf-8'))
    targets = {}
    for c in checks:
        if c['publication_passed']:
            continue
        key = c['repository'], c['commit']
        targets.setdefault(key, dict(repository=key[0], commit=key[1], validation=False))
        targets[key]['validation'] |= c['split'] == 'validation'
    ordered = sorted(targets.values(), key=lambda t: (not t['validation'], t['repository'], t['commit']))
    output.mkdir(parents=True, exist_ok=True)
    batches = [ordered[i:i+8] for i in range(0, len(ordered), 8)]
    plan = dict(targets=len(ordered), requests=len(batches), batch_size=8, concurrency=3,
                source_sha256=hashlib.sha256(checks_path.read_bytes()).hexdigest(), read_only=True)
    (output / 'plan.json').write_text(json.dumps(plan, indent=2))
    def fetch(item):
        i, batch = item
        path = output / f'links_{i:03d}.json'
        if path.exists():
            record = json.loads(path.read_text())
            if record['targets'] != batch:
                raise ValueError('Cached targets changed')
            if record['read_status'] == 'passed':
                return record
        q = query(batch)
        try:
            r = subprocess.run(['gh', 'api', 'graphql', '-f', 'query=' + q], capture_output=True, timeout=45)
            payload = json.loads(r.stdout) if r.stdout else {}
            status = 'passed' if r.returncode == 0 and not payload.get('errors') else 'partial' if payload.get('data') else 'failed'
        except (OSError, ValueError, subprocess.TimeoutExpired):
            payload, status = {}, 'failed'
        record = dict(targets=batch, query=q, payload=payload, read_status=status,
                      observed_at=datetime.now(timezone.utc).isoformat())
        path.write_text(json.dumps(record, indent=2), encoding='utf-8')
        if i % 10 == 0 or status != 'passed':
            print(json.dumps(dict(batch=i, status=status)), flush=True)
        return record
    with ThreadPoolExecutor(max_workers=3) as pool:
        records = list(pool.map(fetch, enumerate(batches)))
    summary = dict(plan, passed=sum(r['read_status'] == 'passed' for r in records),
                   partial=sum(r['read_status'] == 'partial' for r in records),
                   failed=sum(r['read_status'] == 'failed' for r in records))
    (output / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--checks', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    run(a.checks, a.output)
