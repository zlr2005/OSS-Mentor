"""Offline productive-range selection and replacement of the unused weekly plan."""
import argparse
from collections import defaultdict,deque
from datetime import datetime,timezone,timedelta
import gzip
import hashlib
import json
from pathlib import Path
import re
from optimized_teacher_collection import POLICY,DEFAULT_BUDGET,make_batch


def prepare(root,workspace,output):
    if output.exists():raise FileExistsError(output)
    def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
    rows={};seeds={};covered=set();inputs=[];languages={}
    previous=Path('D:/OSS-Mentor/data/screening/teacher_completed_round14_20261018_v3/review_pack.json.gz')
    pack=json.loads(gzip.decompress(previous.read_bytes()))
    for m in pack['repository_details'].values():languages[int(m['id'])]=m.get('language')
    for path in (workspace/'config/midterm_datasets').glob('teacher_round*_plan.json'):
        for s in read(path).get('repositories',[]):
            if s.get('language'):languages[int(s['id'])]=s['language']
    for d in sorted(root.iterdir()):
        if not d.is_dir() or not re.fullmatch(r'(expanded|round\d+)_\d{8}T\d{6}Z',d.name):continue
        if not (d/'status.json').exists():continue
        status=read(d/'status.json')
        if status['status'] not in ('finished_unverified','stopped_unverified','paused_budget_unverified'):
            raise ValueError('Collection still active: '+d.name)
        plan={q['key']:q for q in read(d/'query_plan.json')}
        for q in plan.values():
            owner=re.search(r'org_id IN \(0,(\d+)\)',q['sql'])
            if owner:seeds[int(q['repo_id'])]=dict(repository=q['repository'],repo_id=int(q['repo_id']),owner_id=int(owner[1]))
        for r in status.get('batches',[]):
            if r['status']=='exported':q=plan[r['key']];covered.add((int(q['repo_id']),int(q['month'])))
        path=d/'issues_discovered.json';raw=path.read_bytes()
        inputs.append(dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest(),status=status['status']))
        for r in json.loads(raw.decode('utf-8-sig')):
            key=(str(r['repo_id']),int(r['issue_number']));rows.setdefault(key,r)
    counts=defaultdict(int)
    for repo,number in rows:counts[int(repo)]+=1
    selected=[]
    for language in ('Java','JavaScript','Python','Rust','Go','TypeScript'):
        eligible=[repo for repo in seeds if languages.get(repo)==language and counts[repo]>=60
                  and any((repo,m) not in covered for m in range(202601,202609))]
        selected.extend(sorted(eligible,key=lambda repo:-counts[repo])[:10])
    zone=timezone(timedelta(hours=8))
    def batch(seed,month):
        start=datetime(2026,month%100,1,tzinfo=zone)
        end=datetime(2026,month%100+1,1,tzinfo=zone)
        if month==202610:end=datetime(2026,10,19,tzinfo=zone)
        return make_batch(seed,month,int(start.timestamp()),int(end.timestamp()))
    weights={202601:1.25,202602:1.2,202603:1.1,202604:1,202605:.7,202606:.5,202608:.35,202607:.2}
    historical=[]
    for repo in selected:
        for month in weights:
            if (repo,month) not in covered:
                b=batch(seeds[repo],month);b['collection_reason']='productive_repository_unqueried_historical_month'
                b['prior_distinct_issues']=counts[repo];historical.append(b)
    historical.sort(key=lambda b:-(counts[b['repo_id']]*weights[b['month']]))
    old=read(workspace/'config/midterm_datasets/teacher_round20_plan.json')
    recent=[]
    for s in old['repositories']:
        seed=dict(repository=s['name'],repo_id=s['id'],owner_id=s['owner'])
        for month in (202610,202609):
            if (s['id'],month) not in covered:
                b=batch(seed,month);b['collection_reason']='unused_round20_monthly_replacement';recent.append(b)
    # Mix preserved recent discovery with productive historical gaps. Low-yield
    # repositories are deferred at runtime, not silently declared exhausted.
    historical=deque(historical);recent=deque(recent);batches=[]
    while historical or recent:
        for _ in range(3):
            if historical:batches.append(historical.popleft())
        if recent:batches.append(recent.popleft())
    assert len({b['key'] for b in batches})==len(batches)
    for b in batches:assert (b['repo_id'],b['month']) not in covered
    retries=[]
    for s in old['batches']:
        if not s['reason'].startswith('retry_'):continue
        start=datetime(2026,s['month']%100,s['day'],tzinfo=zone)
        end=start+timedelta(days=s['end_day']-s['day'])
        b=make_batch(dict(repository=s['name'],repo_id=s['id'],owner_id=s['owner']),s['month'],int(start.timestamp()),int(end.timestamp()))
        b.update(collection_reason='pending_original_round20_retry',legacy_retry_key=s.get('parent_key'))
        retries.append(b)
    batches.extend(retries)
    assert len({b['key'] for b in batches})==len(batches)
    value=dict(policy=POLICY,prepared_at=datetime.now(timezone.utc).isoformat(),budget=DEFAULT_BUDGET,
        batches=batches,baseline_keys=[list(k) for k in sorted(rows)],source_manifest=inputs,
        selection_summary=dict(productive_repositories=len(selected),historical_months=sum(b['collection_reason'].startswith('productive') for b in batches),
            recent_months=sum(b['collection_reason'].startswith('unused') for b in batches),
            preserved_retry_batches=len(retries),
            old_weekly_recent_batches=8*len(old['repositories']),old_round20_was_not_executed=True),
        limitations=['Historical Issue discovery is not current recommendation inventory.',
            'Budgets constrain this new collector only; provider/account quota and earlier legacy traffic are unknown.',
            'Server guards may be checked after blocks; zero resource-limit failures cannot be guaranteed.',
            'Full responses are split within the same guards; pending/failed ranges remain explicit.'])
    output.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return dict(roots=len(batches),baseline_unique=len(rows),**value['selection_summary'])


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path('D:/OSS-Mentor/data/teacher_clickhouse'))
    p.add_argument('--workspace',type=Path,default=Path.cwd())
    p.add_argument('--output',type=Path,required=True)
    print(json.dumps(prepare(**vars(p.parse_args()))))
