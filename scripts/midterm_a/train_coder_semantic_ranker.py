"""细粒度语义匹配与同仓库辅助目标，CODER门控融合固定预算消融。"""
import argparse
from collections import Counter,defaultdict
import json
from pathlib import Path
import statistics
import time
import numpy as np
import torch
from torch import nn
from coder_primary_comparison import old,standardize
from pfirec_inspired_ranker import build as base_build,rows,sha,write
from prepare_coder_semantic_matches import semantic_matches,NAMES
from evaluate_expanded_pool import eligible_pool,rank_of
from train_public_text_curve import metrics
from diagnose_pfirec_ranker import ci


SEMANTICS={}

def build(samples,features,lineage,candidates):
    x,cosine,names=base_build(samples,features,lineage,candidates)
    pm={str(k):i for i,k in enumerate(features['profile_versions'])}
    im={str(k):i for i,k in enumerate(features['issue_keys'])}
    extra=[]
    for r in samples:
        v=r['profile_version'];files=np.asarray([SEMANTICS['file'][k] for k in SEMANTICS['selected'][v]])
        for key in [r['positive_issue_key']]+r['comparison_issue_keys']:
            extra.append(semantic_matches(features['user'][pm[v]],files,SEMANTICS['title'][key],features['item'][im[key]]))
    return np.column_stack([x,np.asarray(extra,dtype=np.float32)]),cosine,names+NAMES


def same_repo_loss(scores,mask):
    if not bool(mask[:,0].all()):raise ValueError('TRAIN target must be included in same-repo mask')
    return nn.functional.cross_entropy(scores.masked_fill(~mask,float('-inf')),torch.zeros(len(scores),dtype=torch.long),reduction='none')


class NeuralHead(nn.Module):
    def __init__(self,dimension):
        super().__init__()
        self.net=nn.Sequential(nn.Linear(dimension,32),nn.SiLU(),nn.Linear(32,1))
        nn.init.zeros_(self.net[-1].weight);nn.init.zeros_(self.net[-1].bias)

    def forward(self,x,base):
        return base+2*torch.tanh(self.net(x).squeeze(-1))


def select_controls(sample,eligible,known,scores):
    allowed=set(eligible)-known-{sample['positive_issue_key']}
    selected=[k for k in sample['comparison_issue_keys'] if k in allowed][:10]
    repo=sample['positive_issue_key'].split('#')[0]
    hard=sorted(allowed-set(selected),key=lambda k:(k.split('#')[0]!=repo,-scores[k],k))
    selected += hard[:20-len(selected)]
    if len(set(selected))!=20:raise ValueError('Insufficient unique TRAIN controls')
    return dict(sample,comparison_issue_keys=selected)


class GatedFusion(nn.Module):
    def __init__(self,pair_dimension=16):
        super().__init__()
        self.pair=NeuralHead(pair_dimension)
        self.embedding=nn.Sequential(nn.Linear(128,16),nn.SiLU(),nn.Linear(16,1))
        self.gate=nn.Sequential(nn.Linear(pair_dimension,8),nn.SiLU(),nn.Linear(8,1))
        nn.init.zeros_(self.embedding[-1].weight);nn.init.zeros_(self.embedding[-1].bias)
        nn.init.zeros_(self.gate[-1].weight);nn.init.constant_(self.gate[-1].bias,-2)

    def components(self,x,base):
        pair=x[...,128:]
        primary=self.pair(pair,torch.zeros_like(base))
        gate=torch.sigmoid(self.gate(pair).squeeze(-1))
        coder=.25*gate*torch.tanh(base+self.embedding(x[...,:128]).squeeze(-1))
        return primary,coder,gate

    def forward(self,x,base):
        primary,coder,_=self.components(x,base)
        return primary+coder


def train_head(x,base,weights,seed,epochs,within=None):
    old._set_seed(seed)
    model=GatedFusion(x.shape[-1]-128)
    optimizer=torch.optim.AdamW(model.parameters(),lr=.001,weight_decay=.001)
    log=[];order=np.random.default_rng(seed)
    for epoch in range(epochs):
        total=0.
        for ids in np.array_split(order.permutation(len(x)),int(np.ceil(len(x)/64))):
            pred=model(x[ids],base[ids]);per_query=nn.functional.cross_entropy(pred,torch.zeros(len(ids),dtype=torch.long),reduction='none')
            aux=same_repo_loss(pred,within[ids]) if within is not None else torch.zeros_like(per_query)
            loss=((per_query+.25*aux+.01*pred.square().mean(1))*weights[ids]).sum()/weights[ids].sum()
            optimizer.zero_grad();loss.backward();nn.utils.clip_grad_norm_(model.parameters(),1);optimizer.step();total+=float(loss.detach())*len(ids)
        log.append(dict(epoch=epoch+1,train_loss=total/len(x)))
    return model,log


def run(root,assets,output):
    torch.set_num_threads(1)
    base=root/'data/midterm/working';inputs=base/'rc1_training_inputs_20261106_v1'
    prior=json.loads((base/'coder_primary_20261108_v2/protocol.json').read_text())
    if not all(sha(Path(p))==h for p,h in prior['input_sha256'].items()):raise ValueError('Prior CODER inputs changed')
    dataset=base/'public_text_dataset_20261107_v2';samples=rows(dataset/'samples.jsonl')
    train=[r for r in samples if r['split']=='train'];valid=[r for r in samples if r['split']=='validation']
    c={r['issue_key']:r for r in rows(dataset/'candidates.jsonl')}
    lin={r['profile_version']:r for r in rows(base/'public_x_profiles_20261107_v1/lineage.jsonl')}
    plans=json.loads((base/'expanded_pool_20261107_v2/candidate_plans.json').read_text())
    config=old.AdvancedTrainingConfig.from_path(assets/'runs/public-supplement-20261106-v2/training_config.json')
    frozen=old.load_frozen_inputs(assets/'encoding-assets/coder_bundle/data/models/gfibot-m0-v1.1/coder/coder-full-search8-v1',inputs/'historical_issue_embeddings.npz')
    splits,_,_=old.prepare_training_data(profiles_path=inputs/'profiles.jsonl',outcomes_path=inputs/'advanced_issue_outcomes.jsonl',candidates_path=inputs/'historical_issue_candidates.jsonl',frozen=frozen,config=config)
    profiles={r['profile_version']:r for r in rows(inputs/'profiles.jsonl')}
    firsts=[g[0] for g in old.groups(splits['train']).values()]
    with np.load(base/'public_text_features_20261107_v1/text_features.npz',allow_pickle=False) as f:a={k:f[k].copy() for k in f.files}
    semantic_dir=base/'coder_semantic_matches_20261108_v1'
    complete=json.loads((semantic_dir/'complete.json').read_text())
    if sha(semantic_dir/'semantic_vectors.npz')!=complete['cache_sha256']:raise ValueError('Semantic cache changed')
    with np.load(semantic_dir/'semantic_vectors.npz',allow_pickle=False) as z:
        SEMANTICS['file']=dict(zip(z['file_keys'].astype(str),z['file_vectors']))
        SEMANTICS['title']=dict(zip(z['issue_keys'].astype(str),z['title_vectors']))
    SEMANTICS['selected']=json.loads((base/'public_text_features_20261107_v1/selected_files.json').read_text())
    output.mkdir(parents=True,exist_ok=False)
    hashes=dict(prior['input_sha256']);hashes[str(Path(__file__))]=sha(Path(__file__))
    for pth in [semantic_dir/'protocol.json',semantic_dir/'semantic_vectors.npz',base/'public_text_features_20261107_v1/selected_files.json',Path(__file__).with_name('prepare_coder_semantic_matches.py')]:hashes[str(pth)]=sha(pth)
    protocol=dict(arms=['semantic_only','within_repo_only','semantic_within_repo'],seeds=prior['seeds'],epochs=30,batch_queries=64,hidden=32,
        learning_rate=.001,weight_decay=.001,loss='query-weighted listwise CE + .01 total-score square; within arms add.25 same-repository CE, TRAIN mask only',
        pair_score_cap=2,coder_correction_cap=.25,initial_gate_bias=-2,gradient_clip=1,selection='none; fixed30epochs, no validation checkpoint or parameter search',
        sampling='10 original eligible plus10 same-repo-first CODER-hard controls, excludes all known TRAIN positives, no validation labels',
        feature_scope='CODER128 + original16 and optionally6 frozen title/per-file cosine features; exact same selected public files',
        representation_scope='frozen CODER RC1/v2/v3; learned gate plus neural pair branch, no graph retraining or tree scores/teachers',
        ablation='semantic_only:22pair features; within_repo_only:original16 plus.25 auxiliary loss; semantic_within_repo:both; compare fixed prior gated baseline; no parameter search',
        inherited_audit_limit=prior['limitation'],inherited_training=prior['inherited_training'],
        evaluate_test=False,independent_evaluation=False,production_model_replaced=False,input_sha256=hashes)
    write(output/'protocol.json',protocol)
    pools={};known=defaultdict(set)
    for r in train:known[r['github_numeric_id']].add(r['positive_issue_key'])
    for r in train:
        v=r['profile_version']
        if v not in pools:pools[v]=eligible_pool(list(c.values()),r['as_of'],set())
    counts=Counter(r['github_numeric_id'] for r in train);weights=torch.tensor([1/counts[r['github_numeric_id']] for r in train])
    records=[];ranked_all={}
    for seed in protocol['seeds']:
        adapter=old.ProfileResidualAdapter(old.ProfileAdapterConfig(embedding_dim=32,hidden_dim=32,residual_scale=.75,gate_mode='none'))
        adapter.load_state_dict(torch.load(assets/f'runs/bottleneck-diagnostics-20261106-v1/original-{seed}/weights.pt',weights_only=True));adapter.eval()
        fusion=old.HistoryFusion(len(old.FEATURE_NAMES));fusion.load_state_dict(torch.load(assets/f'runs/history-fusion-v2-20261106-v1/learned_behavior-{seed}/weights.pt',weights_only=True));fusion.eval()
        ranker=old.TransferHead(False,True);directory=assets/f'runs/transfer-cold-v3-20261107-v1/cosine-{seed}'
        ranker.load_state_dict(torch.load(directory/'weights.pt',weights_only=True));ranker.eval()
        scale=torch.tensor(json.loads((directory/'result.json').read_text())['score_scale'])
        tr,norm=old.prepare_tensors(splits['train'],firsts,frozen,profiles,adapter);va,_=old.prepare_tensors(splits['validation'],firsts,frozen,profiles,adapter,norm)
        for data in [tr,va]:
            with torch.no_grad():data['base']=old.users(data,fusion)[0].clone()
        ti={r.outcome_id:i for i,r in enumerate(tr['firsts'])};vi={r.outcome_id:i for i,r in enumerate(va['firsts'])}
        def vectors(r,keys,data,lookup):
            i=lookup[r['outcome_id']]
            if r['parent_profile_version']!=data['firsts'][i].profile_version:raise ValueError('Parent identity mismatch')
            item=torch.stack([frozen.issue_embeddings[k] for k in keys]);ids=torch.tensor([i]*len(keys))
            with torch.inference_mode():
                score=ranker(data['base'][ids],data['original'][ids],data['values'][ids],data['mask'][ids],data['behavior'][ids],data['available'][ids],item,scale).numpy()
            u=nn.functional.normalize(data['base'][ids],dim=-1);v=nn.functional.normalize(item,dim=-1)
            return standardize(score),torch.cat([u,v,u*v,(u-v).abs()],dim=-1).numpy()
        hard=[]
        for r in train:
            keys=pools[r['profile_version']];score,_=vectors(r,keys,tr,ti)
            h=select_controls(r,keys,known[r['github_numeric_id']],dict(zip(keys,map(float,score))))
            assert set(h['comparison_issue_keys'])<=set(keys) and not set(h['comparison_issue_keys'])&known[r['github_numeric_id']]
            hard.append(h)
        write(output/f'train_controls-{seed}.json',[dict(outcome_id=r['outcome_id'],controls=r['comparison_issue_keys']) for r in hard])
        tx,_,names=build(hard,a,lin,c);tb=[];te=[]
        for r in hard:
            s,x=vectors(r,[r['positive_issue_key']]+r['comparison_issue_keys'],tr,ti);tb.append(s);te.append(x)
        mean=tx.mean(0);std=tx.std(0).clip(1e-6)
        tx=np.clip((tx-mean)/std,-5,5).reshape(-1,21,len(names));embedding=np.asarray(te,dtype=np.float32)
        fullx=[];fullb=[];slices={};queries={r['profile_version']:r for r in valid}
        for v,keys in plans.items():
            r=queries[v];s,x=vectors(r,keys,va,vi);slices[v]=(sum(map(len,fullb)),sum(map(len,fullb))+len(keys))
            packs=[]
            for offset in range(0,len(keys),21):
                chunk=keys[offset:offset+21];chunk += [chunk[-1]]*(21-len(chunk))
                packs.append(dict(profile_version=v,positive_issue_key=chunk[0],comparison_issue_keys=chunk[1:]))
            pair,_,_=build(packs,a,lin,c);pair=np.clip((pair[:len(keys)]-mean)/std,-5,5)
            fullx.append(np.column_stack([x,pair]));fullb.append(s)
        fullx=torch.tensor(np.concatenate(fullx),dtype=torch.float32);fullb=torch.tensor(np.concatenate(fullb),dtype=torch.float32)
        baseline=json.loads((base/f'coder_primary_20261108_v2/coder_v3-{seed}/ranks.json').read_text())
        def rank(scores):
            rr=[]
            for r in valid:
                start,end=slices[r['profile_version']];keys=plans[r['profile_version']];s=scores[start:end]
                repo=r['positive_issue_key'].split('#')[0];same=[i for i,k in enumerate(keys) if k.split('#')[0]==repo]
                rr.append(dict(outcome_id=r['outcome_id'],github_numeric_id=r['github_numeric_id'],rank=rank_of(keys,s,r['positive_issue_key']),
                    same_rank=rank_of([keys[i] for i in same],s[same],r['positive_issue_key']),same_candidates=len(same),
                    novel=not any(h[0]==repo for h in lin[r['profile_version']]['retained_X'])))
            return rr
        assert [r['rank'] for r in rank(fullb.numpy())]==[r['rank'] for r in baseline]
        for arm in protocol['arms']:
            print(json.dumps(dict(seed=seed,arm=arm,stage='training')),flush=True)
            selected=np.concatenate([embedding,tx[...,:16] if arm=='within_repo_only' else tx],axis=-1)
            x=torch.tensor(selected,dtype=torch.float32)
            xb=fullx[:,:144] if arm=='within_repo_only' else fullx
            trainbase=torch.tensor(np.asarray(tb))
            evalbase=fullb
            started=time.perf_counter();within=torch.tensor([[k.split('#')[0]==r['positive_issue_key'].split('#')[0] for k in [r['positive_issue_key']]+r['comparison_issue_keys']] for r in hard])
            model,history=train_head(x,trainbase,weights,seed,protocol['epochs'],within if 'within' in arm else None)
            d=output/f'{arm}-{seed}';d.mkdir();torch.save(model.state_dict(),d/'weights.pt')
            write(d/'normalizer.json',dict(mean=mean.tolist(),std=std.tolist(),pair_features=names,fit='TRAIN hard-control pairs only'))
            restored=GatedFusion(x.shape[-1]-128);restored.load_state_dict(torch.load(d/'weights.pt',weights_only=True));model.eval();restored.eval()
            with torch.no_grad():scores=model(xb,evalbase);again=restored(xb,evalbase)
            assert torch.equal(scores,again) and torch.isfinite(scores).all() and float(scores.abs().max())<=2.250001
            ranked=rank(scores.numpy());ranked_all[arm,seed]=ranked
            if True:
                with torch.no_grad():pairscore,coderpart,gates=model.components(xb,evalbase)
                disabled=rank(pairscore.numpy())
                diagnostics=dict(mean_gate=float(gates.mean()),mean_absolute_coder_contribution=float(coderpart.abs().mean()),
                    max_absolute_coder_contribution=float(coderpart.abs().max()),
                    changed_target_ranks=sum(r['rank']!=b['rank'] for r,b in zip(ranked,disabled)),
                    interpretation='Within-trained-model branch-disable diagnostic only, not independent causal gain')
                write(d/'coder_channel.json',diagnostics)
            novel=[r for r in ranked if r['novel']];same=[r for r in ranked if r['same_candidates']>1]
            rec=dict(arm=arm,seed=seed,metrics=metrics([r['rank'] for r in ranked],ranked),
                novel=metrics([r['rank'] for r in novel],novel),same_repository_mrr=statistics.mean(1/r['same_rank'] for r in same),seconds=time.perf_counter()-started)
            records.append(rec);write(d/'ranks.json',ranked);write(d/'history.json',history);write(output/'results.json',records);print(json.dumps(rec),flush=True)
    summary={}
    for arm in protocol['arms']:
        rr=[r for r in records if r['arm']==arm];deltas=[]
        for seed in protocol['seeds']:
            baseline=json.loads((base/f'coder_primary_20261108_v2/coder_v3-{seed}/ranks.json').read_text())
            for r,b in zip(ranked_all[arm,seed],baseline):deltas.append(dict(github_numeric_id=r['github_numeric_id'],delta=1/r['rank']-1/b['rank']))
        summary[arm]=dict(means={k:statistics.mean(r['metrics'][k] for r in rr) for k in ['developer_macro_mrr','mrr','recall5','ndcg5']},
            novel_recall5=statistics.mean(r['novel']['recall5'] for r in rr),same_repository_mrr=statistics.mean(r['same_repository_mrr'] for r in rr),paired_vs_coder_v3=ci(deltas))
    assert all(sha(Path(p))==h for p,h in hashes.items())
    write(output/'summary.json',summary);write(output/'verification.json',dict(coder_baseline_ranks_reproduced=True,reloaded_scores_identical=True,train_control_boundaries_verified=True,input_hashes_verified=True,no_tree_used=True))
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=Path.cwd())
    p.add_argument('--assets',type=Path,default=Path('D:/OSS-Mentor-assets/pr39'));p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.root,a.assets,a.output)
