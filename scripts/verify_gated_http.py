"""真实模型权重的本机HTTP集成检查；输入是隔离合成测试数据，不是真实在线效果。"""
import argparse
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
from threading import Thread
import time

ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from oss_mentor.api import RecommendationApi,make_handler
from oss_mentor.gated_scoring import CachedGatedScorer
from oss_mentor.services.recommendation_service import RecommendationService
from oss_mentor.services.auth_service import AuthService,AuthSettings
from oss_mentor.services.profile_service import ProfileService
from oss_mentor.storage.profiles import SQLiteProfileStorage
from oss_mentor.storage.identity import IdentityStore
from tests.test_gated_scoring import GatedScoringTests,Store


def run(bundle,output):
    output.mkdir(parents=True,exist_ok=False);os.environ['OSS_MENTOR_TEST_GATED_BUNDLE']=str(bundle.resolve())
    case=GatedScoringTests();case.setUp()
    try:
        scorer=CachedGatedScorer(bundle,case.cache);store=Store(case.profile,case.tasks)
        storage=SQLiteProfileStorage(case.root/'http-auth.sqlite3',ROOT/'db/sqlite/001_mvp.sql');storage.initialize()
        auth=AuthService(IdentityStore(storage),AuthSettings(session_secret='isolated-http-test-secret'));profiles=ProfileService(storage)
        service=RecommendationService(store,model_scorer=scorer,auth_service=auth,profile_service=profiles)
        api=RecommendationApi(store,auth_service=auth,profile_service=profiles,recommendation_service=service)
        server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(api,static_root=ROOT/'web'));thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        checks=[]
        def call(path,expected):
            conn=HTTPConnection('127.0.0.1',server.server_port,timeout=10);t=time.perf_counter()
            try:
                conn.request('GET',path);response=conn.getresponse();body=response.read();status=response.status
                assert status==expected,(path,status,body[:200]);checks.append(dict(path=path,status=status,milliseconds=1000*(time.perf_counter()-t)))
                return json.loads(body) if path.startswith('/api/') else None
            finally:conn.close()
        try:
            call('/',200)
            q='/api/v1/recommendations?profile_key='+case.profile.profile_key
            body=call(q,200)
            assert body['count']==2 and all(r['model_version']==scorer.model.manifest['model_version'] for r in body['rankings'])
            call('/api/v1/me/recommendations',401)
            call(q+'&limit=0',400)
            store.tasks[0]['title']='mutated current snapshot'
            body=call(q,503);assert body['error']['code']=='model_features_required'
        finally:server.shutdown();server.server_close();thread.join(timeout=5)
        result=dict(passed=True,model_version=scorer.model.manifest['model_version'],checks=checks,weights='real exported research model',
            inputs='synthetic isolated live-shaped test data',real_github_inventory_used=False,real_oauth_exercised=False,source_database_changed=False)
        (output/'verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result,indent=2))
    finally:case.doCleanups()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bundle',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.bundle,a.output)
