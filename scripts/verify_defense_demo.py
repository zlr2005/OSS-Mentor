"""Real loopback HTTP smoke check using an isolated fixture database and explicit legacy ranking."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]


def run(output):
    output=output.resolve(); output.mkdir(parents=True,exist_ok=False)
    fixture=ROOT/'fixtures/oss_mentor_demo.sqlite3'
    digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    before=digest(fixture)
    env=dict(os.environ, PYTHONPATH=str(ROOT/'src'), TEMP=str(output), TMP=str(output))
    for name in ['SSLKEYLOGFILE','GITHUB_TOKEN','GH_TOKEN','GITHUB_OAUTH_CLIENT_ID','GITHUB_OAUTH_CLIENT_SECRET','OSS_MENTOR_SESSION_SECRET']:
        env.pop(name,None)
    database=output/'demo.sqlite3'
    initialized=subprocess.run([sys.executable,'-m','oss_mentor','init-demo','--database',str(database)],
                               cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',check=True)
    (output/'init.json').write_text(initialized.stdout,encoding='utf-8')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    args=[sys.executable,'-m','oss_mentor','serve-api','--database',str(database),
          '--host','127.0.0.1','--port',str(port),'--legacy-ranking','--refresh-interval-seconds','0']
    checks=[]
    with (output/'server.log').open('w',encoding='utf-8') as log:
        process=subprocess.Popen(args,cwd=ROOT,env=env,stdout=log,stderr=log)
        def request(path,expected=200,body=None):
            started=time.perf_counter()
            req=Request(f'http://127.0.0.1:{port}'+path,
                        data=json.dumps(body).encode() if body is not None else None,
                        headers={'Content-Type':'application/json'} if body is not None else {})
            try: response=urlopen(req,timeout=20)
            except HTTPError as error: response=error
            with response:
                status=response.status; payload=response.read()
                content_type=response.headers.get('Content-Type','')
            assert status==expected,(path,status,payload[:300])
            result=json.loads(payload) if 'application/json' in content_type else None
            checks.append(dict(path=path,status=status,milliseconds=1000*(time.perf_counter()-started),bytes=len(payload)))
            return result
        try:
            deadline=time.monotonic()+30
            while True:
                if process.poll() is not None: raise RuntimeError('Server failed; inspect server.log')
                try:
                    with urlopen(f'http://127.0.0.1:{port}/health',timeout=1) as response: response.read()
                    break
                except (URLError,TimeoutError):
                    if time.monotonic()>deadline: raise RuntimeError('Server startup timed out')
                    time.sleep(.2)
            for path in ['/','/profile','/recommendations','/assets/app.js','/assets/profile.js','/assets/recommendations.js','/health']:
                request(path)
            status=request('/api/v1/status')
            assert not status['automatic_refresh']['enabled']
            profiles=request('/api/v1/profiles')['items']
            recommendation_counts={}
            for profile in profiles:
                key=profile['profile_key']
                result=request('/api/v1/recommendations?profile_key='+key+'&limit=10')
                recommendation_counts[key]=len(result['items'])
            request('/api/v1/me/recommendations',401)
            request('/api/v1/recommendations?profile_key=missing',404)
            request('/api/v1/recommendations?profile_key='+profiles[0]['profile_key']+'&limit=0',400)
            request('/api/v1/recommendations/custom',400,body={'profile':{},'limit':10})
        finally:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
    assert digest(fixture)==before,'Source fixture mutated'
    report=dict(passed=True,ranking_engine='explicit legacy rules, not research LambdaMART or CODER',
                source_fixture_sha256=before,source_fixture_unchanged=True,isolated_database=str(database),
                automatic_refresh=False,real_oauth_exercised=False,browser_rendering_verified=False,
                checks=checks,recommendation_counts=recommendation_counts,
                caveat='Stale fixture tasks remain excluded; no timestamps fabricated; HTTP smoke only.')
    (output/'http_smoke.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    run(p.parse_args().output)
