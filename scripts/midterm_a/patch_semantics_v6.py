"""获证共同文件上的路径、完整前缀和 patch 消融，仅 train/validation；底座公开性未完成审计。"""
import argparse,copy,json,statistics,time
from dataclasses import replace
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tmp/pr39-review/scripts/midterm_a'))
import numpy as np
import torch
from torch import nn
from transfer_cold_v3 import (TransferHead,evaluate,summarize,HistoryFusion,FEATURE_NAMES,prepare_tensors,users,
    AdvancedTrainingConfig,load_frozen_inputs,prepare_training_data,ProfileResidualAdapter,ProfileAdapterConfig,
    _set_seed,_batch_indices,groups,sha,write,bootstrap)


def pca_features(matrix,training_indices,available):
    normalized=matrix/np.maximum(np.linalg.norm(matrix,axis=1,keepdims=True),1e-12)
    training_indices=[i for i in training_indices if available[i]]
    if len(training_indices)<33:raise ValueError('Insufficient available training profiles for PCA32')
    selected=normalized[training_indices];mean=selected.mean(0)
    _,_,v=np.linalg.svd(selected-mean,full_matrices=False);basis=v[:32].T
    projected=(normalized-mean)@basis;std=projected[training_indices].std(0).clip(1e-6)
    values=np.clip(projected/std,-5,5).astype(np.float32);values[~available]=0
    return torch.tensor(values),dict(mean=mean.tolist(),basis=basis.tolist(),std=std.tolist(),fit='unique training profile versions only')


class SemanticHead(nn.Module):
    def __init__(self):
        super().__init__();self.net=nn.Sequential(nn.Linear(78,16),nn.ReLU(),nn.Linear(16,32))
        nn.init.zeros_(self.net[-1].weight);nn.init.zeros_(self.net[-1].bias)

    def forward(self,base,values,mask,semantic,available):
        delta=torch.tanh(self.net(torch.cat([values*mask,mask.float(),semantic],-1)))
        delta=delta/delta.norm(dim=-1,keepdim=True).clamp_min(1)*base.norm(dim=-1,keepdim=True)*.25
        return base+torch.where(available[:,None],delta,torch.zeros_like(delta))


def evaluate_sem(model,data,ranker,frozen,scale,repos):
    if model is None:return evaluate(ranker,data,frozen,scale,repos)
    model.eval()
    with torch.no_grad():
        changed=dict(data,base=model(data['base'],data['values'],data['mask'],data['semantic'],data['semantic_available']))
    return evaluate(ranker,changed,frozen,scale,repos)


def fit(train,valid,ranker,frozen,scale,repos,config):
    _set_seed(config.seed);model=SemanticHead()
    optimizer=torch.optim.AdamW(model.parameters(),lr=config.learning_rate,weight_decay=config.weight_decay)
    best=-1;state=None;stale=0;history=[]
    for epoch in range(1,config.epochs+1):
        model.train();total=0
        for ids in _batch_indices(len(train['row_index']),128,seed=config.seed,epoch=epoch):
            ix=train['row_index'][ids]
            user=model(train['base'][ix],train['values'][ix],train['mask'][ix],train['semantic'][ix],train['semantic_available'][ix])
            def score(item):return ranker(user,train['original'][ix],train['values'][ix],train['mask'][ix],train['behavior'][ix],train['available'][ix],item,scale)
            margin=score(train['positive'][ids])-score(train['comparison'][ids])
            weights=train['weight'][ids]*(1+torch.sigmoid(-margin.detach()))
            loss=(-nn.functional.logsigmoid(margin)*weights).sum()/weights.sum()
            optimizer.zero_grad();loss.backward();optimizer.step();total+=float(loss.detach())*len(ids)
        output=evaluate_sem(model,valid,ranker,frozen,scale,repos);mrr=statistics.mean(1/r['rank'] for r in output)
        history.append(dict(epoch=epoch,train_loss=total/len(train['row_index']),validation_mrr=mrr))
        if mrr>best:best=mrr;state=copy.deepcopy(model.state_dict());stale=0
        else:
            stale+=1
            if stale>=config.patience:break
    model.load_state_dict(state);return model,history


def run(a):
    torch.set_num_threads(1)
    base=a.data_root/'data/midterm/working/rc1_training_inputs_20261106_v1'
    config=AdvancedTrainingConfig.from_path(a.assets/'runs/public-supplement-20261106-v2/training_config.json')
    frozen=load_frozen_inputs(a.assets/'encoding-assets/coder_bundle/data/models/gfibot-m0-v1.1/coder/coder-full-search8-v1',base/'historical_issue_embeddings.npz')
    splits,_,audit=prepare_training_data(profiles_path=base/'profiles.jsonl',outcomes_path=base/'advanced_issue_outcomes.jsonl',candidates_path=base/'historical_issue_candidates.jsonl',frozen=frozen,config=config)
    splits={k:splits[k] for k in ('train','validation')}
    profiles={p['profile_version']:p for p in [json.loads(l) for l in (base/'profiles.jsonl').read_text().splitlines()]}
    firsts=[g[0] for g in groups(splits['train']).values()];repos={r.positive_issue_key.split('#')[0] for r in firsts}
    cache=a.semantics/'profile_semantics.npz';complete=json.loads((a.semantics/'encoding_complete.json').read_text())
    assert sha(cache)==complete['cache_sha256']
    with np.load(cache) as p:
        versions=p['profile_versions'].astype(str).tolist();available=p['available'].copy();matrices={k:p[k].copy() for k in ('path','content','patch')}
    mapping={v:i for i,v in enumerate(versions)};train_indices=sorted({mapping[r.profile_version] for r in firsts})
    transformed={};normalizers={}
    for view in matrices:transformed[view],normalizers[view]=pca_features(matrices[view],train_indices,available)
    a.output.mkdir(parents=True,exist_ok=False)
    write(a.output/'pca.json',normalizers)
    protocol=dict(arms=['v3_reference','path','content','patch'],seeds=list(range(20261102,20261107)),audit=audit,
        input_hashes={p.name:sha(p) for p in base.iterdir() if p.is_file()},semantic_cache_sha256=sha(cache),
        semantic_protocol_sha256=sha(a.semantics/'protocol.json'),source_sha256=sha(Path(__file__)),
        train_unique_profiles=len(train_indices),PCA_fit='unique available training profiles only, no labels',base_training_publication_audit_complete=False,research_only=True,residual_cap=.25,
        selection='validation MRR only',epochs=config.epochs,patience=config.patience,
        evaluate_test=False,external_evaluation_read=False,production_model_replaced=False)
    project_root=Path(__file__).resolve().parents[2]
    protocol['dependency_hashes']={str(Path(m.__file__).resolve()):sha(Path(m.__file__)) for m in list(sys.modules.values())
        if getattr(m,'__file__',None) and str(m.__file__).endswith('.py') and Path(m.__file__).is_file() and Path(m.__file__).resolve().is_relative_to(project_root)}
    write(a.output/'protocol.json',protocol);records=[]
    write(a.output/'outcome_availability.json', {split: [dict(outcome_id=g[0].outcome_id,
        profile_version=g[0].profile_version,semantic_available=bool(available[mapping[g[0].profile_version]]))
        for g in groups(rows).values()] for split,rows in splits.items()})
    for seed in protocol['seeds']:
        adapter=ProfileResidualAdapter(ProfileAdapterConfig(embedding_dim=32,hidden_dim=32,residual_scale=.75,gate_mode='none'))
        rc1=a.assets/f'runs/bottleneck-diagnostics-20261106-v1/original-{seed}/weights.pt';adapter.load_state_dict(torch.load(rc1,weights_only=True));adapter.eval()
        v2=HistoryFusion(len(FEATURE_NAMES));v2_path=a.assets/f'runs/history-fusion-v2-20261106-v1/learned_behavior-{seed}/weights.pt';v2.load_state_dict(torch.load(v2_path,weights_only=True));v2.eval()
        ranker=TransferHead(False,True);v3=a.assets/f'runs/transfer-cold-v3-20261107-v1/cosine-{seed}/weights.pt';ranker.load_state_dict(torch.load(v3,weights_only=True));ranker.eval()
        for param in ranker.parameters():param.requires_grad_(False)
        train,norm=prepare_tensors(splits['train'],firsts,frozen,profiles,adapter);valid,_=prepare_tensors(splits['validation'],firsts,frozen,profiles,adapter,norm)
        for data in (train,valid):
            with torch.no_grad():data['base']=users(data,v2)[0].clone()
            ids=[mapping[r.profile_version] for r in data['firsts']];data['semantic_indices']=ids;data['semantic_available']=torch.tensor(available[ids])
        previous=json.loads((v3.parent/'result.json').read_text());scale=torch.tensor(previous['score_scale'])
        reference=evaluate_sem(None,valid,ranker,frozen,scale,repos)
        assert reference==json.loads((v3.parent/'outcomes.json').read_text())
        for arm in protocol['arms']:
            print(json.dumps(dict(seed=seed,arm=arm,stage='training')),flush=True)
            directory=a.output/f'{arm}-{seed}';directory.mkdir();started=time.monotonic();history=[];model=None
            if arm!='v3_reference':
                for data in (train,valid):data['semantic']=transformed[arm][data['semantic_indices']]
                model,history=fit(train,valid,ranker,frozen,scale,repos,replace(config,seed=seed))
                torch.save(model.state_dict(),directory/'weights.pt');restored=SemanticHead();restored.load_state_dict(torch.load(directory/'weights.pt',weights_only=True))
                assert evaluate_sem(model,valid,ranker,frozen,scale,repos)==evaluate_sem(restored,valid,ranker,frozen,scale,repos)
            output=evaluate_sem(model,valid,ranker,frozen,scale,repos)
            assert all(row==old for row,old,has in zip(output,reference,valid['semantic_available'].tolist()) if not has)
            record=dict(arm=arm,seed=seed,metrics=summarize(output),paired=bootstrap(reference,output),
                epochs=len(history),seconds=time.monotonic()-started,v3_sha256=sha(v3),rc1_sha256=sha(rc1),v2_sha256=sha(v2_path),evaluate_test=False)
            write(directory/'outcomes.json',output);write(directory/'history.json',history);write(directory/'result.json',record)
            records.append(record);write(a.output/'results.json',records)
            print(json.dumps(dict(arm=arm,seed=seed,mrr=record['metrics']['mrr'],epochs=len(history))),flush=True)
    write(a.output/'summary.json',dict(means={arm:{k:statistics.mean(r['metrics'][k] for r in records if r['arm']==arm) for k in ('mrr','ndcg5','recall5','pair_accuracy')} for arm in protocol['arms']},evaluate_test=False,external_evaluation_read=False))
    assert all(sha(base/name)==digest for name,digest in protocol['input_hashes'].items())
    assert all(sha(Path(path))==digest for path,digest in protocol['dependency_hashes'].items())


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('data-root','assets','semantics','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();run(a)
