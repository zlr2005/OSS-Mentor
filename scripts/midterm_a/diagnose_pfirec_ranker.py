"""锁定权重后诊断采样捷径、同仓库排序和条件性区间，不读test。"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics
import numpy as np
from pfirec_inspired_ranker import build,rows,write,sha,evaluate,lgb,torch


def ci(rows):
    by=defaultdict(list)
    for r in rows:by[r['github_numeric_id']].append(r['delta'])
    x=np.asarray([statistics.mean(v) for v in by.values()])
    ix=np.random.default_rng(20261107).integers(len(x),size=(4000,len(x)))
    return dict(delta=float(x.mean()),ci95=np.quantile(x[ix].mean(1),[.025,.975]).tolist(),developers=len(x))


def rank_rows(scores,samples):
    ranks,_=evaluate(scores,samples);matrix=scores.reshape(len(samples),21);result=[]
    for r,rank,s in zip(samples,ranks,matrix):
        repo=r['positive_issue_key'].split('#')[0]
        ids=[i+1 for i,k in enumerate(r['comparison_issue_keys']) if k.split('#')[0]==repo]
        result.append(dict(outcome_id=r['outcome_id'],github_numeric_id=r['github_numeric_id'],rank=rank,
            same_rank=1+sum(s[j]>=s[0] for j in ids) if ids else None))
    return result


def run(root,dataset,features,profiles,output):
    torch.set_num_threads(1);output.mkdir(parents=True,exist_ok=False)
    protocol=json.loads((root/'protocol.json').read_text())
    assert all(sha(Path(p))==h for p,h in protocol['input_sha256'].items())
    with np.load(features/'text_features.npz',allow_pickle=False) as f:a={k:f[k].copy() for k in f.files}
    allsamples=rows(dataset/'samples.jsonl');train=[r for r in allsamples if r['split']=='train'];valid=[r for r in allsamples if r['split']=='validation']
    lin={r['profile_version']:r for r in rows(profiles/'lineage.jsonl')};candidates={r['issue_key']:r for r in rows(dataset/'candidates.jsonl')}
    tx,tc,names=build(train,a,lin,candidates);vx,vc,_=build(valid,a,lin,candidates)
    pmap={p['profile_version']:p for p in rows(profiles/'profiles.jsonl')}
    grouped=defaultdict(list)
    for v in {r['profile_version'] for r in valid}:grouped[pmap[v]['as_of']].append(v)
    shuffled=dict(lin)
    for group in grouped.values():
        group=sorted(group)
        for i,v in enumerate(group):
            other=next(group[(i+j)%len(group)] for j in range(1,len(group)) if pmap[group[(i+j)%len(group)]]['github_numeric_id']!=pmap[v]['github_numeric_id'])
            shuffled[v]=lin[other]
    sx,_,_=build(valid,a,shuffled,candidates)
    baseline=rank_rows(vc,valid);results=[];deltas=defaultdict(list)
    for arm in protocol['arms']:
        for seed in protocol['seeds']:
            d=root/f'{arm}-{seed}';model=lgb.Booster(model_file=str(d/'model.txt'))
            delta=model.predict(vx);score=10*vc.reshape(-1)+np.clip(delta,-1,1) if arm=='cosine_residual' else delta
            ranked=rank_rows(score,valid)
            assert [r['rank'] for r in ranked]==[r['rank'] for r in json.loads((d/'ranks.json').read_text())]
            _,met=evaluate(score,valid)
            for old,new in zip(baseline,ranked):
                deltas[arm].append(dict(github_numeric_id=new['github_numeric_id'],delta=1/new['rank']-1/old['rank']))
            sd=model.predict(sx);shufscore=10*vc.reshape(-1)+np.clip(sd,-1,1) if arm=='cosine_residual' else sd
            _,shuf=evaluate(shufscore,valid)
            results.append(dict(arm=arm,seed=seed,metrics=met,history_shuffled=shuf))
    # Issue-only diagnostic uses zeroed cosine/history and the identical tree budget.
    # No deployment candidate is selected from this additional ablation.
    xt=tx.copy();xv=vx.copy();xt[:,:7]=0;xv[:,:7]=0
    labels=np.tile([1]+[0]*20,len(train));counts=defaultdict(int)
    for r in train:counts[r['github_numeric_id']]+=1
    weights=np.repeat([1/counts[r['github_numeric_id']] for r in train],21)
    issueonly=[]
    for seed in protocol['seeds']:
        m=lgb.LGBMRanker(objective='lambdarank',n_estimators=120,learning_rate=.03,num_leaves=15,max_depth=4,min_child_samples=40,
            colsample_bytree=.9,reg_lambda=5,n_jobs=1,random_state=seed,verbosity=-1,deterministic=True,force_col_wise=True)
        m.fit(xt,labels,group=[21]*len(train),sample_weight=weights)
        best=None
        for n in protocol['selection_iterations']:
            _,met=evaluate(m.booster_.predict(xv,num_iteration=n),valid)
            if best is None or met['developer_macro_mrr']>best['developer_macro_mrr']:best=met
        issueonly.append(best)
    summary=dict(paired_macro_CI={arm:ci(values) for arm,values in deltas.items()},
        means={arm:dict(same_repository_mrr=statistics.mean(r['metrics']['same_repo_diagnostic']['mrr'] for r in results if r['arm']==arm),
            history_shuffled_macro_mrr=statistics.mean(r['history_shuffled']['developer_macro_mrr'] for r in results if r['arm']==arm)) for arm in protocol['arms']},
        issue_only_means={k:statistics.mean(r[k] for r in issueonly) for k in ('developer_macro_mrr','mrr','recall5')},
        issue_only_same_repository_mrr=statistics.mean(r['same_repo_diagnostic']['mrr'] for r in issueonly),
        same_repository_baseline=json.loads((root/'baseline.json').read_text())['same_repo_diagnostic'],
        caveat='Same-repository diagnostic retains only existing same-repo sampled controls (mean2.55), not a new full candidate benchmark; history shuffling leaves cosine unchanged; validation reused; issue-only ablation is diagnostic.')
    write(output/'diagnostics.json',summary);write(output/'records.json',results)
    print(json.dumps(summary))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('root','dataset','features','profiles','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();run(a.root,a.dataset,a.features,a.profiles,a.output)
