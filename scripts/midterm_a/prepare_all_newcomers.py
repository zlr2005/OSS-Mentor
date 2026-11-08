"""Read-only local union of every acquired newcomer/help-wanted task source."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from optimized_teacher_collection import NEWCOMER_LABEL_GROUPS


def prepare(output):
    output.mkdir(parents=True,exist_ok=False)
    signals=set(sum((list(v) for v in NEWCOMER_LABEL_GROUPS.values()),[]))
    def relevant(labels):return any(re.sub(r'[-_ ]+',' ',str(l)).strip().lower() in signals for l in labels)
    rows={};manifest=[];conflicts=[]
    def add(r,source,labels,selected=False):
        if not selected and not relevant(labels):return
        if not re.fullmatch(r'[\w.-]+/[\w.-]+',r['repo_name']) or int(r['issue_number'])<=0:return
        issue_id=str(r['issue_id'])
        if not issue_id.isdigit() or int(issue_id)<=0:return
        r=dict(r,issue_id=issue_id,issue_number=int(r['issue_number']))
        if issue_id not in rows:rows[issue_id]=dict(r,source_batches=[],historical_labels=[])
        row=rows[issue_id]
        if row['repo_name'].lower()!=r['repo_name'].lower() or row['issue_number']!=r['issue_number']:
            conflicts.append(dict(issue_id=issue_id,previous=row,incoming=r))
        if not row.get('repo_id') and r.get('repo_id'):row['repo_id']=r['repo_id']
        if source not in row['source_batches']:row['source_batches'].append(source)
        row['historical_labels']=sorted(set(row['historical_labels'])|set(labels))
        row['issue_labels.name']=row['historical_labels']
    root=Path('D:/OSS-Mentor/data/teacher_clickhouse')
    for path in sorted(root.glob('*/issues_discovered.json')):
        raw=path.read_bytes();data=json.loads(raw.decode('utf-8-sig'))
        for r in data:add(r,str(path),r.get('issue_labels.name',[]))
        manifest.append(dict(path=str(path),type='teacher_local_discovery',rows=len(data),sha256=hashlib.sha256(raw).hexdigest()))
    databases=list(Path('D:/OSS-Mentor/data/working').glob('*.sqlite3'))+list(Path('data').glob('*.sqlite3'))
    for path in sorted(databases):
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
            db.row_factory=sqlite3.Row
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'task_candidate','repository'}<=tables:continue
            query='SELECT t.github_issue_id,t.issue_number,t.title,t.labels_json,t.newcomer_label_signal,t.source_system,r.full_name,r.github_repository_id FROM task_candidate t JOIN repository r USING(repository_id)'
            count=0
            for r in db.execute(query):
                if any(s in (r['source_system'] or '').lower() for s in ('fixture','mock','synthetic')):continue
                labels=json.loads(r['labels_json'] or '[]')
                add(dict(repo_name=r['full_name'],repo_id=r['github_repository_id'],issue_id=r['github_issue_id'],issue_number=r['issue_number'],issue_title=r['title']),str(path),labels,bool(r['newcomer_label_signal']))
                count+=1
            manifest.append(dict(path=str(path.resolve()),type='candidate_database_readonly',rows=count))
    for path in sorted(Path('data/midterm/working').glob('live_issuepilot_*/candidates.json')):
        data=json.loads(path.read_text(encoding='utf-8-sig'))
        for r in data:
            repo=r.get('repository') or r.get('repository_full_name')
            if isinstance(repo,dict):repo=repo.get('full_name')
            if not repo:continue
            labels=r.get('labels',[]);labels=[l['name'] if isinstance(l,dict) else l for l in labels]
            add(dict(repo_name=repo,repo_id=r.get('repository_id'),issue_id=r.get('github_issue_id',r.get('issue_id',r.get('id'))),issue_number=r.get('issue_number',r.get('number')),issue_title=r.get('title')),str(path),labels,bool(r.get('newcomer_label_signal')))
        manifest.append(dict(path=str(path.resolve()),type='live_issuepilot_candidates',rows=len(data)))
    records=sorted(rows.values(),key=lambda r:(r['repo_name'].lower(),r['issue_number']))
    for name,value in [('source_issues.json',records),('source_manifest.json',manifest),('identity_conflicts.json',conflicts)]:
        (output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if conflicts:raise ValueError('Global identity conflicts require resolution')
    return dict(issues=len(records),repositories=len({r['repo_name'].lower() for r in records}),inputs=len(manifest))


if __name__=='__main__':
    print(prepare(Path('data/all_newcomers_screening_20261021_v1')))
