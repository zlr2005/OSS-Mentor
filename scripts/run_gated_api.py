"""通过既有评分端口启动CODER门控平台；不替换默认CLI或下载/训练模型。"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from oss_mentor.api import serve
from oss_mentor.gated_scoring import CachedGatedScorer
from oss_mentor.sqlite_store import SQLiteCandidateStore
from oss_mentor.services.recommendation_service import RecommendationService
from oss_mentor.services.auth_service import AuthService,AuthSettings
from oss_mentor.storage.identity import IdentityStore


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',type=Path,required=True);p.add_argument('--bundle',type=Path,required=True);p.add_argument('--cache',type=Path,required=True)
    p.add_argument('--port',type=int,default=8767,metavar='PORT');a=p.parse_args()
    if not 1<=a.port<=65535:p.error('port must be between1 and65535')
    if not a.database.is_file() or not a.cache.is_dir():raise SystemExit('Initialized database and offline feature cache required')
    scorer=CachedGatedScorer(a.bundle,a.cache,request_dir=a.cache.parent/'gated_feature_requests')
    store=SQLiteCandidateStore(a.database,ROOT/'db/sqlite/001_mvp.sql');store.initialize()
    auth=AuthService(IdentityStore(store),AuthSettings(client_id=os.environ.get('GITHUB_OAUTH_CLIENT_ID'),client_secret=os.environ.get('GITHUB_OAUTH_CLIENT_SECRET'),
        session_secret=os.environ.get('OSS_MENTOR_SESSION_SECRET'),base_url=f'http://127.0.0.1:{a.port}'))
    service=RecommendationService(store,model_scorer=scorer,auth_service=auth)
    print(json.dumps(dict(event='gated_api_starting',host='127.0.0.1',port=a.port,model_version=scorer.model.manifest['model_version'],
        feature_cache_required=True,automatic_refresh=False,training_or_downloading=False)))
    serve(store,host='127.0.0.1',port=a.port,static_root=ROOT/'web',auth_service=auth,recommendation_service=service)
