"""Collect a bounded public authored-code sample for live CODER input preparation."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import quote


def collect(login, output, *, get_json=None, expected_id=None):
    if not re.fullmatch(r'[A-Za-z0-9-]{1,39}', login):
        raise ValueError('Invalid GitHub login')
    output.mkdir(parents=True, exist_ok=False)
    evidence = []
    def get(endpoint):
        if get_json is None:
            result = subprocess.run(['gh', 'api', endpoint], capture_output=True, timeout=40)
            if result.returncode:
                raise RuntimeError('Public GitHub collection failed; no complete manifest published')
            data = json.loads(result.stdout)
            response_bytes=result.stdout
        else:
            data=get_json('/'+endpoint)
            response_bytes=json.dumps(data,ensure_ascii=False).encode('utf-8')
        path = output / f'response-{len(evidence):03d}.json'
        path.write_bytes(response_bytes)
        evidence.append(dict(endpoint=endpoint, path=path.name,
                             sha256=hashlib.sha256(response_bytes).hexdigest(),
                             observed_at=datetime.now(timezone.utc).isoformat()))
        return data
    user = get(f'users/{login}')
    uid = user['id']
    if expected_id is not None and uid!=expected_id:raise ValueError('GitHub numeric identity mismatch')
    if user['login'].casefold() != login.casefold():
        raise ValueError('GitHub identity mismatch')
    cutoff = datetime.now(timezone.utc)
    query = quote(f'author:{login} author-date:<={cutoff.date().isoformat()}', safe='')
    search = get(f'search/commits?q={query}&sort=author-date&order=desc&per_page=10')
    history, files = [], []
    for item in search.get('items', []):
        repo = item['repository']['full_name']
        if item['repository'].get('private') is not False:
            continue
        detail = get(f'repos/{repo}/commits/{item["sha"]}')
        if (detail.get('author') or {}).get('id') != uid:
            continue
        dates = [datetime.fromisoformat(detail['commit'][kind]['date'].replace('Z','+00:00'))
                 for kind in ['author','committer']]
        if any(at > cutoff for at in dates):
            continue
        for f in detail.get('files', []):
            if f.get('status') == 'removed':
                continue
            history.append([repo.casefold(), detail['sha'], f['filename']])
            if len(files) >= 3 or Path(f['filename']).suffix.casefold() not in {'.py','.js','.ts','.tsx','.java','.go','.rs','.cpp','.c'}:
                continue
            blob = get(f'repos/{repo}/git/blobs/{f["sha"]}')
            if blob.get('encoding') != 'base64' or blob.get('size', 0) > 200000:
                continue
            content = base64.b64decode(blob['content'])
            actual = hashlib.sha1(f'blob {len(content)}\0'.encode()+content).hexdigest()
            if actual != f['sha']:
                raise ValueError('Git blob hash mismatch')
            digest = hashlib.sha256(content).hexdigest()
            if any(row['content_sha256'] == digest for row in files):
                continue
            path = output / f'code-{len(files):02d}.txt'
            path.write_bytes(content)
            files.append(dict(repository=repo, commit_sha=detail['sha'], filename=f['filename'],
                              blob_sha=f['sha'], content_path=path.name, content_sha256=digest,
                              author_id=uid, author_date=detail['commit']['author']['date'],
                              committer_date=detail['commit']['committer']['date']))
    manifest = dict(login=user['login'], github_numeric_id=uid, as_of=cutoff.isoformat(),
                    history_complete=False, commit_search_limit=10, code_file_limit=3,
                    retained_X=history, documents=files, responses=evidence,
                    scope='public_live_evidence_sample', features_ready=False,
                    limitations=['Bounded search sample, not lifetime contribution totals.',
                                 'Observed now; cannot be used as historical publication proof.',
                                 'CODER profile encoding and platform profile binding still required.'])
    (output/'evidence_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(login=login, authored_paths=len(history), code_documents=len(files), features_ready=False)))
    return manifest


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--login',required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();collect(a.login,a.output)
