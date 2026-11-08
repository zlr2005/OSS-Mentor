"""完整报告CODER神经头消融、分组与截断指标，避免将组合收益全归因于CODER。"""
import argparse
import json
from pathlib import Path
import statistics
from pfirec_inspired_ranker import rows,sha,write
from diagnose_pfirec_ranker import ci
from summarize_coder_primary import summarize


def run(output):
    p=json.loads((output/'protocol.json').read_text())
    assert all(sha(Path(path))==h for path,h in p['input_sha256'].items())
    base=Path('data/midterm/working');reference=base/'coder_primary_20261108_v2'
    samples=rows(base/'public_text_dataset_20261107_v2/samples.jsonl')
    valid={r['outcome_id']:r for r in samples if r['split']=='validation'}
    users={r['github_numeric_id'] for r in samples if r['split']=='train'}
    profiles={r['profile_version']:r for r in rows(base/'public_x_profiles_20261107_v1/profiles.jsonl')}
    allr={}
    for arm in p['arms']+['coder_v3','lambdamart_only','coder_v3_tree_aux']:
        directory=output if arm in p['arms'] else reference
        for seed in p['seeds']:allr[arm,seed]=json.loads((directory/f'{arm}-{seed}/ranks.json').read_text())
    first=allr[p['arms'][0],p['seeds'][0]]
    groups=dict(overall=set(valid),no_public_train_outcome={key for key,r in valid.items() if r['github_numeric_id'] not in users},
        low_profile_coverage={key for key,r in valid.items() if sum(profiles[r['profile_version']]['observed_mask'])<=3},
        target_repository_unseen_in_X={r['outcome_id'] for r in first if r['novel']})
    result={}
    for arm in p['arms']+['coder_v3','lambdamart_only','coder_v3_tree_aux']:
        result[arm]={}
        for group,ids in groups.items():
            mets=[summarize([r for r in allr[arm,s] if r['outcome_id'] in ids]) for s in p['seeds']]
            result[arm][group]={k:statistics.mean(m[k] for m in mets) for k in mets[0]} if mets[0] else None
    comparisons={}
    for left,right in [('interaction_head','coder_v3'),('interaction_head','pair_only_head'),('interaction_head','lambdamart_only'),('embedding_head','coder_v3')]:
        comparisons[left+' minus '+right]={}
        for group,ids in groups.items():
            delta=[]
            for seed in p['seeds']:
                rr={r['outcome_id']:r for r in allr[right,seed]}
                for r in allr[left,seed]:
                    if r['outcome_id'] in ids:delta.append(dict(github_numeric_id=r['github_numeric_id'],delta=1/r['rank']-1/rr[r['outcome_id']]['rank']))
            comparisons[left+' minus '+right][group]=ci(delta) if delta else None
    write(output/'analysis.json',dict(metrics=result,paired_macro_mrr=comparisons,
        caveat='Repeated development; pair-only retains CODER-mined TRAIN controls but no CODER score/vector at inference. Comparisons do not isolate head capacity or inherited training data.'))
    print(json.dumps(comparisons,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path);run(p.parse_args().output)
