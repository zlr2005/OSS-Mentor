"""CODER门控融合的全合格候选池训练；固定预算，仅开发评价。"""
import argparse
from collections import Counter,defaultdict
import json
from pathlib import Path
import statistics
import time
import numpy as np
import torch
from torch import nn
from train_coder_gated_ranker import GatedFusion,old,standardize,build,rows,sha,write,eligible_pool,rank_of,metrics,ci


def training_pool(sample,candidates,known):
    keys=eligible_pool(candidates,sample['as_of'],known-{sample['positive_issue_key']})
    if sample['positive_issue_key'] not in keys:raise ValueError('TRAIN target ineligible; never inject target')
    return keys


def batch_queries(x,base,slices,targets,ids):
    lengths=[slices[i][1]-slices[i][0] for i in ids];width=max(lengths)
    features=torch.zeros((len(ids),width,x.shape[1]),dtype=x.dtype)
    scores=torch.zeros((len(ids),width),dtype=base.dtype)
    mask=torch.zeros((len(ids),width),dtype=torch.bool)
    for row,i in enumerate(ids):
        start,end=slices[i];n=end-start
        features[row,:n]=x[start:end];scores[row,:n]=base[start:end];mask[row,:n]=True
    return features,scores,mask,torch.tensor([targets[i] for i in ids],dtype=torch.long)


def listwise_loss(pred,mask,targets,weights):
    if not bool(mask[torch.arange(len(targets)),targets].all()):raise ValueError('Target refers to padding')
    ce=nn.functional.cross_entropy(pred.masked_fill(~mask,float('-inf')),targets,reduction='none')
    penalty=(pred.square()*mask).sum(1)/mask.sum(1)
    return ((ce+.01*penalty)*weights).sum()/weights.sum()


def fit(x,base,slices,targets,weights,seed):
    old._set_seed(seed);model=GatedFusion();optimizer=torch.optim.AdamW(model.parameters(),lr=.001,weight_decay=.001)
    order=np.random.default_rng(seed);history=[]
    for epoch in range(30):
        total=0.
        for ids in np.array_split(order.permutation(len(slices)),int(np.ceil(len(slices)/64))):
            xb,bb,mask,target=batch_queries(x,base,slices,targets,ids)
            score=model(xb,bb);loss=listwise_loss(score,mask,target,weights[ids])
            optimizer.zero_grad();loss.backward();nn.utils.clip_grad_norm_(model.parameters(),1);optimizer.step()
            total+=float(loss.detach())*len(ids)
        history.append(dict(epoch=epoch+1,train_loss=total/len(slices)))
    return model,history


def pair_matrix(samples,plans,arrays,lineage,candidates):
    packs=[];cuts=[]
    for r,keys in zip(samples,plans):
        start=len(packs)*21
        for offset in range(0,len(keys),21):
            chunk=keys[offset:offset+21];chunk += [chunk[-1]]*(21-len(chunk))
            packs.append(dict(profile_version=r['profile_version'],positive_issue_key=chunk[0],comparison_issue_keys=chunk[1:]))
        cuts.append((start,start+len(keys)))
    padded,_,names=build(packs,arrays,lineage,candidates)
    matrix=np.concatenate([padded[start:end] for start,end in cuts])
    return matrix,names


def run(root,assets,output):
    torch.set_num_threads(1);base=root/'data/midterm/working';inputs=base/'rc1_training_inputs_20261106_v1'
    reference=base/'coder_gated_20261108_v1';prior=json.loads((reference/'protocol.json').read_text())
    if not all(sha(Path(p))==h for p,h in prior['input_sha256'].items()):raise ValueError('Frozen inputs changed')
    dataset=base/'public_text_dataset_20261107_v2';samples=rows(dataset/'samples.jsonl')
    train=[r for r in samples if r['split']=='train'];valid=[r for r in samples if r['split']=='validation']
    candidates={r['issue_key']:r for r in rows(dataset/'candidates.jsonl')}
    lineage={r['profile_version']:r for r in rows(base/'public_x_profiles_20261107_v1/lineage.jsonl')}
    oldplans=json.loads((base/'expanded_pool_20261107_v2/candidate_plans.json').read_text())
    known=defaultdict(set)
    for r in train:known[r['github_numeric_id']].add(r['positive_issue_key'])
    tp=[training_pool(r,list(candidates.values()),known[r['github_numeric_id']]) for r in train]
    vp=[oldplans[r['profile_version']] for r in valid]
    output.mkdir(parents=True,exist_ok=False)
    hashes=dict(prior['input_sha256']);hashes[str(Path(__file__))]=sha(Path(__file__))
    hashes[str(Path(__file__).with_name('train_coder_gated_ranker.py'))]=sha(Path(__file__).with_name('train_coder_gated_ranker.py'))
    protocol=dict(seeds=prior['seeds'],arms=['full_pool_gated'],epochs=30,batch_queries=64,optimizer='AdamW lr.001 weight_decay.001,gradient_clip1',
        loss='weighted full-pool listwiseCE +.01 valid-score square, exact padding mask',
        checkpoint_selection=False,validation_retuning=False,architecture='unchanged GatedFusion',
        change='all cutoff-eligible TRAIN candidates, exclude other known TRAIN positives; full-pool TRAIN statistics and CODER zscores',
        training_queries=len(train),train_pool_sizes=dict(min=min(map(len,tp)),max=max(map(len,tp)),mean=statistics.mean(map(len,tp))),
        train_candidate_references=sum(map(len,tp)),validation_outcomes=len(valid),validation_candidate_plans_unchanged=True,
        candidates='all admitted reconstructed snapshots only, not all GitHub; unlabeled alternatives are not verified negatives',
        normalization='pair mean/std from flat full TRAIN candidates only; clip5; CODER zscore within actual pool',
        evaluate_test=False,independent_evaluation=False,production_model_replaced=False,
        inherited_audit_limit=prior['inherited_audit_limit'],inherited_training=prior['inherited_training'],input_sha256=hashes)
    write(output/'protocol.json',protocol)
    write(output/'train_candidate_plans.json',[dict(outcome_id=r['outcome_id'],keys=keys) for r,keys in zip(train,tp)])
    config=old.AdvancedTrainingConfig.from_path(assets/'runs/public-supplement-20261106-v2/training_config.json')
    frozen=old.load_frozen_inputs(assets/'encoding-assets/coder_bundle/data/models/gfibot-m0-v1.1/coder/coder-full-search8-v1',inputs/'historical_issue_embeddings.npz')
    required={k for keys in tp+vp for k in keys};missing=sorted(required-set(frozen.issue_embeddings))
    write(output/'coverage.json',dict(required_candidates=len(required),missing_embeddings=missing,targets_injected=False))
    if missing:raise ValueError('Missing vectors: do not silently shrink full pool')
    splits,_,_=old.prepare_training_data(profiles_path=inputs/'profiles.jsonl',outcomes_path=inputs/'advanced_issue_outcomes.jsonl',candidates_path=inputs/'historical_issue_candidates.jsonl',frozen=frozen,config=config)
    profiles={r['profile_version']:r for r in rows(inputs/'profiles.jsonl')}
    firsts=[g[0] for g in old.groups(splits['train']).values()]
    with np.load(base/'public_text_features_20261107_v1/text_features.npz',allow_pickle=False) as f:arrays={k:f[k].copy() for k in f.files}
    print(json.dumps(dict(stage='full_pool_features',queries=len(train),references=sum(map(len,tp)))),flush=True)
    tx,names=pair_matrix(train,tp,arrays,lineage,candidates);vx,_=pair_matrix(valid,vp,arrays,lineage,candidates)
    mean=tx.mean(0);std=tx.std(0).clip(1e-6);tx=np.clip((tx-mean)/std,-5,5);vx=np.clip((vx-mean)/std,-5,5)
    write(output/'normalizer.json',dict(mean=mean.tolist(),std=std.tolist(),features=names,fit='full TRAIN candidate matrix only'))
    def slices(plans):
        result=[];offset=0
        for keys in plans:result.append((offset,offset+len(keys)));offset+=len(keys)
        return result
    ts=slices(tp);vs=slices(vp);targets=[keys.index(r['positive_issue_key']) for r,keys in zip(train,tp)]
    counts=Counter(r['github_numeric_id'] for r in train);weights=torch.tensor([1/counts[r['github_numeric_id']] for r in train])
    records=[];delta=[]
    def ranking(scores):
        rr=[]
        for r,keys,(start,end) in zip(valid,vp,vs):
            s=scores[start:end];repo=r['positive_issue_key'].split('#')[0];same=[i for i,k in enumerate(keys) if k.split('#')[0]==repo]
            rr.append(dict(outcome_id=r['outcome_id'],github_numeric_id=r['github_numeric_id'],rank=rank_of(keys,s,r['positive_issue_key']),
                same_rank=rank_of([keys[i] for i in same],s[same],r['positive_issue_key']),same_candidates=len(same),
                novel=not any(h[0]==repo for h in lineage[r['profile_version']]['retained_X'])))
        return rr
    for seed in prior['seeds']:
        adapter=old.ProfileResidualAdapter(old.ProfileAdapterConfig(embedding_dim=32,hidden_dim=32,residual_scale=.75,gate_mode='none'))
        adapter.load_state_dict(torch.load(assets/f'runs/bottleneck-diagnostics-20261106-v1/original-{seed}/weights.pt',weights_only=True));adapter.eval()
        fusion=old.HistoryFusion(len(old.FEATURE_NAMES));fusion.load_state_dict(torch.load(assets/f'runs/history-fusion-v2-20261106-v1/learned_behavior-{seed}/weights.pt',weights_only=True));fusion.eval()
        ranker=old.TransferHead(False,True);directory=assets/f'runs/transfer-cold-v3-20261107-v1/cosine-{seed}'
        ranker.load_state_dict(torch.load(directory/'weights.pt',weights_only=True));ranker.eval();scale=torch.tensor(json.loads((directory/'result.json').read_text())['score_scale'])
        tr,norm=old.prepare_tensors(splits['train'],firsts,frozen,profiles,adapter);va,_=old.prepare_tensors(splits['validation'],firsts,frozen,profiles,adapter,norm)
        for data in [tr,va]:
            with torch.no_grad():data['base']=old.users(data,fusion)[0].clone()
        def features(rows_,plans,data):
            lookup={r.outcome_id:i for i,r in enumerate(data['firsts'])};emb=[];score=[]
            for r,keys in zip(rows_,plans):
                i=lookup[r['outcome_id']]
                assert data['firsts'][i].profile_version==r['parent_profile_version']
                item=torch.stack([frozen.issue_embeddings[k] for k in keys]);ids=torch.tensor([i]*len(keys))
                with torch.no_grad():s=ranker(data['base'][ids],data['original'][ids],data['values'][ids],data['mask'][ids],data['behavior'][ids],data['available'][ids],item,scale)
                u=nn.functional.normalize(data['base'][ids],dim=-1);v=nn.functional.normalize(item,dim=-1)
                emb.append(torch.cat([u,v,u*v,(u-v).abs()],-1).numpy());score.append(standardize(s.numpy()))
            return np.concatenate(emb),torch.tensor(np.concatenate(score),dtype=torch.float32)
        te,tb=features(train,tp,tr);ve,vb=features(valid,vp,va)
        trainx=torch.tensor(np.column_stack([te,tx]),dtype=torch.float32);validx=torch.tensor(np.column_stack([ve,vx]),dtype=torch.float32)
        del te,ve
        oldrank=json.loads((base/f'coder_primary_20261108_v2/coder_v3-{seed}/ranks.json').read_text())
        assert [r['rank'] for r in ranking(vb.numpy())]==[r['rank'] for r in oldrank]
        print(json.dumps(dict(seed=seed,stage='training30epochs')),flush=True);started=time.perf_counter()
        model,history=fit(trainx,tb,ts,targets,weights,seed);model.eval()
        d=output/f'full_pool_gated-{seed}';d.mkdir();torch.save(model.state_dict(),d/'weights.pt');write(d/'history.json',history)
        restored=GatedFusion();restored.load_state_dict(torch.load(d/'weights.pt',weights_only=True));restored.eval()
        with torch.no_grad():scores=model(validx,vb);again=restored(validx,vb)
        assert torch.equal(scores,again) and torch.isfinite(scores).all() and float(scores.abs().max())<=2.250001
        ranked=ranking(scores.numpy());novel=[r for r in ranked if r['novel']];same=[r for r in ranked if r['same_candidates']>1]
        previous=json.loads((reference/f'gated_fusion-{seed}/ranks.json').read_text())
        for r,b in zip(ranked,previous):delta.append(dict(github_numeric_id=r['github_numeric_id'],delta=1/r['rank']-1/b['rank']))
        rec=dict(seed=seed,metrics=metrics([r['rank'] for r in ranked],ranked),novel=metrics([r['rank'] for r in novel],novel),
            same_repository_mrr=statistics.mean(1/r['same_rank'] for r in same),seconds=time.perf_counter()-started)
        records.append(rec);write(d/'ranks.json',ranked);write(output/'results.json',records);print(json.dumps(rec),flush=True)
    summary=dict(means={k:statistics.mean(r['metrics'][k] for r in records) for k in ['developer_macro_mrr','mrr','recall5','ndcg5']},
        novel_recall5=statistics.mean(r['novel']['recall5'] for r in records),same_repository_mrr=statistics.mean(r['same_repository_mrr'] for r in records),paired_vs_previous_gated=ci(delta))
    assert all(sha(Path(p))==h for p,h in hashes.items())
    write(output/'summary.json',summary);write(output/'verification.json',dict(input_hashes_verified=True,coder_baseline_reproduced=True,reloaded_scores_identical=True,
        train_pool_cutoff_and_known_positive_exclusion_checked=True,targets_injected=False,test_scored=False))
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=Path.cwd());p.add_argument('--assets',type=Path,default=Path('D:/OSS-Mentor-assets/pr39'))
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.root,a.assets,a.output)
