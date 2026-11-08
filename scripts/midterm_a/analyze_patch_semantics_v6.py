"""汇总共同获证文件的五 seed 开发验证对照，不选择发布模型。"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics
import sys
import torch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tmp/pr39-review/scripts/midterm_a'))
from diagnose_ranking import sha, write
from summarize_transfer_cold import ci


def run(root,semantics,assets,data_root):
    read=lambda p:json.loads(p.read_text())
    protocol=read(root/'protocol.json')
    records=read(root/'results.json')
    availability=read(root/'outcome_availability.json')
    covered={r['outcome_id'] for r in availability['validation'] if r['semantic_available']}
    assert len(records)==20 and len({(r['arm'],r['seed']) for r in records})==20
    assert len(list(root.glob('*/weights.pt')))==15
    for path in root.glob('*/weights.pt'):
        state=torch.load(path,weights_only=True,map_location='cpu')
        assert all(torch.isfinite(v).all() for v in state.values())
    assert all(sha(Path(p))==h for p,h in protocol['dependency_hashes'].items())
    assert sha(semantics/'profile_semantics.npz')==protocol['semantic_cache_sha256']
    assert sha(semantics/'protocol.json')==protocol['semantic_protocol_sha256']
    base=data_root/'data/midterm/working/rc1_training_inputs_20261106_v1'
    assert all(sha(base/name)==h for name,h in protocol['input_hashes'].items())
    for r in records:
        for relative,key in [(f"bottleneck-diagnostics-20261106-v1/original-{r['seed']}",'rc1_sha256'),
                             (f"history-fusion-v2-20261106-v1/learned_behavior-{r['seed']}",'v2_sha256'),
                             (f"transfer-cold-v3-20261107-v1/cosine-{r['seed']}",'v3_sha256')]:
            assert sha(assets/'runs'/relative/'weights.pt')==r[key]
        assert r['evaluate_test'] is False
    means={arm:{k:statistics.mean(r['metrics'][k] for r in records if r['arm']==arm)
                for k in ('mrr','recall5','ndcg5','pair_accuracy','developer_macro_mrr')}
           for arm in protocol['arms']}
    comparisons={}
    for arm,reference in [('path','v3_reference'),('content','v3_reference'),('patch','v3_reference'),('content','path'),('patch','path'),('patch','content')]:
        by=defaultdict(lambda:defaultdict(list))
        deltas=[]
        for seed in protocol['seeds']:
            before={r['outcome_id']:r for r in read(root/f'{reference}-{seed}/outcomes.json')}
            after=read(root/f'{arm}-{seed}/outcomes.json')
            assert len(after)==len(before)
            diff=[]
            for r in after:
                old=before[r['outcome_id']]
                delta=1/r['rank']-1/old['rank']
                diff.append(delta)
                for group,yes in [('overall',True),('no_history',old['history_count']==0),('new_repository',old['new_repository']),('low_coverage',old['coverage']<=3)]:
                    if yes:by[group][r['developer_key']].append(delta)
            deltas.append(statistics.mean(diff))
        comparisons[arm+'-'+reference]=dict(paired_seed_mrr=deltas,positive_seeds=sum(d>0 for d in deltas),
            developer_cluster_intervals={k:ci(v) for k,v in by.items()})
    summary=dict(means=means,comparisons=comparisons,
        subgroup_mrr={arm:{group:statistics.mean(r['metrics'][group]['mrr'] for r in records if r['arm']==arm)
                     for group in ('no_history','new_repository','low_coverage')} for arm in protocol['arms']},
        epochs=sum(r['epochs'] for r in records),seconds=sum(r['seconds'] for r in records),
        evaluate_test=False,external_evaluation_read=False,production_model_replaced=False,
        limitation='Repeated development validation and incompletely audited frozen base; not independent generalization evidence.')
    summary['outcome_coverage']={s:dict(total=len(rows),available=sum(r['semantic_available'] for r in rows)) for s,rows in availability.items()}
    summary['covered_validation_means']={}
    for arm in protocol['arms']:
        rows=[r for seed in protocol['seeds'] for r in read(root/f'{arm}-{seed}/outcomes.json') if r['outcome_id'] in covered]
        summary['covered_validation_means'][arm]=dict(mrr=statistics.mean(1/r['rank'] for r in rows),recall5=statistics.mean(r['rank']<=5 for r in rows))
    write(root/'analysis.json',summary)
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('root',type=Path)
    for n in ('semantics','assets','data-root'):
        p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args()
    run(a.root,a.semantics,a.assets,a.data_root)
