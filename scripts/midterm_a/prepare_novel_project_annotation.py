"""为全部稀缺TRAIN查询准备盲标，不把场景意图伪装成用户历史意愿。"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import torch
from train_public_text_curve import prepare


def rows(p):return [json.loads(l) for l in p.read_text(encoding='utf-8').splitlines()]


def run(dataset,features,profiles,output):
    lin={r['profile_version']:r for r in rows(profiles/'lineage.jsonl')}
    candidates={r['issue_key']:r for r in rows(dataset/'candidates.jsonl')}
    selected=[r for r in rows(dataset/'samples.jsonl') if r['split']=='train' and not any(k[0]==r['positive_issue_key'].split('#')[0] for k in lin[r['profile_version']]['retained_X'])]
    with np.load(features/'text_features.npz',allow_pickle=False) as f:a={k:f[k].copy() for k in f.files}
    data=prepare(selected,a)
    scores=torch.nn.functional.cosine_similarity(data['user'][:,:384,None].transpose(1,2),data['item'],dim=-1).numpy()
    annotations={};sampling=[]
    for r,s in zip(selected,scores):
        history=lin[r['profile_version']]['retained_X'];known={k[0] for k in history}
        keys=[r['positive_issue_key']]+r['comparison_issue_keys']
        hard=[j for j in range(1,21) if keys[j].split('#')[0] not in known]
        ids=[0]+sorted(hard,key=lambda j:(-s[j],keys[j]))[:2]
        for j in sorted(ids,key=lambda j:hashlib.sha256((r['outcome_id']+keys[j]).encode()).hexdigest()):
            key=hashlib.sha256((r['profile_version']+'\0'+keys[j]).encode()).hexdigest()
            c=candidates[keys[j]]
            if key not in annotations:
                annotations[key]=dict(annotation_id=key,profile_version=r['profile_version'],as_of=r['as_of'],group_ids=[],
                    scenario='new_project_exploration',scenario_basis='hypothetical annotation task; historical user intention not observed',
                    observed_history_paths=[dict(repository=k[0],path=k[2]) for k in history],
                    current_interest=None,declared_ability=None,issue_key=keys[j],title=c['title'],body=c['body_text'],content_sha256=c['content_sha256'],
                    required_skills=None,transferable_evidence=None,missing_evidence=None,suitability_grade=None,confidence=None,rationale=None,
                    rubric='0 unsuitable / 1 partial / 2 suitable / 3 strong / unknown insufficient evidence. Inspect cutoff-only evidence; never inspect solution PR. Suitability is not verified success.')
            annotations[key]['group_ids'].append(r['outcome_id'])
            sampling.append(dict(annotation_id=key,outcome_id=r['outcome_id'],selection='known TRAIN success calibration' if j==0 else 'hard TRAIN unlabeled comparator'))
    output.mkdir(parents=True,exist_ok=False)
    for name,value in [('blind_annotation.jsonl',list(annotations.values())),('sampling_provenance.jsonl',sampling)]:
        (output/name).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in value),encoding='utf-8')
    summary=dict(training_queries=len(selected),unique_annotation_pairs=len(annotations),pair_references=len(sampling),
        developers=len({r['github_numeric_id'] for r in selected}),completed_labels=0,training_ready=False,validation_or_test_mined=False)
    (output/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('dataset','features','profiles','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();run(a.dataset,a.features,a.profiles,a.output)
