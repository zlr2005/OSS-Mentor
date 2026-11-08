"""多正例监督的固定分组与配对开发评价，不更改验证目标定义。"""
import argparse
import json
from pathlib import Path
import statistics
from pfirec_inspired_ranker import rows,sha,write
from diagnose_pfirec_ranker import ci
from summarize_coder_primary import summarize


def run(output):
    p=json.loads((output/'protocol.json').read_text());base=Path('data/midterm/working')
    assert all(sha(Path(path))==h for path,h in p['input_sha256'].items())
    locations={arm:output for arm in p['arms']}
    locations.update(gated_fusion=base/'coder_gated_20261108_v1',coder_v3=base/'coder_primary_20261108_v2',lambdamart_only=base/'coder_primary_20261108_v2')
    data={(arm,s):json.loads((directory/f'{arm}-{s}/ranks.json').read_text()) for arm,directory in locations.items() for s in p['seeds']}
    samples=rows(base/'public_text_dataset_20261107_v2/samples.jsonl');valid={r['outcome_id']:r for r in samples if r['split']=='validation'}
    trainusers={r['github_numeric_id'] for r in samples if r['split']=='train'}
    profiles={r['profile_version']:r for r in rows(base/'public_x_profiles_20261107_v1/profiles.jsonl')}
    first=data[p['arms'][0],p['seeds'][0]]
    groups=dict(overall=set(valid),no_public_train_outcome={k for k,r in valid.items() if r['github_numeric_id'] not in trainusers},
        low_profile_coverage={k for k,r in valid.items() if sum(profiles[r['profile_version']]['observed_mask'])<=3},
        target_repository_unseen_in_X={r['outcome_id'] for r in first if r['novel']})
    result={}
    for arm in locations:
        result[arm]={}
        for group,ids in groups.items():
            mets=[summarize([r for r in data[arm,s] if r['outcome_id'] in ids]) for s in p['seeds']]
            result[arm][group]={k:statistics.mean(m[k] for m in mets) for k in mets[0]} if mets[0] else None
    comparisons={}
    for arm in p['arms']:
        comparisons[arm+' minus gated_fusion']={}
        for group,ids in groups.items():
            delta=[]
            for seed in p['seeds']:
                ref={r['outcome_id']:r for r in data['gated_fusion',seed]}
                for r in data[arm,seed]:
                    if r['outcome_id'] in ids:delta.append(dict(github_numeric_id=r['github_numeric_id'],delta=1/r['rank']-1/ref[r['outcome_id']]['rank']))
            comparisons[arm+' minus gated_fusion'][group]=ci(delta) if delta else None
    write(output/'analysis.json',dict(metrics=result,paired_macro_mrr=comparisons,independent_evaluation=False,
        caveat='Repeated development; only TRAIN additional positives; original single-target validation and pools unchanged. No checkpoint or loss-weight search. Additional references are not new independent successes.'))
    print(json.dumps(comparisons,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path);run(p.parse_args().output)
