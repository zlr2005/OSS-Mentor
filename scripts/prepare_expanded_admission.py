"""Opt quarantined identities into expanded content gates without admitting them yet."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from oss_mentor.candidate_admission import EXPANDED_CONTENT_POLICY, admission_evidence
from oss_mentor.task_readiness import task_review_reasons
from finalize_automated_screening import connect, digest, snapshot, load_rows, identity, is_ready


def protected(conn,selected_ids):
    result={}
    for table, in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        if table.startswith('sqlite_'):continue
        rows=[]
        for row in conn.execute('SELECT * FROM "'+table.replace('"','""')+'"'):
            item=dict(row)
            if table=='task_candidate' and item['task_candidate_id'] in selected_ids:
                item.pop('feature_evidence_json')
            rows.append(digest(item))
        result[table]=digest(sorted(rows))
    return result


def apply_tags(path,references,now):
    conn=connect(path,'rw')
    try:
        conn.execute('BEGIN IMMEDIATE')
        rows=load_rows(conn)
        selected=[r for r in rows if identity(r) in references]
        if len(selected)!=len(references):raise ValueError('Missing or duplicate target identity')
        ids={r['task_candidate_id'] for r in selected}
        before=protected(conn,ids)
        ready_before={identity(r) for r in rows if is_ready(r,now)}
        content_pass=0
        for row in selected:
            if str(row['github_issue_id'])!=references[identity(row)]['github_issue_id']:
                raise ValueError('GitHub global identity mismatch')
            evidence=json.loads(row['feature_evidence_json'])
            evidence.update(admission_evidence({'admission_policy':EXPANDED_CONTENT_POLICY}))
            if isinstance(evidence.get('automated_screening'),dict):
                evidence['automated_screening']['superseded_by']=EXPANDED_CONTENT_POLICY
            row['feature_evidence_json']=json.dumps(evidence,ensure_ascii=False,sort_keys=True)
            content_pass+=not task_review_reasons(row)
            conn.execute('UPDATE task_candidate SET feature_evidence_json=? WHERE task_candidate_id=?',
                         (row['feature_evidence_json'],row['task_candidate_id']))
        if protected(conn,ids)!=before:raise ValueError('Protected source or business data changed')
        if {identity(r) for r in load_rows(conn) if is_ready(r,now)}!=ready_before:
            raise ValueError('Policy tagging must not bypass current-state verification')
        if conn.execute('PRAGMA quick_check').fetchone()[0]!='ok' or conn.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Database checks failed')
        conn.commit()
        return {'selected':len(selected),'content_gate_potential':content_pass,'ready_before_live_checks':len(ready_before),
                'protected_fingerprints':before}
    except BaseException:
        conn.rollback();raise
    finally:conn.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():parser.error('Use a new output directory')
    rows=[json.loads(line) for line in (args.source/'decisions.jsonl').read_text(encoding='utf-8').splitlines()]
    references={r['identity']:r for r in rows if r['decision']=='quarantine'}
    if not references:parser.error('No quarantined records')
    args.output.mkdir(parents=True);(args.output/'evidence').mkdir()
    now=datetime.now(timezone.utc);results={}
    names=['oss_mentor.sqlite3','oss_mentor_web.sqlite3']
    for name in names:
        original=ROOT/'data'/name;backup=args.output/(name+'.before');staging=args.output/(name+'.staging')
        snapshot(original,backup);snapshot(backup,staging)
        results[name]=apply_tags(staging,references,now)
    for name in names:
        actual=apply_tags(ROOT/'data'/name,references,now)
        if actual!=results[name]:raise ValueError('Staging and destination changed')
    jobs=[]
    connection=connect(ROOT/'data/oss_mentor_web.sqlite3')
    try:
        canonical={identity(r):r['repository'] for r in load_rows(connection)}
    finally:connection.close()
    for key,r in sorted(references.items()):
        _,number=key.rsplit('#',1)
        repo=canonical[key]
        jobs.append({'repository':repo,'issue_number':int(number),'github_issue_id':r['github_issue_id'],
            'status':'pending','admission_policy':EXPANDED_CONTENT_POLICY,'manual_review_required':False})
    (args.output/'jobs.json').write_text(json.dumps(jobs,ensure_ascii=False,indent=2),encoding='utf-8')
    manifest={'created_at':now.isoformat(),'job_count':len(jobs),'admission_policy':EXPANDED_CONTENT_POLICY,
        'scope':'Fresh GitHub state/timeline/PR checks for explicitly expanded content admission',
        'content_policy':'Unknown type and unclear/design-pending scope are warnings; hard availability and integrity gates unchanged.',
        'cache_policy':'No source responses inherited; full new verification required.',
        'model_inference':False,'source_screening':str(args.source.resolve())}
    (args.output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    (args.output/'preparation_audit.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'prepared':len(jobs),'databases':{name:{k:v for k,v in r.items() if k!='protected_fingerprints'} for name,r in results.items()}},ensure_ascii=False))


if __name__=='__main__':main()
