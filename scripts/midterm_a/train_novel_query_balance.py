"""同预算类别平衡诊断：只按TRAIN查询类型调权，不用验证选权重或树数。"""
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics
import numpy as np
from pfirec_inspired_ranker import build,rows,sha,write,lgb,torch,evaluate


def balanced_query_weights(base,novel):
    base=np.asarray(base,dtype=float);novel=np.asarray(novel,dtype=bool)
    if not novel.any() or novel.all():raise ValueError('Both TRAIN query types required')
    total=base.sum();mass_true=base[novel].sum();mass_false=base[~novel].sum()
    return base*np.where(novel,.5*total/mass_true,.5*total/mass_false)


def run(dataset,features,profiles,output):
    torch.set_num_threads(1)
    with np.load(features/'text_features.npz',allow_pickle=False) as f:a={k:f[k].copy() for k in f.files}
    samples=rows(dataset/'samples.jsonl');train=[r for r in samples if r['split']=='train'];valid=[r for r in samples if r['split']=='validation']
    lin={r['profile_version']:r for r in rows(profiles/'lineage.jsonl')};candidates={r['issue_key']:r for r in rows(dataset/'candidates.jsonl')}
    tx,tc,names=build(train,a,lin,candidates);vx,vc,_=build(valid,a,lin,candidates)
    counts=Counter(r['github_numeric_id'] for r in train)
    base=np.asarray([1/counts[r['github_numeric_id']] for r in train])
    novel=np.asarray([not any(k[0]==r['positive_issue_key'].split('#')[0] for k in lin[r['profile_version']]['retained_X']) for r in train])
    balanced=balanced_query_weights(base,novel)
    output.mkdir(parents=True,exist_ok=False)
    protocol=dict(arms=['unweighted_fixed120','novel_balanced_fixed120'],seeds=list(range(20261102,20261107)),trees=120,
        selection='none: fixed120trees in both arms; no validation iteration/weight selection',
        balancing='start with inverse developer frequency; normalize TRAIN novel/seen query mass to50/50, preserve total mass',
        novel_definition='positive repository absent from retained public X, not a claim of first-ever contribution',
        train_queries=len(train),novel_train_queries=int(novel.sum()),base_novel_weight_fraction=float(base[novel].sum()/base.sum()),
        feature_names=names,input_sha256={str(p):sha(p) for p in [dataset/'samples.jsonl',dataset/'candidates.jsonl',features/'text_features.npz',profiles/'lineage.jsonl',Path(__file__)]},
        evaluate_test=False,independent_evaluation=False)
    write(output/'protocol.json',protocol);write(output/'train_query_weight.json',[dict(outcome_id=r['outcome_id'],novel=bool(n),base=float(w),balanced=float(b)) for r,n,w,b in zip(train,novel,base,balanced)])
    records=[]
    for seed in protocol['seeds']:
        for arm in protocol['arms']:
            weights=balanced if arm=='novel_balanced_fixed120' else base
            model=lgb.LGBMRanker(objective='lambdarank',n_estimators=120,learning_rate=.03,num_leaves=15,max_depth=4,min_child_samples=40,
                colsample_bytree=.9,reg_lambda=5,n_jobs=1,random_state=seed,verbosity=-1,deterministic=True,force_col_wise=True)
            model.fit(tx,np.tile([1]+[0]*20,len(train)),group=[21]*len(train),sample_weight=np.repeat(weights,21))
            prediction=model.booster_.predict(vx);ranks,met=evaluate(prediction,valid)
            d=output/f'{arm}-{seed}';d.mkdir();model.booster_.save_model(str(d/'model.txt'))
            restored=lgb.Booster(model_file=str(d/'model.txt'))
            assert np.isfinite(prediction).all() and ranks==evaluate(restored.predict(vx),valid)[0]
            write(d/'ranks.json',[dict(outcome_id=r['outcome_id'],github_numeric_id=r['github_numeric_id'],rank=rank) for r,rank in zip(valid,ranks)])
            record=dict(arm=arm,seed=seed,metrics=met);records.append(record);write(output/'results.json',records)
            print(json.dumps(record),flush=True)
    write(output/'summary.json',{arm:{k:statistics.mean(r['metrics'][k] for r in records if r['arm']==arm) for k in ['developer_macro_mrr','mrr','recall5']} for arm in protocol['arms']})
    assert all(sha(Path(p))==h for p,h in protocol['input_sha256'].items())


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('dataset','features','profiles','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();run(a.dataset,a.features,a.profiles,a.output)
