"""冻结模型的实际离线编码：真实代码证据→23维画像/文本→128+16排序输入。"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tmp/pr39-review/CODER/src')]
from oss_mentor.gated_scoring import content_hash,issue_key,sha_file
from oss_mentor.profiling.evidence import interpret_github_contributions
from oss_mentor.profiling.assessment import _normalize_external_evidence,_fuse_feature


def encode(request, bundle, evidence_dir, assets):
    import torch
    from torch import nn
    from coder_issue.models.profile import ProfileResidualAdapter,ProfileAdapterConfig,PROFILE_FEATURE_NAMES
    from coder_issue.models.text_encoder import FrozenTransformerEncoder
    from coder_issue.training.advanced_issue_inputs import encode_candidates
    torch.set_num_threads(2)
    manifest=json.loads((bundle/'manifest.json').read_text(encoding='utf-8'))
    for path,digest in manifest['backbone_dependencies'].items():
        if sha_file(Path(path))!=digest:raise ValueError('Frozen backbone dependency changed')
    seed=manifest['seed'];base=assets/'encoding-assets/coder_bundle/data/models/gfibot-m0-v1.1/coder/coder-full-search8-v1'
    if sha_file(base/'weights.pt')!='38d279daa0c2fb1b632bcfbfce73abc524a27a535e24429dc9d8615cd59302ae':raise ValueError('CODER encoder weights changed')
    evidence=json.loads((evidence_dir/'evidence_manifest.json').read_text(encoding='utf-8'))
    anonymous=(not request['developer_key'] and evidence.get('scope')=='anonymous_profile_only' and
               not evidence['documents'] and not evidence['retained_X'] and not evidence['responses'])
    if not anonymous and (not request['developer_key'] or evidence['login'].casefold()!=request['developer_key'].casefold()):
        raise ValueError('Verified user evidence required; anonymous profiles are not assigned another user history')
    from datetime import datetime,timezone,timedelta
    now=datetime.now(timezone.utc)
    observed=datetime.fromisoformat(evidence['as_of'])
    if not observed<=now<observed+timedelta(hours=24):raise ValueError('User evidence needs collection refresh')
    for row in evidence['responses']:
        if sha_file(evidence_dir/row['path'])!=row['sha256']:raise ValueError('Evidence response changed')
    records=[];documents=[];texts=[]
    for i,d in enumerate(evidence['documents']):
        if d['author_id']!=evidence['github_numeric_id']:raise ValueError('Code author mismatch')
        content=(evidence_dir/d['content_path']).read_bytes()
        if hashlib.sha256(content).hexdigest()!=d['content_sha256']:raise ValueError('Code checksum mismatch')
        if hashlib.sha1(f'blob {len(content)}\0'.encode()+content).hexdigest()!=d['blob_sha']:raise ValueError('Code blob mismatch')
        if datetime.fromisoformat(d['committer_date'].replace('Z','+00:00'))>=now:raise ValueError('Future code')
        key=f'live:{i}'
        documents.append(dict(file_version_key=key,repository_key=d['repository'],path=d['filename'],available=True))
        records.append(dict(developer_key=evidence['login'],repository_key=d['repository'],
            file_key=d['repository']+':'+d['filename'],file_version_key=key,commit_sha=d['commit_sha'],
            event_at=d['committer_date'],observed_at=evidence['as_of'],behavior_type='commit',
            author_link_status='verified',evidence_location=str(evidence_dir/'evidence_manifest.json')))
        texts.append('[FILE] '+d['filename']+'\n[CONTENT]\n'+content.decode('utf-8',errors='replace')[:8192])
    audit=interpret_github_contributions(records,documents,developer_key=evidence['login'],
        observed_at=evidence['as_of'],as_of=now.isoformat(),collection_status='partial')
    raw=list(audit['evidence'])
    languages={'.py':'python','.js':'javascript','.ts':'typescript','.tsx':'typescript','.go':'go','.java':'java','.rs':'rust'}
    for d in evidence['documents']:
        language=languages.get(Path(d['filename']).suffix.lower())
        if language:
            raw.append(dict(feature_name='preference.language.'+language,value=1.,confidence=.65,
                source_kind='github_direct',evidence_ref='github-language:'+d['content_sha256'],
                observed_at=evidence['as_of'],source_ref=str(evidence_dir),content_sha256=d['content_sha256']))
    normalized=_normalize_external_evidence(raw,allowed_features=set(PROFILE_FEATURE_NAMES))
    features=[_fuse_feature(name,[r for r in normalized if r['feature_name']==name]) for name in PROFILE_FEATURE_NAMES]
    values=torch.tensor([[f['proposed_value'] for f in features]],dtype=torch.float32)
    mask=torch.tensor([[f['observed'] for f in features]],dtype=torch.bool)
    adapter=ProfileResidualAdapter(ProfileAdapterConfig(embedding_dim=32,hidden_dim=32,residual_scale=.75,gate_mode='none'))
    adapter.load_state_dict(torch.load(assets/f'runs/bottleneck-diagnostics-20261106-v1/original-{seed}/weights.pt',weights_only=True));adapter.eval()
    with np.load(base/'embeddings.npz',allow_pickle=False) as z:
        population=torch.tensor(z['user_embeddings'].mean(0)[None],dtype=torch.float32)
    # No verified resolved-history supplied: HistoryFusion's existing unavailable path returns original.
    with torch.inference_mode():user=adapter.fuse(population,values,mask)
    os.environ['HF_HUB_CACHE']=str(assets/'encoding-assets/hf_hub')
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    revision='c21050a7ef692090620a6d037dd736908f9c7cf6'
    encoder_path=assets/f'encoding-assets/hf_hub/models--sentence-transformers--all-MiniLM-L6-v2/snapshots/{revision}'
    if sha_file(encoder_path/'pytorch_model.bin')!='c3a85f238711653950f6a79ece63eb0ea93d76f6a6284be04019c53733baf256':raise ValueError('Text encoder changed')
    encoder=FrozenTransformerEncoder(str(encoder_path),revision=revision,allow_download=False,max_tokens=256)
    tasks=request['candidates'];keys=[issue_key(t) for t in tasks]
    if not keys:raise ValueError('No candidates')
    issue_texts=['[ISSUE] '+t['title']+'\n'+t['body_text'][:8192] for t in tasks]
    semantic=np.concatenate([encoder.encode(issue_texts[start:start+32]) for start in range(0,len(tasks),32)])
    user_text=encoder.encode(texts).mean(0) if texts else np.zeros(384,dtype=np.float32)
    rows=[dict(t,issue_key=k) for t,k in zip(tasks,keys)]
    items,item_audit=encode_candidates(rows,model_dir=base,
        mapping_dir=assets/'runs/public-supplement-20261106-v2/mappings',device='cpu',max_segments=16,segment_batch_size=32)
    item=torch.tensor(items);u=nn.functional.normalize(user.expand(len(tasks),-1),dim=-1);v=nn.functional.normalize(item,dim=-1)
    coder=torch.cat([u,v,u*v,(u-v).abs()],dim=-1).numpy()
    directory=assets/f'runs/transfer-cold-v3-20261107-v1/cosine-{seed}'
    transfer=torch.load(directory/'weights.pt',weights_only=True)
    scale=json.loads((directory/'result.json').read_text())['score_scale']
    with torch.inference_mode():
        projection=transfer['projection.weight'];uu=user.expand(len(tasks),-1)
        logits=(uu*item).sum(-1)+mask.any().float()*.25*torch.tanh(transfer['strength'])*scale*nn.functional.cosine_similarity(uu@projection.T,item@projection.T,dim=-1)
    hist=evidence['retained_X'];commits={(r,c) for r,c,p in hist};paths={p for r,c,p in hist}
    names={Path(p).name.casefold() for p in paths if Path(p).suffix and Path(p).name.casefold() not in {'readme.md','license','package.json'}}
    pair=[]
    for t,k,vector in zip(tasks,keys,semantic):
        title=t['title'];body=t['body_text'];text=title+'\n'+body;terms=text.casefold()
        same={c for r,c,p in hist if r==k.split('#')[0]}
        cosine=float(user_text@vector/max(float(np.linalg.norm(user_text)*np.linalg.norm(vector)),1e-12))
        pair.append([cosine,float(bool(same)),len(same)/max(len(commits),1),math.log1p(len(same)),math.log1p(len(commits)),math.log1p(len(paths)),math.log1p(sum(n in terms for n in names)),math.log1p(len(title)),math.log1p(len(body)),math.log1p(text.count('\n')),math.log1p(text.count('```')),math.log1p(len(re.findall(r'https?://',text)))]+[float(bool(re.search(pattern,terms))) for pattern in [r'\b(bug|error|crash|fail|regression)\b',r'\b(doc|docs|documentation|readme|typo)\b',r'\b(test|tests|coverage)\b',r'\b(feature|implement|support|enhancement)\b']])
    return dict(issue_keys=np.asarray(keys),content_hashes=np.asarray([content_hash(t) for t in tasks]),
        coder_features=coder,pair_features=np.asarray(pair,dtype=np.float32),coder_logits=logits.numpy()),dict(
        evidence_manifest_sha256=sha_file(evidence_dir/'evidence_manifest.json'),as_of=now.isoformat(),
        expires_at=min(now+timedelta(hours=24),observed+(timedelta(minutes=5) if evidence.get('collection_status')=='unavailable' else timedelta(hours=24))).isoformat(),
        profile_features=features,item_encoding=item_audit,history_mode='no_verified_resolved_history',
        history_complete=False,source_evidence_as_of=evidence['as_of'],
        cold_start=not bool(texts),collection_status=evidence.get('collection_status','bounded_public_sample'),
        extra_dependencies={str(base/'weights.pt'):sha_file(base/'weights.pt'),str(assets/'runs/public-supplement-20261106-v2/mappings/repository_mapping.parquet'):sha_file(assets/'runs/public-supplement-20261106-v2/mappings/repository_mapping.parquet')})
