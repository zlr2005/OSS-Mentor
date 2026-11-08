"""真实历史候选上校验NumPy导出与PyTorch权重的分数/排序及CPU耗时。"""
import argparse
import json
from pathlib import Path
import statistics
import sys
import time
import numpy as np
import torch
from torch import nn
from train_coder_multi_positive import old,rows,sha,write,rank_of,metrics,pair_matrix,GatedFusion
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from oss_mentor.gated_scoring import NumpyGatedModel


def run(bundle,output):
    torch.set_num_threads(1);root=Path.cwd();base=root/'data/midterm/working';assets=Path('D:/OSS-Mentor-assets/pr39');inputs=base/'rc1_training_inputs_20261106_v1'
    model=NumpyGatedModel(bundle);seed=model.manifest['seed'];arm=model.manifest['arm'];experiment=base/'coder_multi_positive_20261108_v1'
    p=json.loads((experiment/'protocol.json').read_text());assert all(sha(Path(path))==h for path,h in p['input_sha256'].items())
    statepath=experiment/f'{arm}-{seed}/weights.pt';assert sha(statepath)==model.manifest['original_weights_sha256']
    output.mkdir(parents=True,exist_ok=False)
    samples=rows(base/'public_text_dataset_20261107_v2/samples.jsonl');valid=[r for r in samples if r['split']=='validation']
    candidates={r['issue_key']:r for r in rows(base/'public_text_dataset_20261107_v2/candidates.jsonl')};lin={r['profile_version']:r for r in rows(base/'public_x_profiles_20261107_v1/lineage.jsonl')}
    plans=json.loads((base/'expanded_pool_20261107_v2/candidate_plans.json').read_text());vp=[plans[r['profile_version']] for r in valid]
    with np.load(base/'public_text_features_20261107_v1/text_features.npz',allow_pickle=False) as f:arrays={k:f[k].copy() for k in f.files}
    pair,_=pair_matrix(valid,vp,arrays,lin,candidates)
    config=old.AdvancedTrainingConfig.from_path(assets/'runs/public-supplement-20261106-v2/training_config.json')
    frozen=old.load_frozen_inputs(assets/'encoding-assets/coder_bundle/data/models/gfibot-m0-v1.1/coder/coder-full-search8-v1',inputs/'historical_issue_embeddings.npz')
    splits,_,_=old.prepare_training_data(profiles_path=inputs/'profiles.jsonl',outcomes_path=inputs/'advanced_issue_outcomes.jsonl',candidates_path=inputs/'historical_issue_candidates.jsonl',frozen=frozen,config=config)
    profiles={r['profile_version']:r for r in rows(inputs/'profiles.jsonl')};firsts=[g[0] for g in old.groups(splits['train']).values()]
    adapter=old.ProfileResidualAdapter(old.ProfileAdapterConfig(embedding_dim=32,hidden_dim=32,residual_scale=.75,gate_mode='none'));adapter.load_state_dict(torch.load(assets/f'runs/bottleneck-diagnostics-20261106-v1/original-{seed}/weights.pt',weights_only=True));adapter.eval()
    fusion=old.HistoryFusion(len(old.FEATURE_NAMES));fusion.load_state_dict(torch.load(assets/f'runs/history-fusion-v2-20261106-v1/learned_behavior-{seed}/weights.pt',weights_only=True));fusion.eval()
    ranker=old.TransferHead(False,True);d=assets/f'runs/transfer-cold-v3-20261107-v1/cosine-{seed}';ranker.load_state_dict(torch.load(d/'weights.pt',weights_only=True));ranker.eval();scale=torch.tensor(json.loads((d/'result.json').read_text())['score_scale'])
    _,norm=old.prepare_tensors(splits['train'],firsts,frozen,profiles,adapter);data,_=old.prepare_tensors(splits['validation'],firsts,frozen,profiles,adapter,norm)
    with torch.no_grad():data['base']=old.users(data,fusion)[0].clone()
    lookup={r.outcome_id:i for i,r in enumerate(data['firsts'])};torchmodel=GatedFusion();torchmodel.load_state_dict(torch.load(statepath,weights_only=True));torchmodel.eval()
    records=[];maxerror=0.;offset=0;bench=None
    for r,keys in zip(valid,vp):
        i=lookup[r['outcome_id']];assert r['parent_profile_version']==data['firsts'][i].profile_version
        item=torch.stack([frozen.issue_embeddings[k] for k in keys]);ids=torch.tensor([i]*len(keys))
        with torch.no_grad():raw=ranker(data['base'][ids],data['original'][ids],data['values'][ids],data['mask'][ids],data['behavior'][ids],data['available'][ids],item,scale).numpy()
        u=nn.functional.normalize(data['base'][ids],dim=-1);v=nn.functional.normalize(item,dim=-1);coder=torch.cat([u,v,u*v,(u-v).abs()],-1).numpy()
        pp=pair[offset:offset+len(keys)];offset+=len(keys);normalized=np.clip((pp-model.w['pair_mean'])/model.w['pair_std'],-5,5)
        bb=(raw-raw.mean())/max(float(raw.std()),1e-8);xx=torch.tensor(np.column_stack([coder,normalized]),dtype=torch.float32);bt=torch.tensor(bb)
        with torch.no_grad():expected=torchmodel(xx,bt).numpy()
        actual=model.predict(coder,pp,raw);maxerror=max(maxerror,float(np.max(np.abs(expected-actual))))
        if rank_of(keys,expected,r['positive_issue_key'])!=rank_of(keys,actual,r['positive_issue_key']):raise ValueError('NumPy changes target rank; do not promote this export')
        records.append(dict(outcome_id=r['outcome_id'],github_numeric_id=r['github_numeric_id'],rank=rank_of(keys,actual,r['positive_issue_key'])))
        if bench is None:bench=(coder,pp,raw,xx,bt)
    saved=json.loads((experiment/f'{arm}-{seed}/ranks.json').read_text());assert [r['rank'] for r in records]==[r['rank'] for r in saved]
    assert maxerror<1e-5
    numpy_time=[];torch_time=[];coder,pp,raw,xx,bt=bench
    for _ in range(30):
        t=time.perf_counter();model.predict(coder,pp,raw);numpy_time.append(1000*(time.perf_counter()-t))
        t=time.perf_counter()
        with torch.no_grad():torchmodel(xx,bt)
        torch_time.append(1000*(time.perf_counter()-t))
    write(output/'ranks.json',records)
    result=dict(seed=seed,arm=arm,metrics=metrics([r['rank'] for r in records],records),max_abs_score_error=maxerror,
        all209_saved_target_ranks_reproduced=True,numpy_ms_median=statistics.median(numpy_time),torch_ms_median=statistics.median(torch_time),
        benchmark_scope='one fixed historical query,30iterations,preloaded weights/features; numpy includes normalization,torch only network; not HTTP/network/feature generation latency',
        runtime_model_bytes=(bundle/'weights.npz').stat().st_size,independent_evaluation=False)
    write(output/'verification.json',result);print(json.dumps(result,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bundle',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.bundle,a.output)
