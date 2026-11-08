"""核验冻结 train/validation 的全部 X 文件，生成公开性审计输入；不读取 Y。"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'tmp/pr39-review/scripts/midterm_a'),str(ROOT/'tmp/pr39-review/src')]
from prepare_contribution_semantics import match_version
from oss_mentor.profiling.temporal_training import _choose_source,verified_login_from_evidence


def run(output):
    dataset=ROOT/'tmp/pr39-review/datasets/temporal_xy_2024_2026_v3'
    rows=lambda p:[json.loads(l) for l in p.read_text(encoding='utf-8').splitlines()]
    from datetime import datetime
    moment=lambda s:datetime.fromisoformat(s.replace('Z','+00:00'))
    roots={k:ROOT/v for k,v in json.loads((dataset/'source_manifest.json').read_text())['source_roots'].items()}
    index={(r['repository'],r['commit_sha'],r['path']):r for r in rows(dataset/'file_contributions.jsonl')}
    groups={(g['github_numeric_id'],moment(g['cutoff_at'])):g for s in ('train','validation') for g in rows(dataset/(s+'.jsonl'))}
    profiles=[p for p in rows(ROOT/'data/midterm/working/rc1_training_inputs_20261106_v1/profiles.jsonl') if p['split'] in ('train','validation')]
    output.mkdir(parents=True,exist_ok=False)
    docs,plans,failures,verified={},[],[],{}
    for p in profiles:
        g=groups[p['github_numeric_id'],moment(p['as_of'])]
        if not match_version(p,g,index):raise ValueError('Frozen profile identity changed')
        keys=[]
        for key in g['x_contribution_keys']:
            r=index[tuple(key)]
            try:
                if r['github_numeric_id']!=p['github_numeric_id'] or moment(r['event_at'])>=moment(p['as_of']):raise ValueError('Identity/time mismatch')
                source,content,evidence=_choose_source(r,source_roots=roots)
                tag=tuple(key)
                if tag not in verified:
                    raw=content.read_bytes()
                    if hashlib.sha256(raw).hexdigest()!=r['content_sha256']:raise ValueError('Content hash mismatch')
                    if hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!=r['blob_sha']:raise ValueError('Git blob mismatch')
                    payload=json.loads(evidence.read_text(encoding='utf-8-sig'))
                    login,_=verified_login_from_evidence(payload,github_numeric_id=p['github_numeric_id'])
                    verified[tag]=login
                if verified[tag] not in [p['developer_key']]+p.get('identity_aliases',[]):raise ValueError('Login mismatch')
                dkey=hashlib.sha256(json.dumps(key).encode()).hexdigest()
                docs[dkey]=dict(path=r['path'],content_path=str(content),content_sha256=r['content_sha256'],event_at=r['event_at'],evidence_path=str(evidence))
                keys.append(dkey)
            except (OSError,ValueError,KeyError) as e:
                failures.append(dict(profile_version=p['profile_version'],key=key,reason=str(e)))
        plans.append(dict(profile_version=p['profile_version'],split=p['split'],documents=keys,available=bool(keys),expected_files=len(g['x_contribution_keys'])))
    for name,value in [('documents.json',docs),('profiles.json',plans),('failures.json',failures)]:
        (output/name).write_text(json.dumps(value,indent=2),encoding='utf-8')
    print(json.dumps(dict(profiles=len(plans),documents=len(docs),references=sum(len(p['documents']) for p in plans),failures=len(failures))))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    run(p.parse_args().output)
