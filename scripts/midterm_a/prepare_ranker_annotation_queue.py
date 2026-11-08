"""仅从TRAIN准备人工适配等级与训练难例；不生成伪造成功标签。"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import numpy as np
import torch
from train_public_text_curve import prepare


def rows(p):return [json.loads(l) for l in p.read_text(encoding='utf-8').splitlines()]


def run(dataset,features,profiles,output):
    samples=[r for r in rows(dataset/'samples.jsonl') if r['split']=='train']
    latest={}
    for r in sorted(samples,key=lambda r:(r['as_of'],r['outcome_id'])):latest[r['github_numeric_id']]=r
    selected=[latest[u] for u in sorted(latest,key=lambda u:hashlib.sha256(f'annotation-v1:{u}'.encode()).hexdigest())[:200]]
    with np.load(features/'text_features.npz',allow_pickle=False) as f:a={k:f[k].copy() for k in f.files}
    data=prepare(selected,a)
    scores=torch.nn.functional.cosine_similarity(data['user'][:,:384,None].transpose(1,2),data['item'],dim=-1).numpy()
    candidates={c['issue_key']:c for c in rows(dataset/'candidates.jsonl')}
    lin={r['profile_version']:r for r in rows(profiles/'lineage.jsonl')}
    records=[];sampling=[]
    for r,s in zip(selected,scores):
        keys=[r['positive_issue_key']]+r['comparison_issue_keys']
        ids=[0]+sorted(range(1,21),key=lambda j:(-s[j],keys[j]))[:2]
        # Hide whether a candidate is the known resolved issue from the annotation view.
        for j in sorted(ids,key=lambda j:hashlib.sha256((r['outcome_id']+keys[j]).encode()).hexdigest()):
            c=candidates[keys[j]]
            key=hashlib.sha256((r['profile_version']+'\0'+keys[j]).encode()).hexdigest()
            records.append(dict(annotation_id=key,query_id=r['profile_version'],as_of=r['as_of'],
                profile_evidence_paths=[dict(repository=k[0],path=k[2]) for k in lin[r['profile_version']]['retained_X']],
                stated_current_interest='unknown_not_inferred_from_commits',issue_key=keys[j],title=c['title'],body=c['body_text'],
                candidate_content_sha256=c['content_sha256'],suitability_grade=None,confidence=None,rationale=None,missing_information=None,
                allowed_grades={'0':'unsuitable on available evidence','1':'partially matches','2':'suitable','3':'strong match','unknown':'insufficient evidence'},
                instruction='Judge only cutoff-visible evidence; do not open later solution PRs. This is suitability, not verified success.'))
            sampling.append(dict(annotation_id=key,selection='known TRAIN positive calibration' if j==0 else 'TRAIN semantic hard unlabeled control',original_outcome_id=r['outcome_id']))
    output.mkdir(parents=True,exist_ok=False)
    for n,v in [('blind_annotation.jsonl',records),('sampling_provenance.jsonl',sampling)]:
        (output/n).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in v),encoding='utf-8')
    summary=dict(groups=len(selected),pairs=len(records),split='train_only',validation_or_test_mined=False,
        labels_completed=0,training_ready=False,
        next='double annotation on a subset, resolve disagreements; teacher-generated weak labels must be separately flagged and human-reviewed, never declared verified success')
    (output/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('dataset','features','profiles','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();run(a.dataset,a.features,a.profiles,a.output)
