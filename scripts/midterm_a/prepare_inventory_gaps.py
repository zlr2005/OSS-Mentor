"""Prepare bounded opened-Issue ranges after subtracting complete prior queries."""
import argparse
from collections import defaultdict, Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re

from optimized_teacher_collection import DEFAULT_BUDGET, POLICY, make_batch


def gaps(start, end, intervals):
    cursor=start
    result=[]
    for left,right in sorted(intervals):
        if right<=cursor or left>=end:
            continue
        if left>cursor:
            result.append((cursor,min(left,end)))
        cursor=max(cursor,min(right,end))
    if cursor<end:
        result.append((cursor,end))
    return result


def prepare(output,months=(202610,202609)):
    if output.exists():
        raise FileExistsError(output)
    read=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
    root=Path('D:/OSS-Mentor/data/teacher_clickhouse')
    context=Path('data/growth_projects_20261023_v1')
    ev=read(context/'final_evidence.json')
    allowed={r['repository'] for r in read(context/'results/repository_audit.json') if r['metadata_passed']}
    covered=defaultdict(list);baseline=set();manifest=[]
    zone=timezone(timedelta(hours=8));now=datetime.now(timezone.utc)
    cutoff=int(datetime(2026,10,23,tzinfo=zone).timestamp())
    for source in sorted(root.glob('*/issues_discovered.json')):
        raw=source.read_bytes()
        for r in json.loads(raw.decode('utf-8-sig')):
            baseline.add((str(r['repo_id']),int(r['issue_number'])))
        manifest.append(dict(path=str(source),sha256=hashlib.sha256(raw).hexdigest()))
        status_path=source.parent/'status.json';plan_path=source.parent/'query_plan.json'
        if not status_path.exists() or not plan_path.exists():
            continue
        state=read(status_path)
        if state.get('status') in ('running','waiting_for_credentials','connecting'):
            raise ValueError('Collection is active: '+source.parent.name)
        plan=read(plan_path)
        if not isinstance(plan,list):
            continue
        index={q['key']:q for q in plan}
        run_match=re.search(r'_(\d{8}T\d{6}Z)$',source.parent.name)
        if not run_match:
            continue
        # An old unbounded month query only covers data that existed when that
        # run started. Do not count later days of the month as queried.
        run_start=int(datetime.strptime(run_match[1],'%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc).timestamp())
        for result in state.get('batches',[]):
            if result.get('status')!='exported' or result.get('possibly_truncated') is not False:
                continue
            q=result.get('batch') or index.get(result.get('key'))
            if not q or q.get('discovery_filter','all_opened')!='all_opened':
                continue
            query=q.get('sql','')
            if 'arrayExists' in query or "action='opened'" not in query or "type='IssuesEvent'" not in query:
                continue
            month=int(q['month']);year,month_number=divmod(month,100)
            month_start=int(datetime(year,month_number,1,tzinfo=zone).timestamp())
            month_end=int(datetime(year+(month_number==12),month_number%12+1,1,tzinfo=zone).timestamp())
            left=re.search(r'created_at>=toDateTime\((\d+)\)',query)
            right=re.search(r'created_at<toDateTime\((\d+)\)',query)
            start=int(left[1]) if left else month_start
            end=min(int(right[1]) if right else month_end,run_start,cutoff)
            if start<end:
                covered[int(q['repo_id'])].append((start,end))
    positive=read(Path('data/inventory_screening_20261023_v1/results_v2/candidates.json'))
    productivity=Counter(r['repository'] for r in positive)
    batches=[]
    for repo in sorted(allowed):
        envelope=ev['repositories'][repo];p=envelope['payload']
        if now-datetime.fromisoformat(envelope['observed_at'].replace('Z','+00:00'))>timedelta(days=7):
            raise ValueError('Repository seed evidence is stale')
        seed=dict(repository=repo,repo_id=int(p['id']),owner_id=int(p['owner']['id']),
                  discovery_filter='all_opened',query_layout='action_prewhere_v2',event_action='opened')
        for month in months:
            if month not in range(202601,202611):
                raise ValueError('Invalid discovery month')
            first=int(datetime(2026,month%100,1,tzinfo=zone).timestamp())
            last=min(cutoff,int(datetime(2026,month%100+1,1,tzinfo=zone).timestamp()))
            for start,end in gaps(first,last,covered[p['id']]):
                b=make_batch(seed,month,start,end)
                b['collection_reason']='incomplete_or_unqueried_opened_issue_time_gap'
                b['prior_newly_available']=productivity[repo]
                batches.append(b)
    batches.sort(key=lambda b:(-b['month'],-b['end_epoch'],-b['prior_newly_available'],b['repository']))
    assert len({b['key'] for b in batches})==len(batches)
    for b in batches:
        assert all(b['end_epoch']<=left or b['start_epoch']>=right for left,right in covered[b['repo_id']])
    value=dict(policy=POLICY,budget=DEFAULT_BUDGET,prepared_at=now.isoformat(),batches=batches,
        baseline_keys=[list(k) for k in sorted(baseline)],source_manifest=manifest,
        recommendation_target=10000,historical_inventory=9477,
        coverage_method='Subtract unsaturated successful all-opened scopes; cap past runs at run start; leave label-only scopes and failed/saturated scopes uncovered.',
        limitations=['New raw identities require GitHub screening and deduplication before recommendation.',
                     'Unqueried time gaps do not imply new or currently available Issue identities.',
                     'History inventory includes stale records; no claim of 10000 current recommendations.'])
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return dict(batches=len(batches),repositories=len({b['repo_id'] for b in batches}),
                months=dict(Counter(b['month'] for b in batches)),baseline_unique=len(baseline))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--months',type=int,nargs='+',default=[202610,202609])
    args=parser.parse_args()
    if len(set(args.months))!=len(args.months):raise ValueError('Duplicate months')
    print(json.dumps(prepare(args.output,args.months),ensure_ascii=False))
