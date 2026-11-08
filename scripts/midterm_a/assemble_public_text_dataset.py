"""公开 X 新版本与公开历史候选联接；为全新文本模型生成训练/验证样本。"""
import argparse
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path
from datetime import timedelta
from audit_v5_publication import time


def rows(p):return [json.loads(l) for l in p.read_text(encoding='utf-8').splitlines()]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def choose_controls(pool,positive,excluded,outcome_id):
    eligible=[k for k in pool if k!=positive and k not in excluded]
    ordered=sorted(eligible,key=lambda k:hashlib.sha256((outcome_id+'\0'+k).encode()).hexdigest())
    same=[k for k in ordered if k.split('#')[0]==positive.split('#')[0]][:5]
    return (same+[k for k in ordered if k not in same])[:20]


def dict_nodes(value):
    if isinstance(value,dict):
        yield value
        for v in value.values():yield from dict_nodes(v)
    elif isinstance(value,list):
        for v in value:yield from dict_nodes(v)


def label_verified(label,uid,root,cache):
    path=root/'data/midterm/working/advanced_adapter_backfill_2024_2026_v1'/label['evidence_ref']
    if path not in cache:
        source=json.loads(path.read_text(encoding='utf-8-sig'))
        if hashlib.sha256((json.dumps(source['payload'],ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode()).hexdigest()!=source['payload_sha256']:
            raise ValueError('Label source checksum mismatch')
        cache[path]=list(dict_nodes(source['payload']))
    for pr in cache[path]:
        if pr.get('url')!=label['source_url'] or not pr.get('merged'):continue
        author=pr.get('author') or {}
        if author.get('databaseId')!=uid or time(pr['createdAt'])!=time(label['interaction_at']):continue
        for issue in (pr.get('closingIssuesReferences') or {}).get('nodes',[]):
            key=issue['repository']['nameWithOwner'].casefold()+'#'+str(issue['number'])
            if key!=label['issue_key']:continue
            for event in (issue.get('timelineItems') or {}).get('nodes',[]):
                if event.get('__typename')=='ClosedEvent' and (event.get('closer') or {}).get('url')==pr['url'] and time(event['createdAt'])==time(label['outcome_at']) and time(pr['mergedAt'])<=time(label['outcome_at']):return True
    return False


def run(root,profiles,audit,output):
    output.mkdir(parents=True,exist_ok=False)
    events=defaultdict(list)
    for path in audit.glob('batch_*.json'):
        body=json.loads(path.read_text())
        if len(body['data'])>=200:raise ValueError('Possibly truncated public event response')
        for e in body['data']:events[e['repo_name'].casefold()+'#'+str(e['number'])].append((e,path))
    checks=json.loads((audit/'checks.json').read_text())
    candidate_decisions=[];allowed=set()
    for c in checks:
        proofs=[dict(event=e,source=str(path),source_sha256=sha(path)) for e,path in events[c['issue_key']] if time(e['event_at'])<time(c['cutoff'])]
        passed=c['local_replay_passed'] and bool(proofs)
        if passed:allowed.add(c['issue_key'])
        candidate_decisions.append(dict(c,public_before_cutoff=bool(proofs),admitted=passed,public_proofs=proofs))
    base=root/'data/midterm/working/rc1_training_inputs_20261106_v1'
    candidates=[c for c in rows(base/'historical_issue_candidates.jsonl') if c['issue_key'] in allowed]
    pools=defaultdict(list)
    for c in candidates:pools[time(c['available_from'])].append(c['issue_key'])
    lineage={r['parent_profile_version']:r for r in rows(profiles/'lineage.jsonl')}
    newprofiles={r['profile_version']:r for r in rows(profiles/'profiles.jsonl')}
    oldprofiles={r['profile_version']:r for r in rows(base/'profiles.jsonl') if r['split'] in ('train','validation')}
    labels={r['outcome_id']:r for r in rows(base/'advanced_issue_outcomes.jsonl')}
    actual=json.loads((root/'data/midterm/working/patch_semantics_v6_runs_20261107_v2/outcome_availability.json').read_text())
    known=defaultdict(set)
    for r in actual['train']:
        known[oldprofiles[r['profile_version']]['github_numeric_id']].add(labels[r['outcome_id']]['issue_key'])
    samples=[];rejected=[];seen=set();label_cache={}
    for split in ('train','validation'):
        for r in sorted(actual[split],key=lambda r:(oldprofiles[r['profile_version']]['as_of'],r['outcome_id'])):
            old=oldprofiles[r['profile_version']];label=labels[r['outcome_id']]
            link=lineage.get(r['profile_version'])
            reason=None
            if not link:reason='no_public_X'
            cutoff=time(old['as_of']);pool=pools.get(cutoff,[])
            if reason is None and not label_verified(label,old['github_numeric_id'],root,label_cache):reason='closing_PR_label_not_reverified'
            if reason is None and label['issue_key'] not in pool:reason='positive_without_public_historical_candidate'
            key=old['github_numeric_id'],label['issue_key']
            if reason is None and key in seen:reason='duplicate_user_issue_supervision'
            controls=choose_controls(pool,label['issue_key'],known[old['github_numeric_id']],r['outcome_id']) if reason is None else []
            if reason is None and len(controls)!=20:reason='fewer_than20_controls'
            if reason:
                rejected.append(dict(split=split,outcome_id=r['outcome_id'],reason=reason));continue
            seen.add(key)
            p=newprofiles[link['profile_version']]
            if not (cutoff<time(label['interaction_at'])<=time(label['outcome_at'])<=cutoff+timedelta(days=90)):raise ValueError('Outcome chronology invalid')
            samples.append(dict(split=split,profile_version=p['profile_version'],parent_profile_version=r['profile_version'],
                github_numeric_id=p['github_numeric_id'],developer_key=p['developer_key'],as_of=p['as_of'],outcome_id=r['outcome_id'],
                positive_issue_key=label['issue_key'],comparison_issue_keys=controls,label_source=label))
    for name,value in [('candidates.jsonl',candidates),('samples.jsonl',samples),('excluded_samples.jsonl',rejected)]:
        (output/name).write_text(''.join(json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n' for r in value),encoding='utf-8')
    (output/'candidate_decisions.json').write_text(json.dumps(candidate_decisions,indent=2),encoding='utf-8')
    summary=dict(version='public-text-supervision-20261107-v1',candidates=len(candidates),
        samples=dict(Counter(r['split'] for r in samples)),developers={s:len({r['github_numeric_id'] for r in samples if r['split']==s}) for s in ('train','validation')},
        exclusions=dict(Counter(r['reason'] for r in rejected)),
        policy='new partial-history profile version; known TRAIN positives excluded from controls; no validation/test labels used in TRAIN control mining',
        legacy_graph_reused=False,training_ready_for_fresh_text_baseline=True,legacy_coder_training_ready=False,
        independently_tested=False,input_sha256={str(p):sha(p) for p in [profiles/'profiles.jsonl',profiles/'lineage.jsonl',audit/'checks.json',base/'historical_issue_candidates.jsonl']})
    (output/'summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('root','profiles','audit','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();run(a.root,a.profiles,a.audit,a.output)
