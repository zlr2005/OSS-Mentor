"""仅用TRAIN的90天获证关系构造多正例监督；验证候选与目标口径不变。"""
import argparse
from collections import Counter,defaultdict
from datetime import datetime,timedelta
import json
from pathlib import Path
import statistics
import time
import numpy as np
import torch
from torch import nn
from train_coder_gated_ranker import GatedFusion,old,build,rows,sha,write,rank_of,metrics,ci,standardize
from train_coder_full_pool import pair_matrix


def additional_positives(sample,training_user_rows,candidates):
    moment=lambda s:datetime.fromisoformat(s.replace('Z','+00:00'))
    at=moment(sample['as_of']);end=at+timedelta(days=90);result={}
    for row in training_user_rows:
        if row['split']!='train':raise ValueError('Only TRAIN labels may be used')
        if row['github_numeric_id']!=sample['github_numeric_id']:raise ValueError('Numeric identity mismatch')
        key=row['positive_issue_key'];label=row['label_source'];c=candidates[key]
        if label['verification_status']!='verified' or label['issue_key']!=key:raise ValueError('Unverified or mismatched label')
        if key==sample['positive_issue_key']:continue
        if at<moment(label['interaction_at'])<=moment(label['outcome_at'])<=end and moment(c['available_from'])<=at<moment(c['available_until']):
            result[key]=row['outcome_id']
    return result


def multi_loss(pred,valid,positive,weights,arm):
    if not bool(positive[:,0].all()) or bool((positive&~valid).any()):raise ValueError('Invalid target mask')
    if arm=='masked_single':
        eligible=valid&~positive;eligible[:,0]=True
        loss=nn.functional.cross_entropy(pred.masked_fill(~eligible,float('-inf')),torch.zeros(len(pred),dtype=torch.long),reduction='none')
    else:
        eligible=valid
        logden=torch.logsumexp(pred.masked_fill(~eligible,float('-inf')),dim=1)
        if arm=='uniform_positive':loss=logden-(pred*positive).sum(1)/positive.sum(1)
        elif arm=='marginal_positive':loss=logden-torch.logsumexp(pred.masked_fill(~positive,float('-inf')),dim=1)
        else:raise ValueError('Unknown objective')
    penalty=(pred.square()*eligible).sum(1)/eligible.sum(1)
    return ((loss+.01*penalty)*weights).sum()/weights.sum()


def fit(x,base,valid,positive,weights,seed,arm):
    old._set_seed(seed);model=GatedFusion();optimizer=torch.optim.AdamW(model.parameters(),lr=.001,weight_decay=.001)
    order=np.random.default_rng(seed);history=[]
    for epoch in range(30):
        total=0.
        for ids in np.array_split(order.permutation(len(x)),int(np.ceil(len(x)/64))):
            pred=model(x[ids],base[ids]);loss=multi_loss(pred,valid[ids],positive[ids],weights[ids],arm)
            optimizer.zero_grad();loss.backward();nn.utils.clip_grad_norm_(model.parameters(),1);optimizer.step();total+=float(loss.detach())*len(ids)
        history.append(dict(epoch=epoch+1,train_loss=total/len(x)))
    return model,history


def run(root,assets,output):
    torch.set_num_threads(1);base=root/'data/midterm/working';inputs=base/'rc1_training_inputs_20261106_v1';reference=base/'coder_gated_20261108_v1'
    prior=json.loads((reference/'protocol.json').read_text());hashes=dict(prior['input_sha256'])
    hashes[str(Path(__file__))]=sha(Path(__file__))
    for seed in prior['seeds']:
        for p in [reference/f'train_controls-{seed}.json',reference/f'gated_fusion-{seed}/normalizer.json']:
            hashes[str(p)]=sha(p)
    if not all(sha(Path(p))==h for p,h in hashes.items()):raise ValueError('Frozen inputs changed')
    dataset=base/'public_text_dataset_20261107_v2';samples=rows(dataset/'samples.jsonl')
    train=[r for r in samples if r['split']=='train'];validrows=[r for r in samples if r['split']=='validation']
    candidates={r['issue_key']:r for r in rows(dataset/'candidates.jsonl')};lineage={r['profile_version']:r for r in rows(base/'public_x_profiles_20261107_v1/lineage.jsonl')}
    byuser=defaultdict(list)
    for r in train:byuser[r['github_numeric_id']].append(r)
    extras=[additional_positives(r,byuser[r['github_numeric_id']],candidates) for r in train]
    output.mkdir(parents=True,exist_ok=False)
    protocol=dict(arms=['masked_single','uniform_positive','marginal_positive'],seeds=prior['seeds'],epochs=30,batch_queries=64,
        model='unchanged CODER GatedFusion',optimizer='AdamW lr.001 weight_decay.001,clip1',
        labels='only verified TRAIN success, same numeric user, interaction strictly after query cutoff, resolution within90days, historical candidate open at cutoff',
        objectives='masked single excludes additional positives from loss; uniform CE averages all positives; marginal loss maximizes positive-set probability',
        normalization='reuse original per-seed TRAIN pair normalizer; CODER score statistics from original21 candidates even for additional targets',
        candidate_rule='original target + frozen20 controls + eligible additional TRAIN positives; padding masked',
        multi_positive_queries=sum(bool(e) for e in extras),additional_positive_references=sum(map(len,extras)),max_extra=max(map(len,extras)),
        evaluate_test=False,independent_evaluation=False,validation_protocol_unchanged=True,checkpoint_selection=False,production_model_replaced=False,
        inherited_audit_limit=prior['inherited_audit_limit'],inherited_training=prior['inherited_training'],input_sha256=hashes)
    write(output/'protocol.json',protocol);write(output/'additional_train_positives.json',[dict(outcome_id=r['outcome_id'],additional=e) for r,e in zip(train,extras)])
    config=old.AdvancedTrainingConfig.from_path(assets/'runs/public-supplement-20261106-v2/training_config.json')
    frozen=old.load_frozen_inputs(assets/'encoding-assets/coder_bundle/data/models/gfibot-m0-v1.1/coder/coder-full-search8-v1',inputs/'historical_issue_embeddings.npz')
    splits,_,_=old.prepare_training_data(profiles_path=inputs/'profiles.jsonl',outcomes_path=inputs/'advanced_issue_outcomes.jsonl',candidates_path=inputs/'historical_issue_candidates.jsonl',frozen=frozen,config=config)
    profiles={r['profile_version']:r for r in rows(inputs/'profiles.jsonl')};firsts=[g[0] for g in old.groups(splits['train']).values()]
    with np.load(base/'public_text_features_20261107_v1/text_features.npz',allow_pickle=False) as f:arrays={k:f[k].copy() for k in f.files}
    vpmap=json.loads((base/'expanded_pool_20261107_v2/candidate_plans.json').read_text());vp=[vpmap[r['profile_version']] for r in validrows]
    vpair,_=pair_matrix(validrows,vp,arrays,lineage,candidates);vs=[];offset=0
    for keys in vp:vs.append((offset,offset+len(keys)));offset+=len(keys)
    counts=Counter(r['github_numeric_id'] for r in train);weights=torch.tensor([1/counts[r['github_numeric_id']] for r in train])
    width=21+max(map(len,extras));validmask=torch.zeros((len(train),width),dtype=torch.bool);positive=torch.zeros_like(validmask)
    for i,e in enumerate(extras):validmask[i,:21+len(e)]=True;positive[i,0]=True;positive[i,21:21+len(e)]=True
    records=[];rankings={}
    def rank(values):
        rr=[]
        for r,keys,(start,end) in zip(validrows,vp,vs):
            s=values[start:end];repo=r['positive_issue_key'].split('#')[0];same=[i for i,k in enumerate(keys) if k.split('#')[0]==repo]
            rr.append(dict(outcome_id=r['outcome_id'],github_numeric_id=r['github_numeric_id'],rank=rank_of(keys,s,r['positive_issue_key']),
                same_rank=rank_of([keys[i] for i in same],s[same],r['positive_issue_key']),same_candidates=len(same),
                novel=not any(h[0]==repo for h in lineage[r['profile_version']]['retained_X'])))
        return rr
    for seed in prior['seeds']:
        controls={r['outcome_id']:r['controls'] for r in json.loads((reference/f'train_controls-{seed}.json').read_text())}
        tp=[[r['positive_issue_key']]+controls[r['outcome_id']]+sorted(e) for r,e in zip(train,extras)]
        for keys in tp:assert len(keys)==len(set(keys))
        pair,_=pair_matrix(train,tp,arrays,lineage,candidates)
        normjson=json.loads((reference/f'gated_fusion-{seed}/normalizer.json').read_text());mean=np.asarray(normjson['mean'],dtype=np.float32);std=np.asarray(normjson['std'],dtype=np.float32)
        pair=np.clip((pair-mean)/std,-5,5);vnormalized=np.clip((vpair-mean)/std,-5,5)
        adapter=old.ProfileResidualAdapter(old.ProfileAdapterConfig(embedding_dim=32,hidden_dim=32,residual_scale=.75,gate_mode='none'))
        adapter.load_state_dict(torch.load(assets/f'runs/bottleneck-diagnostics-20261106-v1/original-{seed}/weights.pt',weights_only=True));adapter.eval()
        fusion=old.HistoryFusion(len(old.FEATURE_NAMES));fusion.load_state_dict(torch.load(assets/f'runs/history-fusion-v2-20261106-v1/learned_behavior-{seed}/weights.pt',weights_only=True));fusion.eval()
        ranker=old.TransferHead(False,True);directory=assets/f'runs/transfer-cold-v3-20261107-v1/cosine-{seed}'
        ranker.load_state_dict(torch.load(directory/'weights.pt',weights_only=True));ranker.eval();scale=torch.tensor(json.loads((directory/'result.json').read_text())['score_scale'])
        tr,norm=old.prepare_tensors(splits['train'],firsts,frozen,profiles,adapter);va,_=old.prepare_tensors(splits['validation'],firsts,frozen,profiles,adapter,norm)
        for data in [tr,va]:
            with torch.no_grad():data['base']=old.users(data,fusion)[0].clone()
        def features(rows_,plans,data,trainmode):
            lookup={r.outcome_id:i for i,r in enumerate(data['firsts'])};embedding=[];score=[]
            for r,keys in zip(rows_,plans):
                i=lookup[r['outcome_id']];assert data['firsts'][i].profile_version==r['parent_profile_version']
                item=torch.stack([frozen.issue_embeddings[k] for k in keys]);ids=torch.tensor([i]*len(keys))
                with torch.no_grad():s=ranker(data['base'][ids],data['original'][ids],data['values'][ids],data['mask'][ids],data['behavior'][ids],data['available'][ids],item,scale).numpy()
                stats=s[:21] if trainmode else s
                s=(s-stats.mean())/max(float(stats.std()),1e-8)
                u=nn.functional.normalize(data['base'][ids],dim=-1);v=nn.functional.normalize(item,dim=-1)
                embedding.append(torch.cat([u,v,u*v,(u-v).abs()],-1).numpy());score.append(s)
            return embedding,score
        emb,tb=features(train,tp,tr,True);vemb,vb=features(validrows,vp,va,False)
        x=torch.zeros((len(train),width,144));trainbase=torch.zeros((len(train),width));offset=0
        for i,keys in enumerate(tp):
            n=len(keys);x[i,:n]=torch.tensor(np.column_stack([emb[i],pair[offset:offset+n]]));trainbase[i,:n]=torch.tensor(tb[i]);offset+=n
        vx=torch.tensor(np.column_stack([np.concatenate(vemb),vnormalized]),dtype=torch.float32);vb=torch.tensor(np.concatenate(vb),dtype=torch.float32)
        prior_model=GatedFusion();prior_model.load_state_dict(torch.load(reference/f'gated_fusion-{seed}/weights.pt',weights_only=True));prior_model.eval()
        with torch.no_grad():previous=rank(prior_model(vx,vb).numpy())
        assert previous==json.loads((reference/f'gated_fusion-{seed}/ranks.json').read_text())
        for arm in protocol['arms']:
            print(json.dumps(dict(seed=seed,arm=arm,stage='training')),flush=True);started=time.perf_counter()
            model,history=fit(x,trainbase,validmask,positive,weights,seed,arm);model.eval();d=output/f'{arm}-{seed}';d.mkdir();torch.save(model.state_dict(),d/'weights.pt')
            restored=GatedFusion();restored.load_state_dict(torch.load(d/'weights.pt',weights_only=True));restored.eval()
            with torch.no_grad():values=model(vx,vb);again=restored(vx,vb)
            assert torch.equal(values,again) and torch.isfinite(values).all()
            rr=rank(values.numpy());rankings[arm,seed]=rr;novel=[r for r in rr if r['novel']];same=[r for r in rr if r['same_candidates']>1]
            rec=dict(arm=arm,seed=seed,metrics=metrics([r['rank'] for r in rr],rr),novel=metrics([r['rank'] for r in novel],novel),
                same_repository_mrr=statistics.mean(1/r['same_rank'] for r in same),seconds=time.perf_counter()-started)
            records.append(rec);write(d/'ranks.json',rr);write(d/'history.json',history);write(d/'normalizer.json',normjson);write(output/'results.json',records);print(json.dumps(rec),flush=True)
    summary={}
    for arm in protocol['arms']:
        rr=[r for r in records if r['arm']==arm];delta=[]
        for seed in prior['seeds']:
            previous=json.loads((reference/f'gated_fusion-{seed}/ranks.json').read_text())
            for r,b in zip(rankings[arm,seed],previous):delta.append(dict(github_numeric_id=r['github_numeric_id'],delta=1/r['rank']-1/b['rank']))
        summary[arm]=dict(means={k:statistics.mean(r['metrics'][k] for r in rr) for k in ['developer_macro_mrr','mrr','recall5','ndcg5']},
            novel_recall5=statistics.mean(r['novel']['recall5'] for r in rr),same_repository_mrr=statistics.mean(r['same_repository_mrr'] for r in rr),paired_vs_previous_gated=ci(delta))
    assert all(sha(Path(p))==h for p,h in hashes.items())
    write(output/'summary.json',summary);write(output/'verification.json',dict(prior_gated_rankings_reproduced=True,input_hashes_verified=True,reloaded_predictions_identical=True,
        additional_labels_train_only=True,validation_targets_and_pool_unchanged=True,production_model_replaced=False))
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=Path.cwd());p.add_argument('--assets',type=Path,default=Path('D:/OSS-Mentor-assets/pr39'))
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.root,a.assets,a.output)
