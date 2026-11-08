"""处理特征准备请求，实际编码并原子发布只读缓存；支持单次或后台轮询。"""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from oss_mentor.gated_scoring import CachedGatedScorer,NumpyGatedModel,FEATURE_SCHEMA,context_key,enhanced_profile_fingerprint,sha_file
from oss_mentor.services.profile_service import as_developer_profile_v2
from prepare_gated_context import prepare


def atomic_json(path,payload):
    path.parent.mkdir(parents=True,exist_ok=True)
    descriptor,temporary=tempfile.mkstemp(dir=path.parent,suffix='.tmp')
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as stream:json.dump(payload,stream,ensure_ascii=False,indent=2)
        os.replace(temporary,path)
    finally:Path(temporary).unlink(missing_ok=True)


def _latest_requests(directory):
    selected={}
    for path in directory.glob('*.json'):
        try:
            payload=json.loads(path.read_text(encoding='utf-8'))
            context=payload.get('context_key')
            requested_at=datetime.fromisoformat(str(payload['requested_at']).replace('Z','+00:00'))
            if requested_at.tzinfo is None:requested_at=requested_at.replace(tzinfo=timezone.utc)
            timestamp=requested_at.timestamp()
        except (OSError,KeyError,TypeError,ValueError):
            context=None;timestamp=None
        identity=('context',context) if context else ('file',path.name)
        key=(timestamp is not None,timestamp if timestamp is not None else 0.0,path.stat().st_mtime_ns,path.name)
        if identity not in selected or key>selected[identity][0]:selected[identity]=(key,path)
    return [path for _,path in sorted(selected.values(),key=lambda item:item[0],reverse=True)]


def process(request_path,config,encoder=None):
    bundle=Path(config['bundle']);cache=Path(config['cache'])
    status_path=Path(config['requests'])/'status'/(request_path.stem+'.json')
    request=json.loads(request_path.read_text(encoding='utf-8'))
    try:
        model=NumpyGatedModel(bundle);profile=as_developer_profile_v2(request['profile'])
        enhanced=request.get('enhanced_profile')
        if request['context_key']!=context_key(profile,request['developer_key'],enhanced):raise ValueError('Request profile identity mismatch')
        if request['feature_schema']!=FEATURE_SCHEMA or request['backbone_sha256']!=model.manifest['backbone_sha256'] or request['model_version']!=model.manifest['model_version']:
            raise ValueError('Request model mismatch')
        try:
            CachedGatedScorer(bundle,cache).bind_enhanced_profile(enhanced).score(profile=profile,developer_key=request['developer_key'],candidates=request['candidates'])
            result=dict(state='ready',context_key=request['context_key'],checked_at=datetime.now(timezone.utc).isoformat())
            atomic_json(status_path,result);return result
        except (OSError,ValueError,KeyError,RuntimeError):pass
        login=(request['developer_key'] or '').casefold()
        if config.get('auto_collect_public'):
            from gated_account_bootstrap import account_evidence
            atomic_json(status_path,dict(state='collecting_public_evidence',context_key=request['context_key']))
            evidence_path=account_evidence(request,config)
        else:
            evidence_path=config.get('evidence_by_login',{}).get(login)
        if not evidence_path:raise ValueError('Verified evidence not configured for this user')
        atomic_json(status_path,dict(state='encoding',context_key=request['context_key'],started_at=datetime.now(timezone.utc).isoformat()))
        if encoder is None:
            from encode_gated_live import encode
            encoder=encode
        generation=cache/'generations'/uuid.uuid4().hex
        work=generation/'source';work.mkdir(parents=True,exist_ok=False)
        arrays,audit=encoder(request,bundle,Path(evidence_path),Path(config['assets']))
        np.savez_compressed(work/'prepared.npz',**arrays)
        atomic_json(work/'profile.json',request['profile']);atomic_json(work/'candidates.json',request['candidates'])
        provenance=dict(audit,scope='live',context_key=request['context_key'],feature_schema=FEATURE_SCHEMA,
            backbone_sha256=model.manifest['backbone_sha256'],features_sha256=sha_file(work/'prepared.npz'))
        provenance['enhanced_profile_sha256']=enhanced_profile_fingerprint(enhanced)
        provenance['enhanced_profile_version']=enhanced.get('profile_version') if enhanced else None
        atomic_json(work/'provenance.json',provenance)
        # prepare publishes a full pair in a fresh directory; readers cannot see it yet.
        prepare(bundle,work/'profile.json',work/'candidates.json',work/'prepared.npz',work/'provenance.json',generation,request['developer_key'])
        CachedGatedScorer(bundle,generation).bind_enhanced_profile(enhanced).score(profile=profile,developer_key=request['developer_key'],candidates=request['candidates'])
        pointer=cache/(request['context_key']+'.current.json')
        atomic_json(pointer,dict(generation=generation.name,context_key=request['context_key']))
        # Re-read through the same route used by the running platform.
        CachedGatedScorer(bundle,cache).bind_enhanced_profile(enhanced).score(profile=profile,developer_key=request['developer_key'],candidates=request['candidates'])
        result=dict(state='ready',context_key=request['context_key'],generation=generation.name,
                    completed_at=datetime.now(timezone.utc).isoformat(),candidates=len(request['candidates']))
    except Exception as exc:
        result=dict(state='failed',reason=str(exc),context_key=request.get('context_key'),
                    failed_at=datetime.now(timezone.utc).isoformat())
    atomic_json(status_path,result)
    return result


def run(config,once=False):
    directory=Path(config['requests']);directory.mkdir(parents=True,exist_ok=True)
    # One worker owns a queue; an OS lock is released even after process termination.
    lock=(directory/'worker.lock').open('a+b')
    lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
    if os.name=='nt':
        import msvcrt
        msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    else:
        import fcntl
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    last={}
    failed=False
    try:
        while True:
            # Candidate changes create a new request file while keeping the same
            # profile/identity context. Older files must not overwrite a newer
            # generation for that context on the next retry.
            for path in _latest_requests(directory):
                if time.monotonic()-last.get(path.name,-1e9)<60:continue
                try:result=process(path,config)
                except Exception as exc:result=dict(state='failed',reason=str(exc))
                print(json.dumps(dict(request=path.name,**result),ensure_ascii=False),flush=True)
                failed=failed or result['state']=='failed'
                last[path.name]=time.monotonic()
            if once:return 2 if failed else 0
            time.sleep(2)
    finally:lock.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--once',action='store_true');args=parser.parse_args()
    raise SystemExit(run(json.loads(args.config.read_text(encoding='utf-8')),args.once))
