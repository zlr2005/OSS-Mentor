"""Find verifiable pre-cutoff code in PRs regardless of merge status."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import shutil
import backfill_adapter_2024_2026 as b
import deepen_adapter_evidence as deep


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--allow-network',action='store_true');args=parser.parse_args()
    base=b.OUT.parent;source=base/'sparse_expansion_20261104_handoff_v5'
    outcomes={r['outcome_id']:r for r in b.rows(source/'outcome_index.jsonl')}
    gaps=[r for r in b.rows(source/'evidence_gaps.jsonl') if not r['missing_controls']]
    b.OUT=deep.OUT=base/'sparse_pr_code_20261105_v1';b.OUT.mkdir(exist_ok=True)
    api=b.API(args.allow_network);contributions=[];controls={};comparisons=[];exclusions=[]
    for gap in gaps:
        row=outcomes[gap['outcome_id']];cutoff=b.dt(row['profile_as_of']);identity=row['github_numeric_id']
        query_text=f'author:{row["developer_key"]} is:pr created:<={cutoff.date()} sort:created-desc'
        query='query { q0: search(query:'+json.dumps(query_text)+',type:ISSUE,first:30) { '+b.PAGE+' nodes { ... on PullRequest { url createdAt repository { nameWithOwner } commits(first:50) { '+b.PAGE+' nodes { commit { oid authoredDate committedDate author { user { databaseId } } } } } } } } }'
        payload,listing_ref=api.call(query=query);candidates=[]
        for pr in payload['data']['q0']['nodes']:
            if not pr or b.dt(pr['createdAt'])>=cutoff:continue
            for node in pr['commits']['nodes']:
                commit=node['commit'];author=(commit.get('author') or {}).get('user') or {}
                if author.get('databaseId')==identity and b.dt(commit['authoredDate'])<cutoff and b.dt(commit['committedDate'])<cutoff:
                    candidates.append((commit['committedDate'],pr['repository']['nameWithOwner'],commit['oid'],pr['url']))
        found=[]
        for _,repo,sha,pr_url in sorted(set(candidates),reverse=True)[:10]:
            try:
                detail,ref=api.call(endpoint=f'/repos/{repo}/commits/{sha}?per_page=100')
                if (detail.get('author') or {}).get('id')!=identity:continue
                event=detail['commit']['committer']['date'];authored=detail['commit']['author']['date']
                if b.dt(event)>=cutoff or b.dt(authored)>=cutoff:continue
                for file in b.choose_files(detail.get('files',[])):
                    blob,blob_ref=api.call(endpoint=f'/repos/{repo}/git/blobs/{file["sha"]}')
                    if blob.get('encoding')!='base64' or blob.get('size',0)>262144:continue
                    data=base64.b64decode(blob['content']);data.decode('utf-8')
                    if hashlib.sha1(f'blob {len(data)}\0'.encode()+data).hexdigest()!=file['sha']:raise ValueError('Blob hash mismatch')
                    location='code/'+file['sha']+'.txt';(b.OUT/'code').mkdir(exist_ok=True);(b.OUT/location).write_bytes(data)
                    found.append(dict(github_numeric_id=identity,login=row['developer_key'],repository=repo,
                        as_of=row['profile_as_of'],commit_sha=sha,event_at=event,author_at=authored,path=file['filename'],
                        language=b.code_language(file['filename']),blob_sha=file['sha'],content_sha256=b.digest(data),
                        content_location=location,evidence_ref=ref,blob_evidence_ref=blob_ref,listing_evidence_ref=listing_ref,
                        source_url=detail['html_url'],discovery_pr_url=pr_url,
                        historical_visibility_note='PR creation and commit timestamps checked; first public availability of individual commits not independently verified.'))
                if found:break
            except (ValueError,OSError) as exc:
                exclusions.append(dict(outcome_id=row['outcome_id'],repository=repo,commit_sha=sha,reason=type(exc).__name__))
        contributions+=found
        if found:
            window_path=b.ROOT/gap['evidence_window_file'];window=b.read(window_path);oldroot=window_path.parent.parent
            for c in window['controls']:
                controls[c['issue_key'],c['as_of']]=c
                dest=b.OUT/c['evidence_ref'];dest.parent.mkdir(parents=True,exist_ok=True)
                origin=oldroot/c['evidence_ref']
                if dest.exists() and b.digest(dest.read_bytes())!=b.digest(origin.read_bytes()):raise ValueError('Evidence collision')
                shutil.copy2(origin,dest)
            comparisons.append(dict(outcome_id=row['outcome_id'],github_numeric_id=identity,as_of=row['profile_as_of'],
                repository=row['repository'],comparison_issue_keys=[c['issue_key'] for c in window['controls']],training_ready=False))
        else:exclusions.append(dict(outcome_id=row['outcome_id'],reason='no_verified_pre_cutoff_code_in_bounded_PR_commit_search'))
        print(json.dumps(dict(outcome_id=row['outcome_id'],candidate_commits=len(candidates),files=len(found))),flush=True)
    b.lines('historical_contributions.jsonl',contributions);b.lines('historical_controls.jsonl',list(controls.values()))
    b.lines('comparison_groups.jsonl',comparisons);b.lines('exclusions.jsonl',exclusions)
    b.save('summary.json',dict(requested_outcomes=len(gaps),repaired_outcomes=len(comparisons),file_records=len(contributions),training_ready=False))
    b.save('manifest.json',dict(source_manifest_sha256=b.digest((source/'manifest.json').read_bytes()),
        collector_sha256=b.digest(Path(__file__).read_bytes()),files_sha256={p.relative_to(b.OUT).as_posix():b.digest(p.read_bytes()) for p in b.OUT.rglob('*') if p.is_file() and p.name!='manifest.json'}))
    deep.verify();print(json.dumps(b.read(b.OUT/'summary.json')),flush=True)


if __name__=='__main__':main()
