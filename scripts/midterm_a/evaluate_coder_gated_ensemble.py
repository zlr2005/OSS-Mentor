"""固定五seed查询内z标准化等权集成；两种训练方案同预算，不搜索权重。"""
import argparse
import json
from pathlib import Path
import numpy as np
import torch
from torch import nn
from train_coder_full_pool import old,rows,sha,write,pair_matrix,standardize,rank_of,metrics,ci,GatedFusion


def equal_ensemble(scores,slices):
    result=np.zeros(len(scores[0]),dtype=np.float64)
    for values in scores:
        if not np.isfinite(values).all():raise ValueError('Nonfinite seed scores')
        for start,end in slices:result[start:end]+=standardize(values[start:end])/len(scores)
    return result


def run(output):
    torch.set_num_threads(1);root=Path.cwd();base=root/'data/midterm/working';assets=Path('D:/OSS-Mentor-assets/pr39')
    full=base/'coder_full_pool_20261108_v1';previous=base/'coder_gated_20261108_v1'
    protocol=json.loads((full/'protocol.json').read_text());seeds=protocol['seeds']
    hashes=dict(protocol['input_sha256']);hashes[str(Path(__file__))]=sha(Path(__file__))
    for seed in seeds:
        for p in [full/f'full_pool_gated-{seed}/weights.pt',previous/f'gated_fusion-{seed}/weights.pt',previous/f'gated_fusion-{seed}/normalizer.json']:
            hashes[str(p)]=sha(p)
    hashes[str(full/'normalizer.json')]=sha(full/'normalizer.json')
    assert all(sha(Path(p))==h for p,h in hashes.items())
    output.mkdir(parents=True,exist_ok=False)
    write(output/'protocol.json',dict(arms=['previous_gated_ensemble','full_pool_ensemble'],seeds=seeds,
        rule='within-query zscore each seed (std floor1e-8), equal mean; conservative ties, no weight/seed selection',
        evaluate_test=False,independent_evaluation=False,input_sha256=hashes))
    dataset=base/'public_text_dataset_20261107_v2';samples=rows(dataset/'samples.jsonl');valid=[r for r in samples if r['split']=='validation']
    candidates={r['issue_key']:r for r in rows(dataset/'candidates.jsonl')};lineage={r['profile_version']:r for r in rows(base/'public_x_profiles_20261107_v1/lineage.jsonl')}
    original_plans=json.loads((base/'expanded_pool_20261107_v2/candidate_plans.json').read_text());plans=[original_plans[r['profile_version']] for r in valid]
    slices=[];offset=0
    for keys in plans:slices.append((offset,offset+len(keys)));offset+=len(keys)
    inputs=base/'rc1_training_inputs_20261106_v1';config=old.AdvancedTrainingConfig.from_path(assets/'runs/public-supplement-20261106-v2/training_config.json')
    frozen=old.load_frozen_inputs(assets/'encoding-assets/coder_bundle/data/models/gfibot-m0-v1.1/coder/coder-full-search8-v1',inputs/'historical_issue_embeddings.npz')
    splits,_,_=old.prepare_training_data(profiles_path=inputs/'profiles.jsonl',outcomes_path=inputs/'advanced_issue_outcomes.jsonl',candidates_path=inputs/'historical_issue_candidates.jsonl',frozen=frozen,config=config)
    profiles={r['profile_version']:r for r in rows(inputs/'profiles.jsonl')};firsts=[g[0] for g in old.groups(splits['train']).values()]
    with np.load(base/'public_text_features_20261107_v1/text_features.npz',allow_pickle=False) as f:arrays={k:f[k].copy() for k in f.files}
    pair,_=pair_matrix(valid,plans,arrays,lineage,candidates);scores={'previous_gated_ensemble':[],'full_pool_ensemble':[]}
    def ranking(values):
        rr=[]
        for r,keys,(start,end) in zip(valid,plans,slices):
            v=values[start:end];repo=r['positive_issue_key'].split('#')[0];same=[i for i,k in enumerate(keys) if k.split('#')[0]==repo]
            rr.append(dict(outcome_id=r['outcome_id'],github_numeric_id=r['github_numeric_id'],rank=rank_of(keys,v,r['positive_issue_key']),
                same_rank=rank_of([keys[i] for i in same],v[same],r['positive_issue_key']),same_candidates=len(same),
                novel=not any(h[0]==repo for h in lineage[r['profile_version']]['retained_X'])))
        return rr
    for seed in seeds:
        adapter=old.ProfileResidualAdapter(old.ProfileAdapterConfig(embedding_dim=32,hidden_dim=32,residual_scale=.75,gate_mode='none'))
        adapter.load_state_dict(torch.load(assets/f'runs/bottleneck-diagnostics-20261106-v1/original-{seed}/weights.pt',weights_only=True));adapter.eval()
        fusion=old.HistoryFusion(len(old.FEATURE_NAMES));fusion.load_state_dict(torch.load(assets/f'runs/history-fusion-v2-20261106-v1/learned_behavior-{seed}/weights.pt',weights_only=True));fusion.eval()
        ranker=old.TransferHead(False,True);directory=assets/f'runs/transfer-cold-v3-20261107-v1/cosine-{seed}'
        ranker.load_state_dict(torch.load(directory/'weights.pt',weights_only=True));ranker.eval();scale=torch.tensor(json.loads((directory/'result.json').read_text())['score_scale'])
        _,norm=old.prepare_tensors(splits['train'],firsts,frozen,profiles,adapter);data,_=old.prepare_tensors(splits['validation'],firsts,frozen,profiles,adapter,norm)
        with torch.no_grad():data['base']=old.users(data,fusion)[0].clone()
        lookup={r.outcome_id:i for i,r in enumerate(data['firsts'])};emb=[];basevalues=[]
        for r,keys in zip(valid,plans):
            i=lookup[r['outcome_id']];assert r['parent_profile_version']==data['firsts'][i].profile_version
            item=torch.stack([frozen.issue_embeddings[k] for k in keys]);ids=torch.tensor([i]*len(keys))
            with torch.no_grad():s=ranker(data['base'][ids],data['original'][ids],data['values'][ids],data['mask'][ids],data['behavior'][ids],data['available'][ids],item,scale)
            u=nn.functional.normalize(data['base'][ids],dim=-1);v=nn.functional.normalize(item,dim=-1)
            emb.append(torch.cat([u,v,u*v,(u-v).abs()],-1).numpy());basevalues.append(standardize(s.numpy()))
        embedding=np.concatenate(emb);bb=torch.tensor(np.concatenate(basevalues),dtype=torch.float32)
        for arm,modelpath,normpath,rankpath in [
            ('previous_gated_ensemble',previous/f'gated_fusion-{seed}/weights.pt',previous/f'gated_fusion-{seed}/normalizer.json',previous/f'gated_fusion-{seed}/ranks.json'),
            ('full_pool_ensemble',full/f'full_pool_gated-{seed}/weights.pt',full/'normalizer.json',full/f'full_pool_gated-{seed}/ranks.json')]:
            n=json.loads(normpath.read_text());normalized=np.clip((pair-np.asarray(n['mean'],dtype=np.float32))/np.asarray(n['std'],dtype=np.float32),-5,5)
            model=GatedFusion();model.load_state_dict(torch.load(modelpath,weights_only=True));model.eval()
            with torch.no_grad():values=model(torch.tensor(np.column_stack([embedding,normalized]),dtype=torch.float32),bb).numpy()
            ranked=ranking(values);saved=json.loads(rankpath.read_text())
            assert [r['rank'] for r in ranked]==[r['rank'] for r in saved]
            scores[arm].append(values)
    summary={};ranked={}
    for arm in scores:
        ranked[arm]=ranking(equal_ensemble(scores[arm],slices));rr=ranked[arm];novel=[r for r in rr if r['novel']]
        summary[arm]=dict(metrics=metrics([r['rank'] for r in rr],rr),novel=metrics([r['rank'] for r in novel],novel))
        write(output/f'{arm}.json',rr)
    delta=[dict(github_numeric_id=r['github_numeric_id'],delta=1/r['rank']-1/b['rank']) for r,b in zip(ranked['full_pool_ensemble'],ranked['previous_gated_ensemble'])]
    summary['paired_full_minus_previous']=ci(delta)
    assert all(sha(Path(p))==h for p,h in hashes.items())
    write(output/'summary.json',summary);write(output/'verification.json',dict(all10_single_seed_rankings_reproduced=True,input_hashes_verified=True,weights_or_seeds_tuned=False))
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path);run(p.parse_args().output)
