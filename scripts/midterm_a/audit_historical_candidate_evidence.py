"""核验冻结历史候选的身份、正文、标题重放和开放状态；公开事件另行核验。"""
import argparse
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path
import re
from audit_v5_publication import time


def canonical(x):return (json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode()


def issue_nodes(node,key):
    if isinstance(node,dict):
        if isinstance(node.get('url'),str) and '/issues/' in node['url'] and 'repository' in node:
            repo=node['repository']['nameWithOwner'].casefold()
            if repo+'#'+str(node.get('number'))==key:yield node
        for v in node.values():yield from issue_nodes(v,key)
    elif isinstance(node,list):
        for v in node:yield from issue_nodes(v,key)


def check_snapshot(candidate,issue):
    cutoff=time(candidate['available_from'])
    if time(issue['createdAt'])>cutoff:raise ValueError('created_after_cutoff')
    timeline=issue.get('timelineItems') or {}
    if timeline.get('pageInfo',{}).get('hasNextPage') is not False:raise ValueError('incomplete_timeline')
    events=sorted(timeline.get('nodes',[]),key=lambda e:time(e['createdAt']))
    state='OPEN'
    for event in events:
        if time(event['createdAt'])<=cutoff:
            if event['__typename']=='ClosedEvent':state='CLOSED'
            if event['__typename']=='ReopenedEvent':state='OPEN'
    if state!='OPEN':raise ValueError('closed_at_cutoff')
    final='OPEN'
    for e in events:
        if e['__typename']=='ClosedEvent':final='CLOSED'
        if e['__typename']=='ReopenedEvent':final='OPEN'
    if final!=issue['state']:raise ValueError('timeline_current_state_mismatch')
    edited=issue.get('lastEditedAt')
    if edited and time(edited)>cutoff:raise ValueError('current_body_edited_after_cutoff')
    title=issue['title']
    for event in reversed(events):
        if event['__typename']=='RenamedTitleEvent' and time(event['createdAt'])>cutoff:
            if title!=event['currentTitle']:raise ValueError('title_replay_chain_mismatch')
            title=event['previousTitle']
    if title!=candidate['title'] or (issue.get('body') or '')!=candidate['body_text']:
        raise ValueError('historical_text_mismatch')
    if hashlib.sha256(canonical(dict(title=title,body=candidate['body_text']))).hexdigest()!=candidate['content_sha256']:
        raise ValueError('content_hash_mismatch')


def run(root,output):
    base=root/'data/midterm/working/rc1_training_inputs_20261106_v1'
    source=root/'data/midterm/working/advanced_adapter_backfill_2024_2026_v1'
    rows=[json.loads(l) for l in (base/'historical_issue_candidates.jsonl').read_text(encoding='utf-8').splitlines()]
    output.mkdir(parents=True,exist_ok=False)
    checks=[];cache={};hashes={}
    for c in rows:
        reasons=[];path=source/c['evidence_ref']
        try:
            if path not in cache:
                record=json.loads(path.read_text(encoding='utf-8-sig'))
                if hashlib.sha256(canonical(record['payload'])).hexdigest()!=record['payload_sha256']:raise ValueError('source_hash_mismatch')
                cache[path]=record['payload'];hashes[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
            matches=list(issue_nodes(cache[path],c['issue_key']))
            if not matches:raise ValueError('exact_issue_not_in_evidence')
            failures=[]
            for issue in matches:
                try:check_snapshot(c,issue);break
                except ValueError as e:failures.append(str(e))
            else:raise ValueError('|'.join(sorted(set(failures))))
        except (OSError,KeyError,TypeError,ValueError) as e:reasons.append(str(e))
        checks.append(dict(issue_key=c['issue_key'],cutoff=c['available_from'],content_sha256=c['content_sha256'],local_replay_passed=not reasons,reasons=reasons,source=str(path),public_before_cutoff=False))
    targets=sorted({(c['issue_key'].split('#')[0],int(c['issue_key'].split('#')[1])) for c in checks if c['local_replay_passed']})
    queue=[]
    for start in range(0,len(targets),100):
        conditions=[]
        grouped=defaultdict(list)
        for repo,number in targets[start:start+100]:
            if not re.fullmatch(r'[\w.-]+/[\w.-]+',repo):raise ValueError('Bad repository')
            grouped[repo].append(number)
        for repo,numbers in grouped.items():conditions.append(f"(lower(repo_name)='{repo}' AND number IN ({','.join(map(str,numbers))}))")
        sql="SELECT repo_name,number,toString(min(created_at),'UTC') AS event_at FROM github_events WHERE event_type='IssuesEvent' AND ("+' OR '.join(conditions)+") GROUP BY repo_name,number LIMIT 200 FORMAT JSON"
        queue.append(dict(name=f'batch_{start//100:03d}',sql=sql))
    summary=dict(candidates=len(checks),local_replay_passed=sum(c['local_replay_passed'] for c in checks),rejections=dict(Counter(r for c in checks for r in c['reasons'])),public_issue_queries=len(queue),publication_verified=False)
    for name,value in [('checks.json',checks),('query_queue.json',queue),('summary.json',summary),('source_hashes.json',hashes)]:
        (output/name).write_text(json.dumps(value,indent=2),encoding='utf-8')
    print(json.dumps(summary))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.root,a.output)
