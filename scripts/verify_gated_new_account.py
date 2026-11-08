"""隔离验证任意公开账号的自动采集→编码→发布；任务和登录会话不冒充真实生产流量。"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from oss_mentor.services.github_profile_source import GitHubProfileSource
from oss_mentor.sqlite_store import SQLiteCandidateStore
from oss_mentor.storage.identity import IdentityStore
from oss_mentor.services.recommendation_service import RecommendationService
from oss_mentor.gated_scoring import CachedGatedScorer
from tests.test_gated_scoring import GatedScoringTests,Store
from run_gated_feature_worker import process,atomic_json


def run(login,output):
    user=GitHubProfileSource()._get('/users/'+login)
    output.mkdir(parents=True,exist_ok=False)
    config=dict(bundle=str(ROOT/'data/gated-v1/bundle'),
        cache=str(output/'cache'),requests=str(output/'requests'),database=str(output/'users.sqlite3'),
        assets=str(ROOT/'data/gated-v1/assets'),auto_collect_public=True,evidence_by_login={})
    store=SQLiteCandidateStore(Path(config['database']),ROOT/'db/sqlite/001_mvp.sql');store.initialize()
    IdentityStore(store).create_user(github_user_id=user['id'],github_login=user['login'])
    case=GatedScoringTests();case.setUp()
    try:
        scorer=CachedGatedScorer(Path(config['bundle']),Path(config['cache']),request_dir=Path(config['requests']),automatic_encoding=True)
        service=RecommendationService(Store(case.profile,case.tasks),model_scorer=scorer,allow_preparing_recommendations=True)
        initial=service.recommend_batch(profile=case.profile,developer_key=user['login'])
        assert initial.rankings[0]['scoring_engine']=='profile_bootstrap_rules'
        requests=list(Path(config['requests']).glob('*.json'));assert len(requests)==1
        status=process(requests[0],config);assert status['state']=='ready',status
        ready=service.recommend_batch(profile=case.profile,developer_key=user['login'])
        assert ready.rankings[0]['scoring_engine']=='coder_gated'
        result=dict(passed=True,public_account=user['login'],numeric_identity=user['id'],
            initial_items=len(initial.items),model_items=len(ready.items),profile_mode=ready.rankings[0]['profile_mode'],
            model_version=ready.rankings[0]['model_version'],automatic_evidence_collection=True,
            real_exported_model=True,synthetic_tasks=True,real_oauth_exercised=False,production_database_changed=False)
        atomic_json(output/'verification.json',result);print(json.dumps(result))
    finally:case.doCleanups()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--login',required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run(args.login,args.output.resolve())
