"""只读CODER门控模型评分器；特征在请求外准备，沿用ModelScorer契约。"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime,timedelta,timezone
import hashlib
import json
import os
import tempfile
import re
from pathlib import Path
from typing import Any,Sequence

import numpy as np

from oss_mentor.contracts import DeveloperProfileV2
from oss_mentor.model_scoring import ModelScore,ModelScoreBatch

FEATURE_SCHEMA='coder-gated-128+16-v1'


class ModelFeaturesRequired(RuntimeError):
    error_code = 'model_features_required'


def sha_file(path:Path)->str:return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint(profile:DeveloperProfileV2)->str:
    if not isinstance(profile,DeveloperProfileV2):raise TypeError('DeveloperProfileV2 required')
    payload=json.dumps(asdict(profile),sort_keys=True,separators=(',',':'),ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def context_key(profile:DeveloperProfileV2,developer_key:str|None)->str:
    return hashlib.sha256(((developer_key or '').strip().casefold()+'\0'+fingerprint(profile)).encode()).hexdigest()


def issue_key(candidate:dict[str,Any])->str:
    repo=str(candidate.get('repository') or candidate.get('repository_full_name') or '').strip().casefold()
    number=candidate.get('issue_number')
    if '/' not in repo or isinstance(number,bool) or not isinstance(number,int) or number<=0:raise ValueError('Invalid candidate identity')
    return f'{repo}#{number}'


def content_hash(candidate:dict[str,Any])->str:
    if not isinstance(candidate.get('title'),str) or not isinstance(candidate.get('body_text'),str):
        raise ValueError('Current candidate title/body_text required for feature cache validation')
    payload=json.dumps([candidate['title'],candidate['body_text']],ensure_ascii=False,separators=(',',':'))
    return hashlib.sha256(payload.encode()).hexdigest()


def _moment(value:str)->datetime:
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None:raise ValueError('Timezone required')
    return result.astimezone(timezone.utc)


def _sigmoid(value):
    value=np.clip(value,-40,40)
    return 1/(1+np.exp(-value))


class NumpyGatedModel:
    """仅计算已导出网络；不需要torch、训练源码、图文件或网络连接。"""
    def __init__(self,bundle:Path):
        bundle=Path(bundle);self.manifest=json.loads((bundle/'manifest.json').read_text(encoding='utf-8'))
        m=self.manifest
        if m.get('format')!='coder-gated-numpy-v1' or m.get('feature_schema')!=FEATURE_SCHEMA:raise ValueError('Unsupported gated bundle')
        if sha_file(bundle/'weights.npz')!=m['weights_sha256']:raise ValueError('Model checksum mismatch')
        with np.load(bundle/'weights.npz',allow_pickle=False) as z:self.w={k:z[k].copy() for k in z.files}
        expected={'pair.net.0.weight':(32,16),'pair.net.0.bias':(32,),'pair.net.2.weight':(1,32),'pair.net.2.bias':(1,),
                  'embedding.0.weight':(16,128),'embedding.0.bias':(16,),'embedding.2.weight':(1,16),'embedding.2.bias':(1,),
                  'gate.0.weight':(8,16),'gate.0.bias':(8,),'gate.2.weight':(1,8),'gate.2.bias':(1,),
                  'pair_mean':(16,),'pair_std':(16,)}
        if set(self.w)!=set(expected):raise ValueError('Model parameters do not match schema')
        for key,shape in expected.items():
            if self.w[key].shape!=shape or self.w[key].dtype!=np.float32 or not np.isfinite(self.w[key]).all():raise ValueError(f'Invalid parameter: {key}')
            self.w[key].flags.writeable=False
        if not bool((self.w['pair_std']>0).all()):raise ValueError('Invalid TRAIN normalization')

    def _mlp(self,x,prefix):
        h=x@self.w[prefix+'.0.weight'].T+self.w[prefix+'.0.bias']
        h=h*_sigmoid(h)
        return (h@self.w[prefix+'.2.weight'].T+self.w[prefix+'.2.bias']).reshape(-1)

    def predict(self,coder:np.ndarray,pair:np.ndarray,coder_logits:np.ndarray)->np.ndarray:
        coder=np.asarray(coder,dtype=np.float32);pair=np.asarray(pair,dtype=np.float32);logits=np.asarray(coder_logits,dtype=np.float32)
        n=len(logits)
        if coder.shape!=(n,128) or pair.shape!=(n,16) or logits.shape!=(n,):raise ValueError('Gated feature shape mismatch')
        if not all(np.isfinite(a).all() for a in [coder,pair,logits]):raise ValueError('Nonfinite gated features')
        if not n:return np.empty(0,dtype=np.float32)
        normalized=np.clip((pair-self.w['pair_mean'])/self.w['pair_std'],-5,5)
        base=(logits-logits.mean())/max(float(logits.std()),1e-8)
        primary=2*np.tanh(self._mlp(normalized,'pair.net'))
        gate=_sigmoid(self._mlp(normalized,'gate'))
        score=primary+.25*gate*np.tanh(base+self._mlp(coder,'embedding'))
        if not np.isfinite(score).all():raise ValueError('Nonfinite gated prediction')
        return score.astype(np.float32,copy=False)


class CachedGatedScorer:
    """缓存绑定画像、身份、任务文本和时间；缺失即报错，禁止静默回退。"""
    scoring_engine = 'coder_gated'
    def __init__(self,bundle:Path,cache:Path,*,allow_historical:bool=False,request_dir:Path|None=None,automatic_encoding:bool=False):
        self.model=NumpyGatedModel(bundle);self.cache=Path(cache);self.allow_historical=allow_historical
        self.request_dir=Path(request_dir) if request_dir is not None else None
        self.automatic_encoding=automatic_encoding

    def _request_features(self,profile,developer_key,candidates):
        if self.request_dir is None:
            return
        payload=dict(context_key=context_key(profile,developer_key),profile=asdict(profile),
                     developer_key=developer_key,candidates=list(candidates),
                     feature_schema=FEATURE_SCHEMA,backbone_sha256=self.model.manifest['backbone_sha256'],
                     model_version=self.model.manifest['model_version'],state='required',
                     automatic_encoding_available=self.automatic_encoding)
        request_id=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        self.request_dir.mkdir(parents=True,exist_ok=True)
        target=self.request_dir/(request_id+'.json')
        if target.exists():return
        payload['requested_at']=datetime.now(timezone.utc).isoformat()
        descriptor,path=tempfile.mkstemp(dir=self.request_dir,suffix='.tmp')
        try:
            with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
                json.dump(payload,stream,ensure_ascii=False,indent=2)
            os.replace(path,target)
        finally:
            Path(path).unlink(missing_ok=True)

    def _context(self,key):
        directory=self.cache
        pointer=self.cache/(key+'.current.json')
        if pointer.exists():
            current=json.loads(pointer.read_text(encoding='utf-8'))
            name=current.get('generation','')
            if current.get('context_key')!=key or not re.fullmatch(r'[0-9a-f]{32}',name):raise ValueError('Invalid context generation pointer')
            directory=self.cache/'generations'/name
        meta=json.loads((directory/(key+'.json')).read_text(encoding='utf-8'))
        if meta.get('context_key')!=key or meta.get('feature_schema')!=FEATURE_SCHEMA:raise ValueError('Context identity/schema mismatch')
        if meta.get('backbone_sha256')!=self.model.manifest['backbone_sha256']:raise ValueError('Context belongs to another CODER backbone/seed')
        path=directory/(key+'.npz')
        if sha_file(path)!=meta['features_sha256']:raise ValueError('Context checksum mismatch')
        with np.load(path,allow_pickle=False) as z:
            keys=z['issue_keys'].astype(str).tolist();hashes=z['content_hashes'].astype(str).tolist()
            coder=z['coder_features'].copy();pair=z['pair_features'].copy();logits=z['coder_logits'].copy()
        if len(keys)!=len(set(keys)) or len(hashes)!=len(keys):raise ValueError('Duplicate/missing context candidates')
        if coder.shape!=(len(keys),128) or pair.shape!=(len(keys),16) or logits.shape!=(len(keys),):raise ValueError('Invalid context dimensions')
        if not all(np.isfinite(a).all() for a in [coder,pair,logits]):raise ValueError('Nonfinite cached features')
        for a in [coder,pair,logits]:a.flags.writeable=False
        return meta,dict(zip(keys,range(len(keys)))),hashes,coder,pair,logits

    def score(self,*,profile:DeveloperProfileV2,developer_key:str|None,candidates:Sequence[dict[str,Any]])->ModelScoreBatch:
        try:
            return self._score_cached(profile=profile,developer_key=developer_key,candidates=candidates)
        except ModelFeaturesRequired:
            self._request_features(profile,developer_key,candidates)
            raise

    def _score_cached(self,*,profile,developer_key,candidates):
        key=context_key(profile,developer_key)
        try:meta,index,hashes,coder,pair,logits=self._context(key)
        except FileNotFoundError as exc:raise ModelFeaturesRequired('当前画像的推荐特征尚未准备完成；画像已保留，需要生成特征后再获取推荐。') from exc
        scope=meta.get('scope')
        now=datetime.now(timezone.utc)
        if scope!='live' and not (self.allow_historical and scope=='historical_replay'):raise RuntimeError('Historical features cannot serve live recommendations')
        if _moment(meta['as_of'])>now:raise RuntimeError('Feature snapshot is in the future')
        if scope=='live':
            at=_moment(meta['as_of']);expiry=_moment(meta['expires_at'])
            if not at<expiry<=at+timedelta(hours=24):raise RuntimeError('Invalid gated context validity window')
            if not at<=now<expiry:raise ModelFeaturesRequired('推荐特征需要更新；画像已保留，更新完成后可重新获取推荐。')
        if meta.get('profile_sha256')!=fingerprint(profile):raise ModelFeaturesRequired('画像已修改，需要重新准备推荐特征。')
        ids=[];candidate_ids=[]
        for candidate in candidates:
            task_id=candidate.get('task_candidate_id')
            if isinstance(task_id,bool) or not isinstance(task_id,int) or task_id<=0:raise ValueError('Invalid task candidate id')
            issue=issue_key(candidate)
            if issue not in index:raise ModelFeaturesRequired('候选任务已变化，需要更新推荐特征。')
            i=index[issue]
            if hashes[i]!=content_hash(candidate):raise ModelFeaturesRequired('任务内容已变化，需要更新推荐特征。')
            ids.append(i);candidate_ids.append(task_id)
        if len(candidate_ids)!=len(set(candidate_ids)) or len(ids)!=len(set(ids)):raise ValueError('Duplicate requested candidates')
        scores=self.model.predict(coder[ids],pair[ids],logits[ids]);m=self.model.manifest
        return ModelScoreBatch(scores=tuple(ModelScore(task_id,float(_sigmoid(value)),'gated_prepared_context') for task_id,value in zip(candidate_ids,scores)),
            model_version=m['model_version'],dataset_version=m['dataset_version'],graph_version=m['graph_version'],encoder_version=m['encoder_version'],
            profile_mode='cold_start_population' if meta.get('cold_start') else 'prepared_profile_gated',
            warnings=('gated_scores_are_not_success_probabilities',)+(('historical_replay_only',) if scope!='live' else ())+
                (('coder_no_public_code_history',) if meta.get('cold_start') else ()))
