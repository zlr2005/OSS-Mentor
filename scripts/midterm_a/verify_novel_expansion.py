"""检查新增TRAIN关系、旧样本/向量不变性与同预算权重对照。"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
from datetime import timedelta
import numpy as np
from audit_v5_publication import time
from audit_historical_candidate_evidence import canonical,issue_nodes,check_snapshot
from assemble_public_text_dataset import dict_nodes
from diagnose_pfirec_ranker import ci


def rows(p):return [json.loads(l) for l in p.read_text(encoding='utf-8').splitlines()]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def run(parent,new,profiles,expanded,output):
    before=rows(parent/'samples.jsonl');after=rows(new/'samples.jsonl')
    assert after[:len(before)]==before
    additions=after[len(before):]
    assert len(additions)==31 and all(r['split']=='train' for r in additions)
    assert len({(r['github_numeric_id'],r['positive_issue_key']) for r in after})==len(after)
    with np.load(parent/'features/text_features.npz',allow_pickle=False) as a,np.load(new/'features/text_features.npz',allow_pickle=False) as b:
        for k in ('profile_versions','user','values','masks'):assert np.array_equal(a[k],b[k])
        assert np.array_equal(a['item'],b['item'][:len(a['item'])])
        assert np.array_equal(a['issue_keys'],b['issue_keys'][:len(a['issue_keys'])])
    admitted=rows(new/'admitted_supplement.jsonl');cache={}
    for p in admitted:
        source_path=Path(p['source_file'])
        assert sha(source_path)==p['source_sha256']
        if source_path not in cache:
            cache[source_path]=json.loads(source_path.read_text(encoding='utf-8'))
        source=cache[source_path]
        assert hashlib.sha256(canonical(source['payload'])).hexdigest()==source['payload_sha256']
        assert time(p['as_of'])<time(p['interaction_at'])<=time(p['outcome_at'])<=time(p['as_of'])+timedelta(days=90)
        for proof in p['public_proofs']:
            assert sha(Path(proof['file']))==proof['sha256']
            e=proof['event'];assert e['repo_name'].casefold()+'#'+str(e['number'])==p['issue_key']
            assert time(e['event_at'])<time(p['as_of'])
        assert any(check_snapshot(p['candidate_snapshot'],issue) is None for issue in issue_nodes(source['payload'],p['issue_key']))
        prs=[r for r in dict_nodes(source['payload']) if r.get('url')==p['pr_url'] and r.get('merged')]
        valid_label=False
        for pr in prs:
            if (pr.get('author') or {}).get('databaseId')!=p['github_numeric_id'] or time(pr['createdAt'])!=time(p['interaction_at']):continue
            for issue in (pr.get('closingIssuesReferences') or {}).get('nodes',[]):
                if issue['repository']['nameWithOwner'].casefold()+'#'+str(issue['number'])!=p['issue_key']:continue
                for e in issue['timelineItems']['nodes']:
                    if e.get('__typename')=='ClosedEvent' and (e.get('closer') or {}).get('url')==p['pr_url'] and time(e['createdAt'])==time(p['outcome_at']) and time(pr['mergedAt'])<=time(p['outcome_at']):valid_label=True
        assert valid_label
    train_positives=defaultdict(set)
    for r in after:
        if r['split']=='train':train_positives[r['github_numeric_id']].add(r['positive_issue_key'])
    for r in after:
        assert len(r['comparison_issue_keys'])==len(set(r['comparison_issue_keys']))==20
        if r['split']=='train':assert not(set(r['comparison_issue_keys'])&train_positives[r['github_numeric_id']])
    lin={r['profile_version']:r for r in rows(profiles/'lineage.jsonl')}
    rare={r['outcome_id'] for r in after if r['split']=='validation' and not any(k[0]==r['positive_issue_key'].split('#')[0] for k in lin[r['profile_version']]['retained_X'])}
    records={};group_ci={}
    for arm in ('unweighted_fixed120','novel_balanced_fixed120'):
        records[arm]=[r for seed in range(20261102,20261107) for r in json.loads((expanded/f'{arm}-{seed}.json').read_text())]
    deltas=[dict(outcome_id=a['outcome_id'],github_numeric_id=a['github_numeric_id'],delta=1/a['rank']-1/b['rank']) for a,b in zip(records['novel_balanced_fixed120'],records['unweighted_fixed120'])]
    for label,subset in [('overall',deltas),('target_unseen_in_X',[r for r in deltas if r['outcome_id'] in rare])]:group_ci[label]=ci(subset)
    result=dict(new_train_samples=len(additions),old_samples_unchanged=True,old_vectors_unchanged=True,
        label_author_time_and_publication_rechecked=True,known_TRAIN_positives_in_controls=0,
        novel_train_queries=sum(r['split']=='train' and not any(k[0]==r['positive_issue_key'].split('#')[0] for k in lin[r['profile_version']]['retained_X']) for r in after),
        balanced_minus_unweighted_CI=group_ci)
    output.write_text(json.dumps(result,indent=2));print(json.dumps(result))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('parent','new','profiles','expanded','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();run(a.parent,a.new,a.profiles,a.expanded,a.output)
