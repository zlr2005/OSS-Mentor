"""核对 v5 精确 commit 的历史公开事件；不读取评价标签或排名。"""
import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re


def source_identity(source):
    payload=source.get('payload')
    if payload is not None:
        url=payload.get('url','')
        canonical=(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode()
        if hashlib.sha256(canonical).hexdigest()!=source.get('payload_sha256'):
            raise ValueError('Evidence payload hash mismatch')
    elif source.get('schema_version')=='b-sanitized-source-projection-v1' and source.get('status_code')==200:
        payload=source['fields'];url=source['source_url'].split('?',1)[0]
        # Projection is hash-recorded locally; source_payload_sha256 is not a hash of fields.
    else:
        raise ValueError('Unsupported source evidence')
    m=re.fullmatch(r'https://api.github.com/repos/([^/]+/[^/]+)/commits/([0-9a-f]{40})',url)
    if not m or payload.get('sha')!=m[2]:raise ValueError('Exact source commit missing')
    return m[1].casefold(),m[2]


def load(p):
    return json.loads(p.read_text(encoding='utf-8-sig'))


def time(value):
    return datetime.fromisoformat(value.replace(' ', 'T').replace('Z', '+00:00') + ('+00:00' if not re.search(r'Z$|[+-]\d\d:\d\d$', value) else ''))


def public_commits(event):
    # closed alone never proves merge SHA public; require archive merged flag.
    result = [event.get('head_sha')]
    if event.get('merged') in (1, True):
        result.append(event.get('merge_commit_sha'))
    return [c for c in result if c and re.fullmatch('[0-9a-f]{40}', c)]


def run(root, semantics, output, event_dirs):
    output.mkdir(parents=True, exist_ok=False)
    docs = load(semantics / 'documents.json')
    plans = load(semantics / 'profiles.json')
    versions = {p['profile_version'] for p in plans}
    profiles = {}
    with (root / 'data/midterm/working/rc1_training_inputs_20261106_v1/profiles.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            p = json.loads(line)
            if p['split'] in ('train', 'validation') and p['profile_version'] in versions:
                profiles[p['profile_version']] = p
    events = defaultdict(list)
    hashes = {}
    for directory in event_dirs:
        for path in directory.glob('batch_*.json'):
            body = load(path)
            if 'data' not in body:
                continue
            hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
            for e in body['data']:
                for commit in public_commits(e):
                    events[e['repo_name'].casefold(), commit].append(dict(event=e, source=str(path)))
    identities = {}
    for key, d in docs.items():
        source = load(root / d['evidence_path'])
        identities[key] = source_identity(source)
    checks, missing = [], set()
    for plan in plans:
        cutoff = profiles[plan['profile_version']]['as_of']
        for key in plan['documents']:
            repo, commit = identities[key]
            proofs = [e for e in events[repo, commit] if time(e['event']['event_at']) < time(cutoff)]
            checks.append(dict(profile_version=plan['profile_version'], split=plan['split'], document_key=key,
                               repository=repo, commit=commit, cutoff=cutoff, proofs=proofs,
                               publication_passed=bool(proofs)))
            if not proofs:
                missing.add((repo, commit))
    queries = []
    targets = sorted(missing)
    for start in range(0, len(targets), 20):
        batch = targets[start:start+20]
        for repo, commit in batch:
            if not re.fullmatch(r'[\w.-]+/[\w.-]+', repo):
                raise ValueError('Invalid repository')
        conditions = ' OR '.join(f"(lower(repo_name)='{repo}' AND (head_sha='{commit}' OR merge_commit_sha='{commit}'))" for repo, commit in batch)
        sql = "SELECT repo_name,head_sha,merge_commit_sha,merged,toString(min(created_at),'UTC') AS event_at FROM github_events WHERE event_type='PullRequestEvent' AND (" + conditions + ") GROUP BY repo_name,head_sha,merge_commit_sha,merged LIMIT 200 FORMAT JSON"
        queries.append(dict(name=f'batch_{start//20:03d}', sql=sql))
    summary = dict(references=len(checks), passed=sum(c['publication_passed'] for c in checks),
                   unresolved_commit_targets=len(targets), queries=len(queries),
                   event_input_hashes=hashes, external_results_read=False, test_scored=False)
    by_profile = defaultdict(list)
    for c in checks:
        by_profile[c['profile_version']].append(c['publication_passed'])
    summary['profile_coverage'] = {s: dict(
        total=sum(p['split'] == s for p in plans),
        any_proven=sum(p['split'] == s and any(by_profile[p['profile_version']]) for p in plans),
        all_proven=sum(p['split'] == s and bool(by_profile[p['profile_version']]) and all(by_profile[p['profile_version']]) for p in plans))
        for s in ('train', 'validation')}
    for name, value in [('checks.json', checks), ('query_queue.json', queries), ('summary.json', summary),
                        ('unresolved_commits.json', [dict(repository=r, commit=c) for r, c in targets])]:
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k!='event_input_hashes'}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for n in ('root', 'semantics', 'output'):
        p.add_argument('--'+n, required=True, type=Path)
    p.add_argument('--events', nargs='*', type=Path, default=[])
    a = p.parse_args()
    run(a.root, a.semantics, a.output, a.events)
