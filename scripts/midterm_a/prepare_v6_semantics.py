"""仅用公开证据通过且存在完整 patch 的共同文件，离线编码三种视图。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tmp/pr39-review/scripts/midterm_a'))
from diagnose_ranking import sha, write


def run(a):
    read = lambda p: json.loads(p.read_text(encoding='utf-8'))
    original = read(a.original / 'documents.json')
    plans = read(a.original / 'profiles.json')
    patches = read(a.patches / 'patches.json')
    checks = read(a.proofs / 'checks.json')
    allowed = {(c['profile_version'], c['document_key']) for c in checks if c['publication_passed']}
    selected, documents = [], {}
    for p in plans:
        if p['split'] not in ('train', 'validation'):
            raise ValueError('Unexpected split')
        keys = [k for k in p['documents'] if (p['profile_version'], k) in allowed and k in patches]
        selected.append(dict(p, documents=keys, available=bool(keys)))
        for k in keys:
            d = original[k]
            if sha(ROOT / d['content_path']) != d['content_sha256']:
                raise ValueError('Snapshot changed')
            if sha(Path(patches[k]['evidence_path'])) != patches[k]['evidence_sha256']:
                raise ValueError('Patch evidence changed')
            documents[k] = d
    if not documents:
        raise ValueError('No qualified documents')
    a.output.mkdir(parents=True, exist_ok=False)
    protocol = dict(views=['path', 'content', 'patch'], max_tokens=256, max_characters=8192,
        selection='intersection of original v5 selection, cutoff publication proof and cached patch; no replacement documents',
        base_training_publication_audit_complete=False, research_only=True,
        encoder='all-MiniLM-L6-v2@c21050a7ef692090620a6d037dd736908f9c7cf6',
        input_hashes={str(p): sha(p) for p in [a.original/'documents.json', a.original/'profiles.json', a.patches/'patches.json', a.proofs/'checks.json']},
        source_sha256=sha(Path(__file__)), documents=len(documents),
        profile_coverage={s: dict(total=sum(p['split']==s for p in selected), available=sum(p['split']==s and p['available'] for p in selected)) for s in ('train','validation')},
        evaluate_test=False, external_evaluation_read=False)
    write(a.output/'protocol.json', protocol)
    write(a.output/'profiles.json', selected)
    write(a.output/'documents.json', documents)
    os.environ['HF_HUB_CACHE'] = str(a.assets/'encoding-assets/hf_hub')
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    weights=a.assets/'encoding-assets/hf_hub/models--sentence-transformers--all-MiniLM-L6-v2/snapshots/c21050a7ef692090620a6d037dd736908f9c7cf6/pytorch_model.bin'
    if sha(weights) != 'c3a85f238711653950f6a79ece63eb0ea93d76f6a6284be04019c53733baf256':
        raise ValueError('Pinned encoder weights changed')
    import torch
    from coder_issue.models.text_encoder import FrozenTransformerEncoder
    torch.set_num_threads(4)
    encoder = FrozenTransformerEncoder('sentence-transformers/all-MiniLM-L6-v2', revision='c21050a7ef692090620a6d037dd736908f9c7cf6', allow_download=False, max_tokens=256)
    keys = sorted(documents)
    views = {}
    for view in protocol['views']:
        vectors = []
        for start in range(0,len(keys),32):
            texts=[]
            for k in keys[start:start+32]:
                d=documents[k]
                text='[FILE] '+d['path']
                if view=='content':
                    text+='\n[CONTENT]\n'+(ROOT/d['content_path']).read_text(encoding='utf-8',errors='replace')[:8192]
                elif view=='patch':
                    text+='\n[PATCH]\n'+patches[k]['patch'][:8192]
                texts.append(text)
            vectors.append(encoder.encode(texts))
        matrix=np.concatenate(vectors)
        assert matrix.shape==(len(keys),384) and np.isfinite(matrix).all()
        by_key=dict(zip(keys,matrix))
        views[view]=np.asarray([np.mean([by_key[k] for k in p['documents']],axis=0) if p['available'] else np.zeros(384,dtype=np.float32) for p in selected], dtype=np.float32)
        print(json.dumps(dict(view=view,documents=len(keys),stage='encoded')),flush=True)
    np.savez_compressed(a.output/'profile_semantics.npz', profile_versions=np.asarray([p['profile_version'] for p in selected]), available=np.asarray([p['available'] for p in selected]), **views)
    write(a.output/'encoding_complete.json',dict(cache_sha256=sha(a.output/'profile_semantics.npz'),profiles=len(selected),documents=len(keys)))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('original','patches','proofs','assets','output'):
        p.add_argument('--'+n,type=Path,required=True)
    run(p.parse_args())
