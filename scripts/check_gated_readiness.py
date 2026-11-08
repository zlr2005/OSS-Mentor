"""在真实库存上只读验证指定画像的门控模型覆盖；不刷新网络、不生成特征。"""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from oss_mentor.contracts import DeveloperProfileV2
from oss_mentor.gated_scoring import CachedGatedScorer,fingerprint
from oss_mentor.services.recommendation_service import RecommendationService,ModelUnavailableError
from oss_mentor.sqlite_store import SQLiteCandidateStore
from oss_mentor.services.profile_service import as_developer_profile_v2


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ['database','bundle','cache','profile']:p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--developer-key');a=p.parse_args()
    if not a.database.is_file():raise SystemExit('Existing initialized database required')
    raw_profile=json.loads(a.profile.read_text(encoding='utf-8'));profile=as_developer_profile_v2(raw_profile.get('profile',raw_profile))
    scorer=CachedGatedScorer(a.bundle,a.cache);store=SQLiteCandidateStore(a.database,ROOT/'db/sqlite/001_mvp.sql')
    service=RecommendationService(store,model_scorer=scorer)
    try:
        batch=service.recommend_batch(profile=profile,developer_key=a.developer_key)
        used=bool(batch.rankings) and all(r.get('model_version')==scorer.model.manifest['model_version'] for r in batch.rankings)
        result=dict(ready=used,model_version=scorer.model.manifest['model_version'],profile_sha256=fingerprint(profile),
            returned_items=len(batch.items),reason=None if used else 'No currently eligible matching task; model coverage cannot be established')
    except ModelUnavailableError as exc:result=dict(ready=False,reason=str(exc))
    print(json.dumps(result,ensure_ascii=False));raise SystemExit(0 if result['ready'] else 2)
