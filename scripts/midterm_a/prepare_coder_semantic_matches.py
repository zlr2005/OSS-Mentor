"""离线编码已冻结的历史文件与Issue标题，用于细粒度语义交互；不改变选材。"""
import argparse
import json
import os
from pathlib import Path
import sys
import numpy as np
from pfirec_inspired_ranker import rows,sha,write


def normalize(x):return x/np.maximum(np.linalg.norm(x,axis=-1,keepdims=True),1e-12)


def semantic_matches(user,files,title,item):
    u=normalize(user[None,:])[0];f=normalize(files);t=normalize(title[None,:])[0];i=normalize(item[None,:])[0]
    si=f@i;st=f@t
    return [float(u@t),float(si.max()),float(st.max()),float(si.std()),float(si[0]),float(st[0])]


NAMES=['user_title_cosine','best_file_issue_cosine','best_file_title_cosine','file_issue_cosine_std','recent_file_issue_cosine','recent_file_title_cosine']


def run(output):
    root=Path.cwd();base=root/'data/midterm/working';assets=Path('D:/OSS-Mentor-assets/pr39')
    original=base/'public_text_features_20261107_v1';dataset=base/'public_text_dataset_20261107_v2'
    selected=json.loads((original/'selected_files.json').read_text());docs=json.loads((base/'full_x_materials_20261107_v1/documents.json').read_text())
    candidates={r['issue_key']:r for r in rows(dataset/'candidates.jsonl')}
    with np.load(original/'text_features.npz',allow_pickle=False) as z:
        versions=z['profile_versions'].astype(str).tolist();issues=z['issue_keys'].astype(str).tolist();users=z['user'].copy()
    paths=[original/'text_features.npz',original/'selected_files.json',dataset/'candidates.jsonl',base/'full_x_materials_20261107_v1/documents.json',Path(__file__)]
    texts={}
    for key in sorted({k for v in versions for k in selected[v]}):
        d=docs[key];p=Path(d['content_path'])
        if sha(p)!=d['content_sha256']:raise ValueError('Historical file changed')
        texts['file:'+key]='[FILE] '+d['path']+'\n[CONTENT]\n'+p.read_text(encoding='utf-8',errors='replace')[:8192]
        paths.append(p)
    for key in issues:texts['title:'+key]='[ISSUE] '+candidates[key]['title']
    weights=assets/'encoding-assets/hf_hub/models--sentence-transformers--all-MiniLM-L6-v2/snapshots/c21050a7ef692090620a6d037dd736908f9c7cf6/pytorch_model.bin'
    if sha(weights)!='c3a85f238711653950f6a79ece63eb0ea93d76f6a6284be04019c53733baf256':raise ValueError('Encoder changed')
    paths.append(weights);output.mkdir(parents=True,exist_ok=False)
    hashes={str(p):sha(p) for p in paths}
    write(output/'protocol.json',dict(feature_names=NAMES,encoder='frozen all-MiniLM-L6-v2 c21050a7ef692090620a6d037dd736908f9c7cf6',
        max_tokens=256,selection='exact original selected_files, maximum3 latest cutoff-safe distinct-content files; no labels or new selection',
        titles='original admitted historical title only',documents=len(texts),input_sha256=hashes,evaluate_test=False,research_data_uploaded=False))
    os.environ['HF_HUB_CACHE']=str(assets/'encoding-assets/hf_hub');os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    sys.path.insert(0,str(root/'tmp/pr39-review/CODER/src'))
    import torch
    from coder_issue.models.text_encoder import FrozenTransformerEncoder
    torch.set_num_threads(4)
    encoder=FrozenTransformerEncoder('sentence-transformers/all-MiniLM-L6-v2',revision='c21050a7ef692090620a6d037dd736908f9c7cf6',allow_download=False,max_tokens=256)
    keys=sorted(texts);vectors=[]
    for start in range(0,len(keys),32):
        vectors.extend(encoder.encode([texts[k] for k in keys[start:start+32]]))
        if start%640==0:print(json.dumps(dict(encoded=min(start+32,len(keys)),total=len(keys))),flush=True)
    by=dict(zip(keys,vectors));files=sorted({k for v in versions for k in selected[v]})
    difference=max(float(np.max(np.abs(np.mean([by['file:'+k] for k in selected[v]],axis=0)-users[j]))) for j,v in enumerate(versions))
    if difference>1e-4:raise ValueError(f'Original user vector reconstruction mismatch: {difference}')
    np.savez_compressed(output/'semantic_vectors.npz',file_keys=np.asarray(files),file_vectors=np.asarray([by['file:'+k] for k in files],dtype=np.float32),
                        issue_keys=np.asarray(issues),title_vectors=np.asarray([by['title:'+k] for k in issues],dtype=np.float32))
    assert all(sha(Path(p))==h for p,h in hashes.items())
    write(output/'complete.json',dict(cache_sha256=sha(output/'semantic_vectors.npz'),original_user_max_abs_reconstruction_error=difference,
        documents=len(files),issues=len(issues),input_hashes_verified=True))
    print(json.dumps(dict(status='complete',max_error=difference)))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path);run(p.parse_args().output)
