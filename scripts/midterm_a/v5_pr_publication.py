"""精确 merge SHA/时间与独立公开 PR 关闭事件复合核验。"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
from audit_v5_publication import load, time


def links_from(directory):
    result = defaultdict(list)
    for path in sorted(directory.glob('links_*.json')):
        record = load(path)
        for i, target in enumerate(record['targets']):
            data = (record.get('payload', {}).get('data') or {}).get(f'q{i}') or {}
            obj = data.get('object') or {}
            if obj.get('oid') != target['commit']:
                continue
            for pr in (obj.get('associatedPullRequests') or {}).get('nodes', []):
                repo = (pr.get('baseRepository') or {}).get('nameWithOwner', '').casefold()
                if repo != target['repository'].casefold():
                    continue
                if (pr.get('mergeCommit') or {}).get('oid') != target['commit'] or not pr.get('mergedAt'):
                    continue
                result[repo, target['commit']].append(dict(pr=pr, source=str(path),
                    source_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    return result


def qualifies(check, link, event):
    pr = link['pr']
    repo = check['repository'].casefold()
    return (repo == event['repo_name'].casefold()
            and repo == (pr.get('baseRepository') or {}).get('nameWithOwner', '').casefold()
            and pr['number'] == event['number']
            and (pr.get('mergeCommit') or {}).get('oid') == check['commit']
            and event['action'] in ('closed', 'merged')
            and bool(pr.get('mergedAt'))
            and time(pr['mergedAt']) <= time(event['event_at']) < time(check['cutoff']))


def prepare(checks_path, links_dir, output):
    checks = load(checks_path)
    links = links_from(links_dir)
    targets = {}
    for c in checks:
        if c['publication_passed']:
            continue
        for link in links[c['repository'], c['commit']]:
            pr = link['pr']
            if time(pr['mergedAt']) < time(c['cutoff']):
                targets[c['repository'], pr['number'], pr['mergedAt']] = dict(repository=c['repository'], number=pr['number'], merged_at=pr['mergedAt'])
    ordered = sorted(targets.values(), key=lambda t: (t['repository'], t['number'], t['merged_at']))
    output.mkdir(parents=True, exist_ok=False)
    queries = []
    for start in range(0, len(ordered), 20):
        conditions = []
        for t in ordered[start:start+20]:
            if not re.fullmatch(r'[\w.-]+/[\w.-]+', t['repository']) or not isinstance(t['number'], int) or t['number'] <= 0:
                raise ValueError('Invalid PR')
            date = time(t['merged_at']).strftime('%Y-%m-%d %H:%M:%S')
            conditions.append(f"(lower(repo_name)='{t['repository']}' AND number={t['number']} AND created_at>=toDateTime('{date}','UTC'))")
        sql = "SELECT repo_name,number,action,toString(min(created_at),'UTC') AS event_at FROM github_events WHERE event_type='PullRequestEvent' AND action IN ('closed','merged') AND (" + ' OR '.join(conditions) + ") GROUP BY repo_name,number,action LIMIT 200 FORMAT JSON"
        queries.append(dict(name=f'batch_{start//20:03d}', sql=sql))
    (output / 'query_queue.json').write_text(json.dumps(queries, indent=2))
    (output / 'targets.json').write_text(json.dumps(ordered, indent=2))
    print(json.dumps(dict(pr_targets=len(ordered), queries=len(queries))))


def audit(checks_path, links_dir, events_dir, output):
    checks = load(checks_path)
    links = links_from(links_dir)
    events = defaultdict(list)
    for path in events_dir.glob('batch_*.json'):
        for e in load(path)['data']:
            events[e['repo_name'].casefold(), e['number']].append(dict(event=e, source=str(path), source_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    added = 0
    for c in checks:
        if c['publication_passed']:
            continue
        for link in links[c['repository'], c['commit']]:
            for event in events[c['repository'], link['pr']['number']]:
                if qualifies(c, link, event['event']):
                    c['proofs'].append(dict(method='exact_merge_commit_plus_public_post_merge_event', metadata=link, archive=event))
        if c['proofs']:
            c['publication_passed'] = True
            added += 1
    by_profile = defaultdict(list)
    splits = {}
    for c in checks:
        by_profile[c['profile_version']].append(c['publication_passed'])
        splits[c['profile_version']] = c['split']
    summary = dict(references=len(checks), added_composite_proofs=added,
                   passed=sum(c['publication_passed'] for c in checks),
                   unresolved_commits=len({(c['repository'], c['commit']) for c in checks if not c['publication_passed']}),
                   interpretation='Composite historical inference, not contemporaneous blob snapshot',
                   test_scored=False, external_results_read=False)
    summary['profile_coverage'] = {s: dict(total=sum(v == s for v in splits.values()),
        any_proven=sum(splits[k] == s and any(v) for k, v in by_profile.items()),
        all_proven=sum(splits[k] == s and all(v) for k, v in by_profile.items())) for s in ('train', 'validation')}
    output.mkdir(parents=True, exist_ok=False)
    (output / 'checks.json').write_text(json.dumps(checks, indent=2), encoding='utf-8')
    (output / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['prepare', 'audit'])
    for n in ('checks', 'links', 'output'):
        p.add_argument('--'+n, type=Path, required=True)
    p.add_argument('--events', type=Path)
    a = p.parse_args()
    if a.mode == 'prepare':
        prepare(a.checks, a.links, a.output)
    else:
        audit(a.checks, a.links, a.events, a.output)
