"""离线编码新公开证据文本数据，不载入旧 CODER/Adapter 权重或图。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'tmp/pr39-review/CODER/src')]


def rows(p):return [json.loads(l) for l in p.read_text(encoding='utf-8').splitlines()]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def run(dataset,profiles,assets,output):
    os.environ['HF_HUB_CACHE']=str(assets/'encoding-assets/hf_hub')
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    import torch
    from coder_issue.models.text_encoder import FrozenTransformerEncoder
    torch.set_num_threads(4)
    weights=assets/'encoding-assets/hf_hub/models--sentence-transformers--all-MiniLM-L6-v2/snapshots/c21050a7ef692090620a6d037dd736908f9c7cf6/pytorch_model.bin'
    if sha(weights)!='c3a85f238711653950f6a79ece63eb0ea93d76f6a6284be04019c53733baf256':raise ValueError('Encoder drift')
    samples=rows(dataset/'samples.jsonl')
    versions=sorted({s['profile_version'] for s in samples})
    issues=sorted({k for s in samples for k in [s['positive_issue_key']]+s['comparison_issue_keys']})
    pmap={p['profile_version']:p for p in rows(profiles/'profiles.jsonl')}
    lineages={p['profile_version']:p for p in rows(profiles/'lineage.jsonl')}
    idx={(r['repository'],r['commit_sha'],r['path']):r for r in rows(ROOT/'tmp/pr39-review/datasets/temporal_xy_2024_2026_v3/file_contributions.jsonl')}
    docs=json.loads((ROOT/'data/midterm/working/full_x_materials_20261107_v1/documents.json').read_text())
    selected={};texts={}
    for v in versions:
        ordered=sorted(lineages[v]['retained_X'],key=lambda k:(idx[tuple(k)]['event_at'],k),reverse=True)
        seen=set();keys=[]
        for key in ordered:
            r=idx[tuple(key)]
            if r['content_sha256'] in seen:continue
            seen.add(r['content_sha256'])
            dkey=hashlib.sha256(json.dumps(key).encode()).hexdigest();d=docs[dkey]
            path=Path(d['content_path'])
            if sha(path)!=d['content_sha256']:raise ValueError('File content changed')
            texts[dkey]='[FILE] '+d['path']+'\n[CONTENT]\n'+path.read_text(encoding='utf-8',errors='replace')[:8192]
            keys.append(dkey)
            if len(keys)==3:break
        selected[v]=keys
    crows={c['issue_key']:c for c in rows(dataset/'candidates.jsonl')}
    for key in issues:texts['issue:'+key]='[ISSUE] '+crows[key]['title']+'\n'+crows[key]['body_text'][:8192]
    output.mkdir(parents=True,exist_ok=False)
    protocol=dict(encoder_sha256=sha(weights),revision='c21050a7ef692090620a6d037dd736908f9c7cf6',max_tokens=256,
        max_characters=8192,profile_selection='latest maximum3 distinct hashes from explicitly public subset, no Y',
        legacy_graph_or_weights_read=False,profiles=len(versions),issues=len(issues),documents=len(texts),
        input_hashes={str(p):sha(p) for p in [dataset/'samples.jsonl',dataset/'candidates.jsonl',profiles/'profiles.jsonl',profiles/'lineage.jsonl']},
        source_sha256=sha(Path(__file__)))
    (output/'protocol.json').write_text(json.dumps(protocol,indent=2))
    (output/'selected_files.json').write_text(json.dumps(selected,indent=2))
    encoder=FrozenTransformerEncoder('sentence-transformers/all-MiniLM-L6-v2',revision=protocol['revision'],allow_download=False,max_tokens=256)
    keys=sorted(texts);vectors=[]
    for start in range(0,len(keys),32):
        vectors.extend(encoder.encode([texts[k] for k in keys[start:start+32]]))
        if start%640==0:print(json.dumps(dict(encoded=min(start+32,len(keys)),total=len(keys))),flush=True)
    by=dict(zip(keys,vectors))
    user=np.asarray([np.mean([by[k] for k in selected[v]],axis=0) for v in versions],dtype=np.float32)
    item=np.asarray([by['issue:'+k] for k in issues],dtype=np.float32)
    assert np.isfinite(user).all() and np.isfinite(item).all()
    values=np.asarray([pmap[v]['values'] for v in versions],dtype=np.float32)
    masks=np.asarray([pmap[v]['observed_mask'] for v in versions],dtype=np.float32)
    np.savez_compressed(output/'text_features.npz',profile_versions=np.asarray(versions),issue_keys=np.asarray(issues),user=user,item=item,values=values,masks=masks)
    (output/'complete.json').write_text(json.dumps(dict(sha256=sha(output/'text_features.npz'),profiles=len(versions),issues=len(issues)),indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('dataset','profiles','assets','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();run(a.dataset,a.profiles,a.assets,a.output)
