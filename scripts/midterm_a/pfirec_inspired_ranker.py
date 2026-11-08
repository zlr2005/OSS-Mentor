"""PFIRec启发的成对特征/LambdaMART小规模开发对照，非论文复现。"""
import argparse
from collections import Counter,defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys
import numpy as np
import torch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tmp/research_lightgbm_4_6_0'))
import lightgbm as lgb
from train_public_text_curve import prepare,metrics


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def rows(p):return [json.loads(l) for l in p.read_text(encoding='utf-8').splitlines()]
def write(p,v):p.write_text(json.dumps(v,indent=2),encoding='utf-8')


def history_features(history,issue_key,text):
    """只读原截止前获证X；不读当前任务的解决PR、实际修改文件或结果。"""
    repo=issue_key.split('#')[0]
    commits={k[0]+'@'+k[1] for k in history}
    same={k[1] for k in history if k[0]==repo}
    paths={k[2] for k in history}
    names={Path(p).name.casefold() for p in paths if Path(p).suffix and Path(p).name.casefold() not in {'readme.md','license','package.json'}}
    text=text.casefold()
    matches=sum(name in text for name in names)
    return [float(bool(same)),len(same)/max(len(commits),1),math.log1p(len(same)),
            math.log1p(len(commits)),math.log1p(len(paths)),math.log1p(matches)]


def build(samples,features,lineage,candidates):
    data=prepare(samples,features)
    cosine=torch.nn.functional.cosine_similarity(data['user'][:,:384,None].transpose(1,2),data['item'],dim=-1).numpy()
    out=[];names=['cosine','repo_seen','repo_commit_fraction','log_repo_commits','log_all_commits','log_paths','log_filename_matches',
        'log_title_chars','log_body_chars','log_lines','log_code_fences','log_urls','is_bug_text','is_docs_text','is_test_text','is_feature_text']
    for r,c in zip(samples,cosine):
        hist=lineage[r['profile_version']]['retained_X']
        for k,sim in zip([r['positive_issue_key']]+r['comparison_issue_keys'],c):
            item=candidates[k];title=item['title'];body=item['body_text'];text=title+'\n'+body
            terms=text.casefold()
            value=[float(sim)]+history_features(hist,k,text)+[
                math.log1p(len(title)),math.log1p(len(body)),math.log1p(text.count('\n')),
                math.log1p(text.count('```')),math.log1p(len(re.findall(r'https?://',text))),
                float(bool(re.search(r'\b(bug|error|crash|fail|regression)\b',terms))),
                float(bool(re.search(r'\b(doc|docs|documentation|readme|typo)\b',terms))),
                float(bool(re.search(r'\b(test|tests|coverage)\b',terms))),
                float(bool(re.search(r'\b(feature|implement|support|enhancement)\b',terms)))]
            out.append(value)
    matrix=np.asarray(out,dtype=np.float32)
    assert matrix.shape==(len(samples)*21,len(names)) and np.isfinite(matrix).all()
    return matrix,cosine,names


def evaluate(score,samples):
    score=score.reshape(len(samples),21)
    ranks=(1+(score[:,1:]>=score[:,:1]).sum(1)).tolist()
    result=metrics(ranks,samples)
    same=[]
    for s,r in zip(score,samples):
        repo=r['positive_issue_key'].split('#')[0]
        ids=[i+1 for i,k in enumerate(r['comparison_issue_keys']) if k.split('#')[0]==repo]
        if ids:same.append((1+sum(s[i]>=s[0] for i in ids),len(ids)))
    result['same_repo_diagnostic']=dict(outcomes=len(same),mrr=statistics.mean(1/r for r,n in same) if same else None,
        mean_controls=statistics.mean(n for r,n in same) if same else None)
    return ranks,result


def run(dataset,features,profiles,output):
    torch.set_num_threads(1)
    with np.load(features/'text_features.npz',allow_pickle=False) as f:arrays={k:f[k].copy() for k in f.files}
    samples=rows(dataset/'samples.jsonl');train=[r for r in samples if r['split']=='train'];valid=[r for r in samples if r['split']=='validation']
    lineage={r['profile_version']:r for r in rows(profiles/'lineage.jsonl')};candidates={r['issue_key']:r for r in rows(dataset/'candidates.jsonl')}
    tx,tc,names=build(train,arrays,lineage,candidates);vx,vc,_=build(valid,arrays,lineage,candidates)
    labels=np.tile([1]+[0]*20,len(train));groups=[21]*len(train)
    counts=Counter(r['github_numeric_id'] for r in train)
    weights=np.repeat([1/counts[r['github_numeric_id']] for r in train],21)
    output.mkdir(parents=True,exist_ok=False)
    protocol=dict(seeds=list(range(20261102,20261107)),arms=['from_scratch','cosine_residual'],features=names,
        model='LightGBM4.6 LambdaRank',trees=120,selection_iterations=list(range(10,121,10)),
        primary='developer_macro_mrr',learning_rate=.03,num_leaves=15,max_depth=4,min_child_samples=40,
        colsample_bytree=.9,reg_lambda=5,baseline_scale=10,residual_prediction_cap=1.,
        feature_scope='cutoff-safe retained X and historical Issue title/body only; no IDs as direct model features, no resolving PR fields',
        training_labels='positive vs sampled-unlabeled ordinal comparison; raw scores are not calibrated success probabilities',
        limitation='PFIRec-inspired adaptation, not full reproduction; development validation reused',
        input_sha256={str(p):sha(p) for p in [dataset/'samples.jsonl',dataset/'candidates.jsonl',features/'text_features.npz',profiles/'lineage.jsonl',Path(__file__)]},
        evaluate_test=False,external_evaluation_read=False)
    write(output/'protocol.json',protocol)
    baseline_ranks,baseline=evaluate(vc,valid);write(output/'baseline.json',baseline)
    assert abs(baseline['developer_macro_mrr']-.5364244311929497)<1e-12
    records=[]
    for seed in protocol['seeds']:
        for arm in protocol['arms']:
            residual=arm=='cosine_residual'
            model=lgb.LGBMRanker(objective='lambdarank',n_estimators=120,learning_rate=.03,num_leaves=15,max_depth=4,
                min_child_samples=40,colsample_bytree=.9,reg_lambda=5,n_jobs=1,random_state=seed,verbosity=-1,deterministic=True,force_col_wise=True)
            model.fit(tx,labels,group=groups,sample_weight=weights,init_score=(10*tc.reshape(-1) if residual else None))
            history=[];best=None
            for iteration in protocol['selection_iterations']:
                delta=model.predict(vx,num_iteration=iteration)
                score=10*vc.reshape(-1)+np.clip(delta,-1,1) if residual else delta
                ranks,result=evaluate(score,valid)
                history.append(dict(iteration=iteration,metrics=result))
                if best is None or result['developer_macro_mrr']>best[0]:best=(result['developer_macro_mrr'],iteration,ranks,result)
            directory=output/f'{arm}-{seed}';directory.mkdir()
            model.booster_.save_model(str(directory/'model.txt'),num_iteration=best[1])
            restored=lgb.Booster(model_file=str(directory/'model.txt'))
            delta=restored.predict(vx);score=10*vc.reshape(-1)+np.clip(delta,-1,1) if residual else delta
            assert np.isfinite(score).all() and evaluate(score,valid)[0]==best[2]
            write(directory/'history.json',history)
            write(directory/'ranks.json',[dict(outcome_id=r['outcome_id'],github_numeric_id=r['github_numeric_id'],rank=rank) for r,rank in zip(valid,best[2])])
            write(directory/'importance.json',dict(zip(names,map(float,model.feature_importances_))))
            record=dict(seed=seed,arm=arm,iteration=best[1],metrics=best[3],delta_macro_mrr=best[0]-baseline['developer_macro_mrr'])
            records.append(record);write(output/'results.json',records);print(json.dumps(record),flush=True)
    means={arm:{k:statistics.mean(r['metrics'][k] for r in records if r['arm']==arm) for k in ('developer_macro_mrr','mrr','recall5','ndcg5')} for arm in protocol['arms']}
    write(output/'summary.json',dict(baseline=baseline,means=means,evaluate_test=False,independent_evaluation=False))
    assert all(sha(Path(p))==h for p,h in protocol['input_sha256'].items())


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('dataset','features','profiles','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();run(a.dataset,a.features,a.profiles,a.output)
