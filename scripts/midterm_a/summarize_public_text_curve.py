"""复核20次训练并计算开发者等权的条件性区间。"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import statistics
import numpy as np
import torch
from train_public_text_curve import TextRanker,prepare,metrics


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def macro_ci(before,after):
    by=defaultdict(list)
    for b,a in zip(before,after):
        old={r['outcome_id']:r for r in b}
        for r in a:
            by[r['github_numeric_id']].append(1/r['rank']-1/old[r['outcome_id']]['rank'])
    values=np.asarray([statistics.mean(v) for v in by.values()])
    ix=np.random.default_rng(20261107).integers(len(values),size=(4000,len(values)))
    return dict(developer_macro_delta=float(values.mean()),ci95=np.quantile(values[ix].mean(1),[.025,.975]).tolist(),developers=len(values))


def run(root,dataset,features):
    read=lambda p:json.loads(p.read_text())
    records=read(root/'results.json');protocol=read(root/'protocol.json')
    assert len(records)==20 and len(list(root.glob('*/weights.pt')))==20
    assert all(sha(Path(p))==h for p,h in protocol['input_hashes'].items())
    with np.load(features/'text_features.npz',allow_pickle=False) as f:a={k:f[k].copy() for k in f.files}
    samples=[json.loads(l) for l in (dataset/'samples.jsonl').read_text(encoding='utf-8').splitlines()]
    valid=prepare([r for r in samples if r['split']=='validation'],a)
    pm={v:i for i,v in enumerate(a['profile_versions'].astype(str))}
    user=torch.tensor(a['user'][[pm[r['profile_version']] for r in valid['rows']]])
    s=torch.nn.functional.cosine_similarity(user[:,None,:],valid['item'],dim=-1)
    ranks=(1+(s[:,1:]>=s[:,:1]).sum(1)).tolist()
    cosine=[dict(outcome_id=r['outcome_id'],github_numeric_id=r['github_numeric_id'],rank=rank) for r,rank in zip(valid['rows'],ranks)]
    assert metrics(ranks,valid['rows'])==read(root/'untrained_cosine.json')
    allranks={}
    for seed in protocol['seeds']:
        previous=set()
        for f in protocol['fractions']:
            d=root/f'{seed}-{f:.2f}'
            selected=set(read(d/'assignment.json')['developers'])
            assert previous<=selected;previous=selected
            state=torch.load(d/'weights.pt',weights_only=True,map_location='cpu')
            assert all(torch.isfinite(v).all() for v in state.values())
            allranks[seed,f]=read(d/'ranks.json')
    comparisons={}
    for name,before in [('full-minus-quarter',[allranks[s,.25] for s in protocol['seeds']]),
                        ('full-minus-threequarter',[allranks[s,.75] for s in protocol['seeds']]),
                        ('full-minus-cosine',[cosine]*5)]:
        after=[allranks[s,1.] for s in protocol['seeds']]
        comparisons[name]=macro_ci(before,after)
    summary=dict(read(root/'summary.json'),baseline=read(root/'untrained_cosine.json'),comparisons=comparisons,
        verification=dict(all20_finite=True,nested_groups=True,input_hashes=True,saved_reload_ranks_checked_during_training=True),
        caveat='Development validation reused for checkpointing; bootstrap conditional on fixed cohort and sampled candidate lists; not independent evaluation or proof more data will always help.')
    (root/'analysis.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('root','dataset','features'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();run(a.root,a.dataset,a.features)
