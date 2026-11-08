"""仅TRAIN开发者的新项目补采扩展；按现有时间窗检索并排除最早已知项目。"""
import argparse
from collections import defaultdict,Counter
from datetime import timedelta,datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
from audit_v5_publication import time
from audit_historical_candidate_evidence import check_snapshot,canonical


def rows(p):return [json.loads(l) for l in p.read_text(encoding='utf-8').splitlines()]


def run(root,dataset,profiles,output,limit=100,skip_plan=None):
    samples=[r for r in rows(dataset/'samples.jsonl') if r['split']=='train']
    pmap={p['profile_version']:p for p in rows(profiles/'profiles.jsonl')}
    lin={r['profile_version']:r for r in rows(profiles/'lineage.jsonl')}
    groups=defaultdict(dict)
    for r in samples:groups[r['github_numeric_id']][r['profile_version']]=r
    # Select from TRAIN using historical repository diversity, never validation failures.
    users=[]
    for uid,versions in groups.items():
        repos={k[0] for v in versions for k in lin[v]['retained_X']}
        if repos:users.append(uid)
    skip=set(json.loads(skip_plan.read_text())['users']) if skip_plan else set()
    users=sorted((u for u in users if u not in skip),key=lambda u:hashlib.sha256(f'first-project-expansion:{u}'.encode()).hexdigest())[:limit]
    excluded={(r['github_numeric_id'],r['issue_key']) for r in json.loads((root/'data/midterm/working/prospective_protocol_20261107_v3/excluded_user_issues.json').read_text())}
    excluded.update((r['github_numeric_id'],r['positive_issue_key']) for r in rows(dataset/'samples.jsonl'))
    output.mkdir(parents=True,exist_ok=False)
    plan=dict(users=users,selection='TRAIN only, deterministic hash, skip prior20 authors; no validation/test performance mining',
        max_prs_per_user=30,search_period='per author: earliest TRAIN cutoff through latest TRAIN cutoff+90days, capped2025-12-31; exclude up to3 earliest-known repositories',requests=(len(users)+3)//4,
        cutoff='latest existing TRAIN profile strictly before PR creation, outcome within90days',
        scope='target repository absent from observed public X proxy; not proof of actual first-ever repository contribution',
        training_ready=False,source_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (output/'plan.json').write_text(json.dumps(plan,indent=2))
    proposed=[];seen=set();reasons=Counter();reads=[]
    for start in range(0,len(users),4):
        fields=[];batch=users[start:start+4]
        for i,uid in enumerate(batch):
            version=min(groups[uid],key=lambda v:time(pmap[v]['as_of']));login=pmap[version]['developer_key']
            first=min(time(r['as_of']) for r in groups[uid].values())
            last=min(max(time(r['as_of']) for r in groups[uid].values())+timedelta(days=90),time('2025-12-31T23:59:59Z'))
            known_repos=Counter(k[0] for k in lin[version]['retained_X']).most_common(3)
            base_query=f'is:pr is:merged is:public author:{login} created:{first.date()}..{last.date()} sort:created-desc'
            excluded_repos=[' -repo:'+r for r,n in known_repos]
            while excluded_repos and len(base_query+''.join(excluded_repos))>250:excluded_repos.pop()
            query=base_query+''.join(excluded_repos)
            fields.append('q%d: search(query:%s,type:ISSUE,first:30) { issueCount pageInfo { hasNextPage } nodes { ... on PullRequest { url number createdAt mergedAt merged author { login ... on User { databaseId } } repository { nameWithOwner visibility } closingIssuesReferences(first:10) { pageInfo { hasNextPage } nodes { url number title body createdAt lastEditedAt state repository { nameWithOwner visibility } timelineItems(first:100,itemTypes:[CLOSED_EVENT,REOPENED_EVENT,RENAMED_TITLE_EVENT]) { pageInfo { hasNextPage } nodes { __typename ... on ClosedEvent { createdAt closer { ... on PullRequest { url } } } ... on ReopenedEvent { createdAt } ... on RenamedTitleEvent { createdAt previousTitle currentTitle } } } } } } } }'%(i,json.dumps(query)))
        q='query { '+' '.join(fields)+' }'
        result=subprocess.run(['gh','api','graphql','-f','query='+q],capture_output=True,timeout=60)
        payload=json.loads(result.stdout) if result.stdout else {}
        source=dict(payload=payload,payload_sha256=hashlib.sha256(canonical(payload)).hexdigest(),observed_at=datetime.now(timezone.utc).isoformat(),query=q)
        path=output/f'collection_{start//4:03d}.json';path.write_text(json.dumps(source,indent=2),encoding='utf-8')
        reads.append(dict(batch=start//4,passed=result.returncode==0 and not payload.get('errors')))
        for i,uid in enumerate(batch):
            data=(payload.get('data') or {}).get('q'+str(i)) or {}
            for pr in data.get('nodes',[]):
                if (pr.get('author') or {}).get('databaseId')!=uid or not pr.get('merged') or (pr.get('repository') or {}).get('visibility')!='PUBLIC':continue
                eligible=[r for r in groups[uid].values() if time(r['as_of'])<time(pr['createdAt'])]
                if not eligible:reasons['no_earlier_training_profile']+=1;continue
                parent=max(eligible,key=lambda r:time(r['as_of']))
                asof=parent['as_of'];v=parent['profile_version']
                historical={k[0] for k in lin[v]['retained_X']}
                for issue in (pr.get('closingIssuesReferences') or {}).get('nodes',[]):
                    repo=issue['repository']['nameWithOwner'].casefold();key=repo+'#'+str(issue['number'])
                    if issue['repository'].get('visibility')!='PUBLIC':continue
                    if (uid,key) in excluded or (uid,key) in seen:reasons['known_or_duplicate_user_issue']+=1;continue
                    if repo in historical:reasons['target_repository_already_in_X']+=1;continue
                    closing=[e for e in issue['timelineItems']['nodes'] if e.get('__typename')=='ClosedEvent' and (e.get('closer') or {}).get('url')==pr['url']]
                    if not closing:reasons['no_exact_closing_event']+=1;continue
                    outcome=min(closing,key=lambda e:time(e['createdAt']))['createdAt']
                    if not(time(asof)<time(pr['createdAt'])<=time(pr['mergedAt'])<=time(outcome)<=time(asof)+timedelta(days=90)):reasons['outside90day_training_window']+=1;continue
                    c=dict(available_from=asof,title=issue['title'],body_text=issue.get('body') or '')
                    # Restore historical title before invoking the unchanged replay checker.
                    for e in sorted(issue['timelineItems']['nodes'],key=lambda e:time(e['createdAt']),reverse=True):
                        if e.get('__typename')=='RenamedTitleEvent' and time(e['createdAt'])>time(asof):c['title']=e['previousTitle']
                    c['content_sha256']=hashlib.sha256(canonical(dict(title=c['title'],body=c['body_text']))).hexdigest()
                    try:check_snapshot(c,issue)
                    except (ValueError,KeyError) as error:reasons['snapshot:'+str(error)]+=1;continue
                    seen.add((uid,key));proposed.append(dict(github_numeric_id=uid,developer_key=pmap[v]['developer_key'],profile_version=v,as_of=asof,
                        issue_key=key,pr_url=pr['url'],interaction_at=pr['createdAt'],outcome_at=outcome,candidate_snapshot=c,
                        source_file=str(path),source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),historical_publication_verified=False,training_ready=False))
    (output/'proposed_samples.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in proposed),encoding='utf-8')
    summary=dict(users=len(users),queries=len(reads),successful_queries=sum(r['passed'] for r in reads),proposed_new_samples=len(proposed),
        new_developers=len({r['github_numeric_id'] for r in proposed}),rejections=dict(reasons),
        training_ready=False,remaining='independent pre-cutoff public Issue event, historical candidate pool and vector admission before training',validation_test_performance_used=False,known_labels_read_only_for_duplicate_exclusion=True)
    (output/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('root','dataset','profiles','output'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--limit',type=int,default=100);p.add_argument('--skip-plan',type=Path)
    a=p.parse_args();run(a.root,a.dataset,a.profiles,a.output,a.limit,a.skip_plan)
