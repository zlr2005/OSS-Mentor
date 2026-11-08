"""Collect pinned historical manifests and setup docs for low-history candidates.

Uses already verified sampled commits, not current repository metadata. A sampled
commit is not asserted to be the latest repository state at the cutoff.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re

import backfill_adapter_2024_2026 as b

FILES = {'package.json': 'Node.js', 'tsconfig.json': 'TypeScript',
         'pyproject.toml': 'Python', 'setup.py': 'Python', 'requirements.txt': 'Python',
         'Cargo.toml': 'Rust', 'go.mod': 'Go', 'pom.xml': 'Java',
         'build.gradle': 'Gradle', 'README.md': None, 'CONTRIBUTING.md': None}


def collect(api, job):
    repo, cutoff, commit = job
    owner, name = repo.split('/')
    fields = ['commit: object(expression:'+json.dumps(commit)+') { ... on Commit { oid committedDate authoredDate } }']
    for i, path in enumerate(FILES):
        fields.append(f'f{i}: object(expression:'+json.dumps(commit+':'+path)
                      +') { ... on Blob { oid byteSize isBinary text } }')
    query = 'query { repository(owner:'+json.dumps(owner)+',name:'+json.dumps(name)+') { '+' '.join(fields)+' } }'
    payload, ref = api.call(query=query)
    result = payload['data']['repository']
    if not result or not result.get('commit'):
        raise ValueError('Pinned commit unavailable')
    c = result['commit']
    if c['oid'] != commit or any(b.dt(c[k]) >= b.dt(cutoff) for k in ('authoredDate', 'committedDate')):
        raise ValueError('Pinned commit identity/time mismatch')
    documents, ecosystems, dependencies, setup = [], set(), [], []
    for i, (path, ecosystem) in enumerate(FILES.items()):
        blob = result.get(f'f{i}')
        if not blob or blob.get('isBinary') or blob.get('text') is None:
            continue
        data = blob['text'].encode('utf-8')
        if len(data) != blob['byteSize'] or hashlib.sha1(f'blob {len(data)}\0'.encode()+data).hexdigest() != blob['oid']:
            raise ValueError('Historical document blob mismatch')
        if len(data) > 262144:
            continue
        location = 'documents/'+blob['oid']+'.txt'
        dest = b.OUT / location
        dest.parent.mkdir(exist_ok=True)
        dest.write_bytes(data)
        documents.append(dict(path=path, blob_sha=blob['oid'], content_sha256=b.digest(data),
                              content_location=location, evidence_ref=ref))
        if ecosystem:
            ecosystems.add(ecosystem)
        if path == 'package.json':
            try:
                package = json.loads(blob['text'])
                for kind in ('dependencies', 'devDependencies', 'peerDependencies'):
                    if isinstance(package.get(kind), dict):
                        dependencies.extend(dict(name=k, version=v, kind=kind, source_path=path)
                                            for k,v in package[kind].items())
            except (ValueError, AttributeError):
                pass
        if path in ('README.md', 'CONTRIBUTING.md'):
            setup.extend(dict(path=path, line=i+1, text=line[:500])
                for i,line in enumerate(blob['text'].splitlines())
                if re.search(r'(?i)\b(install|prerequisit|requirements?|setup|npm (?:ci|install)|pip install|cargo build|jdk|node\.js)\b', line))
    return dict(repository=repo, as_of=cutoff, commit_sha=commit,
        committed_at=c['committedDate'], authored_at=c['authoredDate'], documents=documents,
        manifest_ecosystems=sorted(ecosystems), package_dependencies=dependencies, setup_document_evidence=setup,
        primary_language=None, code_difficulty=None, setup_difficulty=None,
        evidence_scope='Verified pre-cutoff sampled commit; manifests indicate repository ecosystem, not Issue required language or latest cutoff state.',
        first_public_availability_verified=False, training_ready=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-network', action='store_true')
    args = parser.parse_args()
    base = b.ROOT / 'data/midterm/working'
    source = base / 'merged_ab_history_2024_2026_v6'
    cohort = {r['github_numeric_id'] for r in b.rows(base/'temporal_xy_2024_2026_v6/low_history_gaps.jsonl')}
    groups = [g for g in b.rows(base/'temporal_xy_2024_2026_v6/train.jsonl') if g['github_numeric_id'] in cohort]
    keys = {(y['issue_key'].rsplit('#',1)[0], g['cutoff_at']) for g in groups for y in g['y']}
    histories = b.rows(source/'file_contributions.jsonl')
    jobs, missing = [], []
    for repo, cutoff in sorted(keys):
        candidates = [r for r in histories if r['repository'].casefold() == repo.casefold()
                      and r.get('event_at') and b.dt(r['event_at']) < b.dt(cutoff)]
        if not candidates:
            missing.append(dict(repository=repo, as_of=cutoff, reason='no_verified_pre_cutoff_commit'))
            continue
        latest = max(candidates, key=lambda r: r['event_at'])
        jobs.append((repo, cutoff, latest['commit_sha']))
    b.OUT = base / 'historical_repository_context_20261104_v1'
    b.OUT.mkdir(exist_ok=True)
    api = b.API(args.allow_network)
    records, failures = [], []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(collect, api, j): j for j in jobs}
        for done, future in enumerate(as_completed(futures), 1):
            try:
                records.append(future.result())
            except Exception as exc:
                failures.append(dict(job=futures[future], reason=type(exc).__name__))
            b.lines('repository_context.jsonl', sorted(records, key=lambda r:(r['repository'],r['as_of'])))
            b.lines('exclusions.jsonl', missing+failures)
            if done % 10 == 0 or done == len(jobs):
                print(json.dumps(dict(done=done, total=len(jobs), failures=len(failures))), flush=True)
    summary = dict(requested_repo_cutoffs=len(keys), collected=len(records),
        missing_commits=len(missing), failed_reads=len(failures), documents=sum(len(r['documents']) for r in records),
        contexts_with_ecosystems=sum(bool(r['manifest_ecosystems']) for r in records),
        contexts_with_setup_evidence=sum(bool(r['setup_document_evidence']) for r in records), training_ready=False)
    b.save('summary.json', summary)
    b.save('manifest.json', dict(source_manifest_sha256=b.digest((source/'manifest.json').read_bytes()),
        collector_sha256=b.digest(Path(__file__).read_bytes()),
        files_sha256={p.relative_to(b.OUT).as_posix():b.digest(p.read_bytes())
                      for p in b.OUT.rglob('*') if p.is_file() and p.name!='manifest.json'}))
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
