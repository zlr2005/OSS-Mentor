"""公开文本新版本的从头训练学习曲线；不载入旧图或旧训练权重。"""
import argparse
import copy
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics
import time
import numpy as np
import torch
from torch import nn
from build_audited_curve_plan import nested_users


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,v):p.write_text(json.dumps(v,indent=2),encoding='utf-8')


class TextRanker(nn.Module):
    def __init__(self):
        super().__init__()
        self.user=nn.Sequential(nn.Linear(430,64),nn.GELU(),nn.Linear(64,32))
        self.item=nn.Linear(384,32)

    def forward(self,user,item):
        u=nn.functional.normalize(self.user(user),dim=-1)
        i=nn.functional.normalize(self.item(item),dim=-1)
        return 10*(u[:,None,:]*i).sum(-1)


def prepare(samples,features):
    pm={v:i for i,v in enumerate(features['profile_versions'].astype(str))}
    im={k:i for i,k in enumerate(features['issue_keys'].astype(str))}
    ids=[pm[r['profile_version']] for r in samples]
    user=np.concatenate([features['user'][ids],features['values'][ids]*features['masks'][ids],features['masks'][ids]],axis=1)
    item=features['item'][[[im[k] for k in [r['positive_issue_key']]+r['comparison_issue_keys']] for r in samples]]
    return dict(user=torch.tensor(user),item=torch.tensor(item),rows=samples)


def metrics(ranks,rows):
    by=defaultdict(list)
    for rank,row in zip(ranks,rows):by[row['github_numeric_id']].append(1/rank)
    return dict(developer_macro_mrr=statistics.mean(statistics.mean(v) for v in by.values()),
        mrr=statistics.mean(1/r for r in ranks),recall5=statistics.mean(r<=5 for r in ranks),
        ndcg5=statistics.mean(1/math.log2(r+1) if r<=5 else 0 for r in ranks),developers=len(by),outcomes=len(rows))


def evaluate(model,data):
    model.eval()
    with torch.no_grad():
        score=model(data['user'],data['item'])
        if not torch.isfinite(score).all():raise ValueError('Nonfinite scores')
        return (1+(score[:,1:]>=score[:,:1]).sum(1)).tolist()


def fit(train,valid,seed):
    torch.manual_seed(seed)
    model=TextRanker();opt=torch.optim.AdamW(model.parameters(),lr=.001,weight_decay=.0001)
    counts=defaultdict(int)
    for r in train['rows']:counts[r['github_numeric_id']]+=1
    weights=torch.tensor([1/counts[r['github_numeric_id']] for r in train['rows']])
    best=-1;state=None;history=[];stale=0
    for epoch in range(1,61):
        model.train();total=0
        ids=np.random.default_rng(seed+epoch).permutation(len(train['rows']))
        for start in range(0,len(ids),64):
            ix=ids[start:start+64]
            score=model(train['user'][ix],train['item'][ix])
            loss=(-nn.functional.logsigmoid(score[:,:1]-score[:,1:]).mean(1)*weights[ix]).sum()/weights[ix].sum()
            if not torch.isfinite(loss):raise ValueError('Nonfinite loss')
            opt.zero_grad();loss.backward();opt.step();total+=float(loss.detach())*len(ix)
        ranks=evaluate(model,valid);primary=metrics(ranks,valid['rows'])['developer_macro_mrr']
        history.append(dict(epoch=epoch,loss=total/len(ids),validation_macro_mrr=primary))
        if primary>best:best=primary;state=copy.deepcopy(model.state_dict());stale=0
        else:stale+=1
        if epoch>=10 and stale>=8:break
    model.load_state_dict(state)
    assert all(torch.isfinite(v).all() for v in model.state_dict().values())
    return model,history


def run(dataset,features,output):
    torch.set_num_threads(1)
    samples=[json.loads(l) for l in (dataset/'samples.jsonl').read_text(encoding='utf-8').splitlines()]
    if sha(features/'text_features.npz')!=json.loads((features/'complete.json').read_text())['sha256']:raise ValueError('Feature drift')
    with np.load(features/'text_features.npz',allow_pickle=False) as f:arrays={k:f[k].copy() for k in f.files}
    train=[r for r in samples if r['split']=='train'];valid=prepare([r for r in samples if r['split']=='validation'],arrays)
    users={r['github_numeric_id'] for r in train}
    output.mkdir(parents=True,exist_ok=False)
    protocol=dict(seeds=list(range(20261102,20261107)),fractions=[.25,.5,.75,1.],primary='developer_macro_mrr',
        model='fresh two-tower: user430-64-32 GELU; item384-32; cosine times10',loss='BPR20 with inverse developer-frequency outcome weights',
        epochs=60,patience=8,min_epochs=10,batch_outcomes=64,learning_rate=.001,weight_decay=.0001,
        selection='same development validation across sizes; no independent test evaluation',
        sampling='nested developer groups; seed-specific deterministic hash order',
        pretrained='pinned MiniLM only; its pretraining corpus is not independently audited',
        legacy_CODER_graph_or_weights_read=False,evaluate_test=False,future_evaluation_read=False,
        input_hashes={str(p):sha(p) for p in [dataset/'samples.jsonl',features/'text_features.npz',features/'protocol.json',Path(__file__)]})
    write(output/'protocol.json',protocol)
    # A non-trained baseline on exactly the same sampled candidate lists.
    pm={v:i for i,v in enumerate(arrays['profile_versions'].astype(str))}
    u=torch.tensor(arrays['user'][[pm[r['profile_version']] for r in valid['rows']]])
    score=nn.functional.cosine_similarity(u[:,None,:],valid['item'],dim=-1)
    ranks=(1+(score[:,1:]>=score[:,:1]).sum(1)).tolist()
    write(output/'untrained_cosine.json',metrics(ranks,valid['rows']))
    records=[]
    for seed in protocol['seeds']:
        ordered=nested_users(users,seed)
        for fraction in protocol['fractions']:
            chosen=set(ordered[:math.ceil(len(ordered)*fraction)])
            subset=[r for r in train if r['github_numeric_id'] in chosen]
            data=prepare(subset,arrays);directory=output/f'{seed}-{fraction:.2f}';directory.mkdir()
            write(directory/'assignment.json',dict(developers=sorted(chosen),outcomes=[r['outcome_id'] for r in subset]))
            started=time.monotonic();model,history=fit(data,valid,seed)
            ranks=evaluate(model,valid)
            torch.save(model.state_dict(),directory/'weights.pt')
            restored=TextRanker();restored.load_state_dict(torch.load(directory/'weights.pt',weights_only=True))
            assert ranks==evaluate(restored,valid)
            write(directory/'ranks.json',[dict(outcome_id=r['outcome_id'],github_numeric_id=r['github_numeric_id'],rank=rank) for r,rank in zip(valid['rows'],ranks)])
            write(directory/'history.json',history)
            record=dict(seed=seed,fraction=fraction,train_users=len(chosen),train_outcomes=len(subset),epochs=len(history),seconds=time.monotonic()-started,metrics=metrics(ranks,valid['rows']))
            records.append(record);write(output/'results.json',records)
            print(json.dumps(record),flush=True)
    summary={str(f):{k:statistics.mean(r['metrics'][k] for r in records if r['fraction']==f) for k in ('developer_macro_mrr','mrr','recall5','ndcg5')} for f in protocol['fractions']}
    write(output/'summary.json',dict(means=summary,models=len(records),epochs=sum(r['epochs'] for r in records),seconds=sum(r['seconds'] for r in records),independent_generalization=False))
    assert all(sha(Path(p))==h for p,h in protocol['input_hashes'].items())


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('dataset','features','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();run(a.dataset,a.features,a.output)
