"""将离线已准备的CODER特征绑定到实际平台画像/任务快照；本工具不伪造缺失特征。"""
import argparse
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from oss_mentor.contracts import DeveloperProfileV2
from oss_mentor.services.profile_service import as_developer_profile_v2
from oss_mentor.gated_scoring import FEATURE_SCHEMA,NumpyGatedModel,context_key,fingerprint,content_hash,issue_key,sha_file,_moment


def prepare(bundle,profile_path,candidates_path,prepared,provenance_path,cache,developer_key):
    model=NumpyGatedModel(bundle);raw_profile=json.loads(profile_path.read_text(encoding='utf-8'))
    profile=as_developer_profile_v2(raw_profile.get('profile',raw_profile))
    candidates=json.loads(candidates_path.read_text(encoding='utf-8'));provenance=json.loads(provenance_path.read_text(encoding='utf-8'))
    key=context_key(profile,developer_key)
    if provenance.get('feature_schema')!=FEATURE_SCHEMA or provenance.get('backbone_sha256')!=model.manifest['backbone_sha256']:raise ValueError('Offline feature provenance/model mismatch')
    if provenance.get('context_key')!=key:raise ValueError('Offline features belong to another profile or user')
    if provenance.get('features_sha256')!=sha_file(prepared):raise ValueError('Offline feature checksum mismatch')
    if provenance.get('scope') not in ['live','historical_replay']:raise ValueError('Explicit feature scope required')
    evidence_hash=provenance.get('evidence_manifest_sha256','')
    if len(evidence_hash)!=64 or any(c not in '0123456789abcdef' for c in evidence_hash):raise ValueError('Offline evidence manifest hash required')
    at=_moment(provenance['as_of']);expiry=_moment(provenance['expires_at'])
    if not at<expiry<=at+timedelta(hours=24):raise ValueError('Feature expiry must be within24hours of snapshot')
    if provenance['scope']=='live' and not at<=datetime.now(timezone.utc)<expiry:raise ValueError('Live feature snapshot not current')
    keys=[issue_key(c) for c in candidates]
    if len(keys)!=len(set(keys)):raise ValueError('Duplicate candidates')
    with np.load(prepared,allow_pickle=False) as z:
        if z['issue_keys'].astype(str).tolist()!=keys:raise ValueError('Feature/candidate ordering differs')
        if z['content_hashes'].astype(str).tolist()!=[content_hash(c) for c in candidates]:raise ValueError('Offline features were built for different task content')
        coder=np.asarray(z['coder_features'],dtype=np.float32);pair=np.asarray(z['pair_features'],dtype=np.float32);logits=np.asarray(z['coder_logits'],dtype=np.float32)
    model.predict(coder,pair,logits)
    cache.mkdir(parents=True,exist_ok=True);target=cache/(key+'.npz');meta=cache/(key+'.json')
    if target.exists() or meta.exists():raise FileExistsError('Use a fresh cache directory; published contexts are immutable')
    np.savez_compressed(target,issue_keys=np.asarray(keys),content_hashes=np.asarray([content_hash(c) for c in candidates]),coder_features=coder,pair_features=pair,coder_logits=logits)
    metadata=dict(context_key=key,profile_sha256=fingerprint(profile),feature_schema=FEATURE_SCHEMA,backbone_sha256=model.manifest['backbone_sha256'],
        scope=provenance['scope'],as_of=provenance['as_of'],expires_at=provenance['expires_at'],features_sha256=sha_file(target),
        evidence_manifest_sha256=provenance['evidence_manifest_sha256'],source_features_sha256=sha_file(prepared))
    metadata.update(cold_start=bool(provenance.get('cold_start',False)),collection_status=provenance.get('collection_status'))
    meta.write_text(json.dumps(metadata,indent=2),encoding='utf-8');print(json.dumps(dict(context_key=key,candidates=len(keys),cache=str(cache))))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ['bundle','profile','candidates','prepared','provenance','cache']:p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--developer-key');a=p.parse_args();prepare(a.bundle,a.profile,a.candidates,a.prepared,a.provenance,a.cache,a.developer_key)
