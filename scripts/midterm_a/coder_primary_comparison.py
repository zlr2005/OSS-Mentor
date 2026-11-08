"""CODER主线同池比较：冻结底座/已有增强，树作为对照及有界辅助。仅开发验证。"""
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics
import time
import numpy as np
import patch_semantics_v6 as old
from pfirec_inspired_ranker import build, rows, sha, write, lgb
from evaluate_expanded_pool import rank_of
from train_public_text_curve import metrics
from diagnose_pfirec_ranker import ci


def bounded_score(base, delta, cap=.25):
    if not np.isfinite(base).all() or not np.isfinite(delta).all():
        raise ValueError('Nonfinite scores')
    return base+np.clip(delta,-cap,cap)


def standardize(values):
    return (values-values.mean())/max(float(values.std()),1e-8)


def run(root,assets,output):
    torch=old.torch;torch.set_num_threads(1)
    base=root/'data/midterm/working'; inputs=base/'rc1_training_inputs_20261106_v1'
    frozen=old.load_frozen_inputs(assets/'encoding-assets/coder_bundle/data/models/gfibot-m0-v1.1/coder/coder-full-search8-v1',inputs/'historical_issue_embeddings.npz')
    config=old.AdvancedTrainingConfig.from_path(assets/'runs/public-supplement-20261106-v2/training_config.json')
    splits,_,audit=old.prepare_training_data(profiles_path=inputs/'profiles.jsonl',outcomes_path=inputs/'advanced_issue_outcomes.jsonl',candidates_path=inputs/'historical_issue_candidates.jsonl',frozen=frozen,config=config)
    profiles={p['profile_version']:p for p in rows(inputs/'profiles.jsonl')}
    dataset=base/'public_text_dataset_20261107_v2'; samples=rows(dataset/'samples.jsonl')
    train=[r for r in samples if r['split']=='train']; valid=[r for r in samples if r['split']=='validation']
    candidates={r['issue_key']:r for r in rows(dataset/'candidates.jsonl')}
    lin={r['profile_version']:r for r in rows(base/'public_x_profiles_20261107_v1/lineage.jsonl')}
    plans=json.loads((base/'expanded_pool_20261107_v2/candidate_plans.json').read_text())
    required={k for keys in plans.values() for k in keys}
    required|={k for r in train for k in [r['positive_issue_key']]+r['comparison_issue_keys']}
    missing=sorted(required-set(frozen.issue_embeddings))
    output.mkdir(parents=True,exist_ok=False)
    write(output/'coverage.json',dict(required_candidates=len(required),missing_embeddings=missing,validation_outcomes=len(valid),target_coverage=sum(r['positive_issue_key'] in frozen.issue_embeddings for r in valid)/len(valid)))
    if missing: raise ValueError('Missing CODER vectors: do not shrink cohort silently; see coverage.json')
    features=base/'public_text_features_20261107_v1/text_features.npz'
    with np.load(features,allow_pickle=False) as f: arrays={k:f[k].copy() for k in f.files}
    tx,_,names=build(train,arrays,lin,candidates)
    packs=[];slices={}; queries={r['profile_version']:r for r in valid}
    for v,keys in plans.items():
        start=len(packs)*21
        for offset in range(0,len(keys),21):
            chunk=keys[offset:offset+21];chunk += [chunk[-1]]*(21-len(chunk))
            packs.append(dict(profile_version=v,positive_issue_key=chunk[0],comparison_issue_keys=chunk[1:]))
        slices[v]=(start,start+len(keys))
    vx,_,_=build(packs,arrays,lin,candidates)
    paths=[Path(__file__),dataset/'samples.jsonl',dataset/'candidates.jsonl',features,base/'public_x_profiles_20261107_v1/lineage.jsonl',base/'expanded_pool_20261107_v2/candidate_plans.json']
    paths += list(p for p in inputs.iterdir() if p.is_file())
    paths += [assets/'encoding-assets/coder_bundle/data/models/gfibot-m0-v1.1/coder/coder-full-search8-v1'/n for n in ['weights.pt','embeddings.npz','model_manifest.json']]
    seeds=list(range(20261102,20261107))
    for seed in seeds:
        for folder,arm in [('bottleneck-diagnostics-20261106-v1','original'),('history-fusion-v2-20261106-v1','learned_behavior'),('transfer-cold-v3-20261107-v1','cosine')]:
            paths += [assets/f'runs/{folder}/{arm}-{seed}/weights.pt']
    protocol=dict(seeds=seeds,arms=['coder_profile','coder_v3','coder_v3_itemnorm','lambdamart_only','coder_v3_tree_aux'],trees=120,
        auxiliary_rule='CODER v3 within-query zscore + clip(LambdaMART residual,-0.25,+0.25); init_score=CODER; no cap tuning',
        primary='developer_macro_mrr',train_queries=len(train),validation_outcomes=len(valid),
        coder_scope='existing CODER-inspired frozen graph/item embeddings plus RC1 profile adapter; v3 adds history fusion and cosine correction',
        input_scope='CODER uses original parent profiles; tree features use audited partial-X lineage; declared unequal feature sources, same supervision and candidate evaluation cohort',
        limitation='legacy CODER graph/base publication audit incomplete; partial-profile reconstruction cannot retroactively sanitize frozen CODER training',
        sequential_diagnostic='v2 adds parameter-free item-L2-normalized v3 scores after v1 revealed large cross-repository weakness; exploratory development, no new independent evidence',
        inherited_training='frozen CODER and its adapters used original larger training cohort; auxiliary stage and tree-only use1937 public-text TRAIN queries; this is a system comparison, not equal-data architecture attribution',
        independent_evaluation=False,evaluate_test=False,base_retrained=False,production_model_replaced=False,
        feature_names=names,input_sha256={str(p):sha(p) for p in paths},audit=audit)
    write(output/'protocol.json',protocol)
    firsts=[g[0] for g in old.groups(splits['train']).values()]
    counts=Counter(r['github_numeric_id'] for r in train);weights=np.repeat([1/counts[r['github_numeric_id']] for r in train],21)
    results=[];rankings={}
    def rank_records(scores):
        records=[]
        for r in valid:
            v=r['profile_version'];start,end=slices[v];keys=plans[v];values=scores[start:end]
            repo=r['positive_issue_key'].split('#')[0];same=[i for i,k in enumerate(keys) if k.split('#')[0]==repo]
            records.append(dict(outcome_id=r['outcome_id'],github_numeric_id=r['github_numeric_id'],
                rank=rank_of(keys,values,r['positive_issue_key']),same_rank=rank_of([keys[i] for i in same],values[same],r['positive_issue_key']),
                same_candidates=len(same),novel=not any(h[0]==repo for h in lin[v]['retained_X'])))
        return records
    for seed in seeds:
        adapter=old.ProfileResidualAdapter(old.ProfileAdapterConfig(embedding_dim=32,hidden_dim=32,residual_scale=.75,gate_mode='none'))
        adapter.load_state_dict(torch.load(assets/f'runs/bottleneck-diagnostics-20261106-v1/original-{seed}/weights.pt',weights_only=True));adapter.eval()
        fusion=old.HistoryFusion(len(old.FEATURE_NAMES));fusion.load_state_dict(torch.load(assets/f'runs/history-fusion-v2-20261106-v1/learned_behavior-{seed}/weights.pt',weights_only=True));fusion.eval()
        ranker=old.TransferHead(False,True);directory=assets/f'runs/transfer-cold-v3-20261107-v1/cosine-{seed}'
        ranker.load_state_dict(torch.load(directory/'weights.pt',weights_only=True));ranker.eval()
        scale=torch.tensor(json.loads((directory/'result.json').read_text())['score_scale'])
        tr,norm=old.prepare_tensors(splits['train'],firsts,frozen,profiles,adapter)
        va,_=old.prepare_tensors(splits['validation'],firsts,frozen,profiles,adapter,norm)
        for data in [tr,va]:
            with torch.no_grad():data['base']=old.users(data,fusion)[0].clone()
        reference=old.evaluate(ranker,va,frozen,scale,{r.positive_issue_key.split('#')[0] for r in firsts})
        if reference != json.loads((directory/'outcomes.json').read_text()): raise ValueError('Original CODER v3 reproduction failed')
        def score_query(r,keys,data,lookup,profile_only=False):
            i=lookup[r['outcome_id']]; parent=data['firsts'][i].profile_version
            if r['parent_profile_version']!=parent:raise ValueError('Parent profile mismatch')
            items=torch.stack([frozen.issue_embeddings[k] for k in keys])
            with torch.inference_mode():
                if profile_only:return (items@data['original'][i]).numpy()
                ids=torch.tensor([i]*len(keys))
                return ranker(data['base'][ids],data['original'][ids],data['values'][ids],data['mask'][ids],data['behavior'][ids],data['available'][ids],items,scale).numpy()
        ti={r.outcome_id:i for i,r in enumerate(tr['firsts'])};vi={r.outcome_id:i for i,r in enumerate(va['firsts'])}
        tc=np.concatenate([standardize(score_query(r,[r['positive_issue_key']]+r['comparison_issue_keys'],tr,ti)) for r in train])
        raw=np.zeros(len(vx),dtype=np.float32);enh=np.zeros_like(raw);normalized=np.zeros_like(raw)
        for v,keys in plans.items():
            start,end=slices[v];r=queries[v]
            raw[start:end]=score_query(r,keys,va,vi,True)
            enh[start:end]=standardize(score_query(r,keys,va,vi))
            itemnorm=np.array([float(frozen.issue_embeddings[k].norm()) for k in keys])
            normalized[start:end]=standardize(score_query(r,keys,va,vi)/np.maximum(itemnorm,1e-8))
            # Padding is never ranked but must have finite init/features.
        for arm in protocol['arms']:
            started=time.perf_counter()
            if arm=='coder_profile':scores=raw
            elif arm=='coder_v3':scores=enh
            elif arm=='coder_v3_itemnorm':scores=normalized
            else:
                auxiliary=arm=='coder_v3_tree_aux'
                x=np.column_stack([tx,tc]) if auxiliary else tx
                v=np.column_stack([vx,enh]) if auxiliary else vx
                model=lgb.LGBMRanker(objective='lambdarank',n_estimators=120,learning_rate=.03,num_leaves=15,max_depth=4,min_child_samples=40,
                    colsample_bytree=.9,reg_lambda=5,n_jobs=1,random_state=seed,verbosity=-1,deterministic=True,force_col_wise=True)
                model.fit(x,np.tile([1]+[0]*20,len(train)),group=[21]*len(train),sample_weight=weights,init_score=tc if auxiliary else None)
                d=output/f'{arm}-{seed}';d.mkdir();model.booster_.save_model(str(d/'model.txt'))
                pred=model.booster_.predict(v); restored=lgb.Booster(model_file=str(d/'model.txt'))
                if not np.array_equal(pred,restored.predict(v)):raise ValueError('Tree reload mismatch')
                scores=bounded_score(enh,pred) if auxiliary else pred
            if not np.isfinite(scores).all():raise ValueError('Nonfinite score')
            ranked=rank_records(scores);rankings[arm,seed]=ranked
            d=output/f'{arm}-{seed}';d.mkdir(exist_ok=True);write(d/'ranks.json',ranked)
            met=metrics([r['rank'] for r in ranked],ranked)
            novel=[r for r in ranked if r['novel']];same=[r for r in ranked if r['same_candidates']>1]
            record=dict(arm=arm,seed=seed,metrics=met,novel=metrics([r['rank'] for r in novel],novel),
                same_repository_mrr=statistics.mean(1/r['same_rank'] for r in same),seconds=time.perf_counter()-started)
            results.append(record);write(output/'results.json',results);print(json.dumps(record),flush=True)
    summary={}
    for arm in protocol['arms']:
        rr=[r for r in results if r['arm']==arm];delta=[]
        for seed in seeds:
            for a,b in zip(rankings[arm,seed],rankings['coder_v3',seed]):
                delta.append(dict(github_numeric_id=a['github_numeric_id'],delta=1/a['rank']-1/b['rank']))
        summary[arm]=dict(means={k:statistics.mean(r['metrics'][k] for r in rr) for k in ['developer_macro_mrr','mrr','recall5','ndcg5']},
            novel_recall5=statistics.mean(r['novel']['recall5'] for r in rr),same_repository_mrr=statistics.mean(r['same_repository_mrr'] for r in rr),
            paired_vs_coder_v3=ci(delta))
    write(output/'summary.json',summary)
    if not all(sha(Path(p))==h for p,h in protocol['input_sha256'].items()):raise ValueError('Inputs changed')
    write(output/'verification.json',dict(all_five_coder_v3_original_rankings_reproduced=True,input_hashes_verified=True,tree_reload_predictions_identical=True,
        candidate_coverage=1,targets_injected=False,original_test_scored=False))
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path.cwd());p.add_argument('--assets',type=Path,default=Path('D:/OSS-Mentor-assets/pr39'))
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.root,a.assets,a.output)
