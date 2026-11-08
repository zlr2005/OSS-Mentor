"""补齐CODER同池实验的截断指标与固定输入分组，不训练或选择参数。"""
import argparse
import json
import math
from pathlib import Path
import statistics
from pfirec_inspired_ranker import rows,write,sha
from diagnose_pfirec_ranker import ci


def summarize(records):
    if not records:return None
    result=dict(outcomes=len(records),developers=len({r['github_numeric_id'] for r in records}))
    for k in [5,10,20]:
        result[f'recall{k}']=statistics.mean(r['rank']<=k for r in records)
        result[f'hit{k}']=result[f'recall{k}']
        result[f'mrr{k}']=statistics.mean(1/r['rank'] if r['rank']<=k else 0 for r in records)
        result[f'ndcg{k}']=statistics.mean(1/math.log2(r['rank']+1) if r['rank']<=k else 0 for r in records)
    return result


def run(output):
    p=json.loads((output/'protocol.json').read_text())
    assert all(sha(Path(path))==h for path,h in p['input_sha256'].items())
    dataset=next(Path(path).parent for path in p['input_sha256'] if Path(path).name=='samples.jsonl')
    samples=rows(dataset/'samples.jsonl');valid={r['outcome_id']:r for r in samples if r['split']=='validation'}
    train_users={r['github_numeric_id'] for r in samples if r['split']=='train'}
    profiles={r['profile_version']:r for r in rows(dataset.parent/'public_x_profiles_20261107_v1/profiles.jsonl')}
    groups={'overall':set(valid),'no_public_train_outcome':{k for k,r in valid.items() if r['github_numeric_id'] not in train_users},
            'low_profile_coverage':{k for k,r in valid.items() if sum(profiles[r['profile_version']]['observed_mask'])<=3}}
    result={}
    for arm in p['arms']:
        records=[json.loads((output/f'{arm}-{seed}/ranks.json').read_text()) for seed in p['seeds']]
        if 'target_repository_unseen_in_X' not in groups:
            groups['target_repository_unseen_in_X']={r['outcome_id'] for r in records[0] if r['novel']}
        result[arm]={}
        for name,ids in groups.items():
            mets=[summarize([r for r in rr if r['outcome_id'] in ids]) for rr in records]
            deltas=[]
            for seed,rr in zip(p['seeds'],records):
                ref={r['outcome_id']:r for r in json.loads((output/f'coder_v3-{seed}/ranks.json').read_text())}
                for r in rr:
                    if r['outcome_id'] in ids:deltas.append(dict(github_numeric_id=r['github_numeric_id'],delta=1/r['rank']-1/ref[r['outcome_id']]['rank']))
            result[arm][name]=dict(metrics={k:statistics.mean(m[k] for m in mets) for k in mets[0]},paired_vs_coder_v3=ci(deltas)) if mets[0] else None
    write(output/'extended_metrics.json',dict(arms=result,interpretation='Single known relevant target per outcome: Hit@K equals Recall@K. MRR@K zeroes ranks beyondK. Groups fixed by public input coverage, not errors. Reused development only.'))
    print(json.dumps({a:result[a]['target_repository_unseen_in_X'] for a in p['arms']},indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path);run(p.parse_args().output)
