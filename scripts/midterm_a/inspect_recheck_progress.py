"""Read local checkpoints and list only missing verification work; no network or writes."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path

from assemble_recheck_evidence import latest_details, read_records
from screen_unified_teacher import BLOCKED, SCOPE, MANUAL_REVIEW
from oss_mentor.datasets.live_issues import fresh


def inspect(previous, directory, queue):
    rows=json.loads((previous/'source_issues.json').read_text('utf-8'))
    sources={f"{r['repo_name']}#{r['issue_number']}".casefold():r for r in rows}
    audit=json.loads((previous/'audit.json').read_text('utf-8'))
    targets={r['key'].casefold() for r in audit if r['decision'] in ('unverified','review_pending')}
    records,_=read_records(directory)
    details=latest_details(records,sources)
    timelines={}
    for r in records:
        if r['kind']=='timeline' and (r['key'] not in timelines or timelines[r['key']]['observed_at']<r['observed_at']):
            timelines[r['key']]=r
    missing=sorted(targets-set(details))
    if queue=='details':
        return [[f"{sources[k]['repo_name']}#{sources[k]['issue_number']}",str(sources[k]['issue_id']),str(sources[k]['repo_id'])] for k in missing]
    timeline_queue=[]
    for key,record in details.items():
        d=record.get('payload',{})
        manual=MANUAL_REVIEW.get(key)
        exact_manual=(manual and str(d.get('id'))==manual['issue_id'] and d.get('title')==manual['title'])
        if (not record.get('identity_match') or d.get('state')!='open' or d.get('locked') or d.get('assignees')
                or exact_manual or SCOPE.search(d.get('title',''))
                or any(BLOCKED.search(l['name']) for l in d.get('labels',[]))):
            continue
        t=timelines.get(key)
        if not t or not t['complete'] or not fresh(t,datetime.now(timezone.utc)):
            timeline_queue.append([key,d.get('timeline_url') or d['url']+'/timeline'])
    if queue=='timelines':
        return timeline_queue
    old=json.loads(gzip.decompress((previous/'connector_evidence.json.gz').read_bytes()))
    prs=old['pull_requests']
    for r in records:
        if r['kind']=='pull_request' and r.get('payload'):
            prs[r['url']]=dict(r['payload'],observed_at=r['observed_at'])
    refs=set()
    for t in timelines.values():
        if not t['complete']:
            continue
        for page in t['pages']:
            for event in page:
                url=((event.get('source') or {}).get('issue',{}).get('pull_request') or {}).get('url')
                if event.get('event')=='cross-referenced' and url:
                    refs.add(url)
    pulls=sorted(u for u in refs if u not in prs or prs[u].get('state') not in ('open','closed') or not fresh(prs[u],datetime.now(timezone.utc)))
    if queue=='pulls':
        return pulls
    return dict(targets=len(targets),attempted=len(details),not_attempted=len(missing),
                missing_by_repository=Counter(sources[k]['repo_name'] for k in missing),
                new_timelines=len(timelines),missing_timelines=len(timeline_queue),missing_prs=len(pulls))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('previous',type=Path)
    p.add_argument('directory',type=Path)
    p.add_argument('--queue',choices=['details','timelines','pulls','none'],default='none')
    print(json.dumps(inspect(**vars(p.parse_args())),ensure_ascii=False,separators=(',',':')))
