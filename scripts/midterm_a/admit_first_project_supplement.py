"""补充TRAIN关系的公开证据/候选准入与小量增量编码，保留原版本。"""
import argparse
from collections import defaultdict,Counter
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import sys
import numpy as np
from audit_v5_publication import time
from assemble_public_text_dataset import choose_controls


def rows(p):return [json.loads(l) for l in p.read_text(encoding='utf-8').splitlines()]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def run(root,collection,events,dataset,features,profiles,assets,output):
    proposals=rows(collection/'proposed_samples.jsonl')
    evidence=defaultdict(list)
    for path in events.glob('batch_*.json'):
        for e in json.loads(path.read_text())['data']:
            evidence[e['repo_name'].casefold()+'#'+str(e['number'])].append(dict(event=e,file=str(path),sha256=sha(path)))
    candidates={c['issue_key']:c for c in rows(dataset/'candidates.jsonl')}
    samples=rows(dataset/'samples.jsonl');original_count=len(samples)
    ps={p['profile_version']:p for p in rows(profiles/'profiles.jsonl')};lin={r['profile_version']:r for r in rows(profiles/'lineage.jsonl')}
    known=defaultdict(set)
    base=root/'data/midterm/working/rc1_training_inputs_20261106_v1'
    old_profiles={p['profile_version']:p for p in rows(base/'profiles.jsonl') if p['split']=='train'}
    labels={r['outcome_id']:r for r in rows(base/'advanced_issue_outcomes.jsonl')}
    actual=json.loads((root/'data/midterm/working/patch_semantics_v6_runs_20261107_v2/outcome_availability.json').read_text())
    for r in actual['train']:known[old_profiles[r['profile_version']]['github_numeric_id']].add(labels[r['outcome_id']]['issue_key'])
    admitted=[];reasons=Counter()
    for p in proposals:
        proofs=[e for e in evidence[p['issue_key']] if time(e['event']['event_at'])<time(p['as_of'])]
        if not proofs:reasons['no_cutoff_public_Issue_event']+=1;continue
        if sha(Path(p['source_file']))!=p['source_sha256']:raise ValueError('Collection evidence changed')
        profile=ps[p['profile_version']]
        if profile['split']!='train' or time(profile['as_of'])!=time(p['as_of']):raise ValueError('Wrong profile split/cutoff')
        if any(k[0]==p['issue_key'].split('#')[0] for k in lin[p['profile_version']]['retained_X']):raise ValueError('Already seen target repository')
        old=candidates.get(p['issue_key']);snapshot=p['candidate_snapshot']
        if old and (time(old['available_from'])!=time(p['as_of']) or old['content_sha256']!=snapshot['content_sha256']):
            reasons['candidate_version_conflict_requires_versioned_cache']+=1;continue
        if p['issue_key'] in known[p['github_numeric_id']]:raise ValueError('Duplicate TRAIN positive')
        if not old:
            candidates[p['issue_key']]=dict(issue_key=p['issue_key'],title=snapshot['title'],body_text=snapshot['body_text'],content_sha256=snapshot['content_sha256'],
                available_from=p['as_of'],available_until=(time(p['as_of'])+timedelta(seconds=1)).isoformat(),
                source_url='https://github.com/'+p['issue_key'].replace('#','/issues/'),source_file=p['source_file'],source_sha256=p['source_sha256'],public_proofs=proofs,
                availability_policy='exact_verified_snapshot_second',schema_version='public-first-project-candidate-v1')
        admitted.append(dict(p,public_proofs=proofs));known[p['github_numeric_id']].add(p['issue_key'])
    for p in admitted:
        pool=[k for k,c in candidates.items() if time(c['available_from'])<=time(p['as_of'])<time(c['available_until'])]
        oid=p['pr_url']+'->'+p['issue_key']
        controls=choose_controls(pool,p['issue_key'],known[p['github_numeric_id']],oid)
        if len(controls)!=20:raise ValueError('Insufficient historical controls')
        samples.append(dict(split='train',profile_version=p['profile_version'],parent_profile_version=lin[p['profile_version']]['parent_profile_version'],
            github_numeric_id=p['github_numeric_id'],developer_key=p['developer_key'],as_of=p['as_of'],outcome_id=oid,positive_issue_key=p['issue_key'],comparison_issue_keys=controls,
            label_source=dict(source_url=p['pr_url'],interaction_at=p['interaction_at'],outcome_at=p['outcome_at'],source_file=p['source_file'],source_sha256=p['source_sha256'],verification_status='exact_numeric_PR_author_and_closing_event_reverified_in_collection')))
    output.mkdir(parents=True,exist_ok=False)
    for name,value in [('samples.jsonl',samples),('candidates.jsonl',list(candidates.values())),('admitted_supplement.jsonl',admitted)]:
        (output/name).write_text(''.join(json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n' for r in value),encoding='utf-8')
    with np.load(features/'text_features.npz',allow_pickle=False) as f:arrays={k:f[k].copy() for k in f.files}
    have=set(arrays['issue_keys'].astype(str));needed=sorted({k for r in samples for k in [r['positive_issue_key']]+r['comparison_issue_keys']}-have)
    if needed:
        os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1';os.environ['HF_HUB_CACHE']=str(assets/'encoding-assets/hf_hub')
        sys.path.insert(0,str(root/'tmp/pr39-review/CODER/src'))
        from coder_issue.models.text_encoder import FrozenTransformerEncoder
        import torch
        torch.set_num_threads(4)
        encoder=FrozenTransformerEncoder('sentence-transformers/all-MiniLM-L6-v2',revision='c21050a7ef692090620a6d037dd736908f9c7cf6',allow_download=False,max_tokens=256)
        texts=['[ISSUE] '+candidates[k]['title']+'\n'+candidates[k]['body_text'][:8192] for k in needed]
        vectors=encoder.encode(texts)
        arrays['issue_keys']=np.concatenate([arrays['issue_keys'],np.asarray(needed)])
        arrays['item']=np.concatenate([arrays['item'],vectors.astype(np.float32)])
    outfeatures=output/'features';outfeatures.mkdir()
    np.savez_compressed(outfeatures/'text_features.npz',**arrays)
    (outfeatures/'complete.json').write_text(json.dumps(dict(sha256=sha(outfeatures/'text_features.npz'))))
    (outfeatures/'protocol.json').write_text(json.dumps(dict(base_protocol_sha256=sha(features/'protocol.json'),base_cache_sha256=sha(features/'text_features.npz'),incremental_issues=needed,legacy_weights_reused=False)))
    summary=dict(admitted_train_samples=len(samples)-original_count,all_train_samples=sum(r['split']=='train' for r in samples),
        validation_samples=sum(r['split']=='validation' for r in samples),new_vector_count=len(needed),exclusions=dict(reasons),
        existing_samples_unchanged=True,validation_unchanged=True,prototype_training_ready=True,
        all_new_repositories_unseen_in_observed_X=True,actual_first_ever_project_contribution_proven=False,
        input_sha256={str(p):sha(p) for p in [collection/'proposed_samples.jsonl',dataset/'samples.jsonl',dataset/'candidates.jsonl',features/'text_features.npz',Path(__file__)]})
    (output/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('root','collection','events','dataset','features','profiles','assets','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();run(a.root,a.collection,a.events,a.dataset,a.features,a.profiles,a.assets,a.output)
