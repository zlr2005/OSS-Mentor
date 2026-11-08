"""按历史 X 路径确定仓库，抓取当前 open Issue 预备快照；不抓未来结果。"""
import argparse
from collections import Counter,defaultdict
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
from audit_v5_publication import source_identity,load


def run(materials,output):
    counts=defaultdict(Counter)
    languages={'.py':'python','.java':'java','.js':'javascript','.ts':'typescript','.rs':'rust','.go':'go'}
    for d in load(materials/'documents.json').values():
        lang=languages.get(Path(d['path']).suffix)
        if lang:
            repo,_=source_identity(load(Path(d['evidence_path'])))
            counts[lang][repo]+=1
    repos=sorted({repo for lang in sorted(counts) for repo,_ in sorted(counts[lang].items(),key=lambda x:(-x[1],x[0]))[:2]})
    output.mkdir(parents=True,exist_ok=False)
    plan=dict(repository_selection='top two repositories per six path languages by distinct full-X files; no labels/outcome ranking',
              repositories=repos,limit_per_repository=50,candidate_order='created descending',
              status='pilot_snapshot_not_enrolled',prediction_lock_at=None,maturity_started=False)
    (output/'plan.json').write_text(json.dumps(plan,indent=2))
    candidates=[];failures=[]
    for start in range(0,len(repos),4):
        fields=[]
        for i,repo in enumerate(repos[start:start+4]):
            owner,name=repo.split('/')
            fields.append('q%d: repository(owner:%s,name:%s) { nameWithOwner visibility issues(first:50,states:OPEN,orderBy:{field:CREATED_AT,direction:DESC}) { totalCount pageInfo { hasNextPage } nodes { number url title body state createdAt updatedAt locked labels(first:30) { nodes { name } pageInfo { hasNextPage } } } } }'%(i,json.dumps(owner),json.dumps(name)))
        query='query { '+' '.join(fields)+' }'
        observed=datetime.now(timezone.utc).isoformat()
        r=subprocess.run(['gh','api','graphql','-f','query='+query],capture_output=True,timeout=60)
        payload=json.loads(r.stdout) if r.stdout else {}
        record=dict(repositories=repos[start:start+4],query=query,observed_at=observed,payload=payload)
        path=output/f'snapshot_{start//4:03d}.json';path.write_text(json.dumps(record,indent=2),encoding='utf-8')
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        for i,repo in enumerate(repos[start:start+4]):
            data=(payload.get('data') or {}).get('q'+str(i)) or {}
            if not data or data.get('visibility')!='PUBLIC':failures.append(repo);continue
            for issue in (data.get('issues') or {}).get('nodes',[]):
                if issue['state']!='OPEN':continue
                candidates.append(dict(repository=repo,issue_key=repo+'#'+str(issue['number']),snapshot=issue,
                    observed_at=observed,source_file=str(path),source_sha256=digest,
                    eligibility='provisional_open_non_PR; refresh at actual prediction lock',training_allowed=False))
    (output/'candidates.json').write_text(json.dumps(candidates,indent=2),encoding='utf-8')
    summary=dict(repositories=len(repos),failed_repositories=failures,candidates=len(candidates),
                 snapshot_completed_at=datetime.now(timezone.utc).isoformat(),prediction_lock_at=None,
                 evaluation_ready=False,maturity_started=False,
                 warning='bounded repository/candidate coverage; profiles and predictions still need locking; not an independent evaluation result')
    (output/'summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('materials','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();run(a.materials,a.output)
