"""模型包/缓存失效边界及既有推荐服务、API连接测试。"""
from dataclasses import asdict,replace
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import numpy as np

from oss_mentor.api import RecommendationApi
from oss_mentor.contracts import DeveloperProfileV2,REASON_CODE_NEGATIVE_FEEDBACK
from oss_mentor.gated_scoring import CachedGatedScorer,NumpyGatedModel,FEATURE_SCHEMA,context_key,fingerprint,content_hash,sha_file
from oss_mentor.services.recommendation_service import RecommendationService,ModelUnavailableError

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from prepare_gated_context import prepare


def candidate(i,language='Python'):
    return dict(task_candidate_id=i,repository='example/demo',issue_number=i,title=f'Task {i}',body_text='Historical task body',
        html_url=f'https://github.com/example/demo/issues/{i}',newcomer_label_signal=1,estimated_code_difficulty=1,estimated_setup_difficulty=1,
        newcomer_score=80,growth_value_score=80,text_clarity_score=80,primary_language=language,task_types=['testing'],
        task_feature_version='test-features',feature_evidence={'difficulty_assessment':{'information_quality':{'actionability':'actionable'}}},
        requirements=[dict(skill_name=language,minimum_level=1,importance=1.)],candidate_availability='available',
        github_verified_at=datetime.now(timezone.utc).isoformat(),warnings_json='[]')


class Store:
    database_path=Path('memory.sqlite3')
    def __init__(self,profile,tasks):self.profile=profile;self.tasks=tasks;self.feedback={}
    def matchable_candidates(self):return self.tasks
    def feedback_states(self,context,ids):return {i:self.feedback[i] for i in ids if i in self.feedback}
    def list_profiles_public(self):return [asdict(self.profile)]
    def profile_for_matching(self,key):
        if key!=self.profile.profile_key:raise ValueError('Unknown profile')
        return asdict(self.profile)


class GatedScoringTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        supplied=os.environ.get('OSS_MENTOR_TEST_GATED_BUNDLE')
        self.bundle=Path(supplied) if supplied else self.root/'model'
        if not supplied:
            self.bundle.mkdir();rng=np.random.default_rng(7);data={}
            for prefix,dimensions in [('pair.net',(16,32)),('embedding',(128,16)),('gate',(16,8))]:
                n,h=dimensions
                for key,shape in [('0.weight',(h,n)),('0.bias',(h,)),('2.weight',(1,h)),('2.bias',(1,))]:data[prefix+'.'+key]=rng.normal(0,.1,shape).astype(np.float32)
            data.update(pair_mean=np.zeros(16,dtype=np.float32),pair_std=np.ones(16,dtype=np.float32))
            np.savez_compressed(self.bundle/'weights.npz',**data)
            m=dict(format='coder-gated-numpy-v1',feature_schema=FEATURE_SCHEMA,backbone_sha256='a'*64,weights_sha256=sha_file(self.bundle/'weights.npz'),
                model_version='gated-test',dataset_version='test',graph_version='test',encoder_version='test')
            (self.bundle/'manifest.json').write_text(json.dumps(m))
        self.model=NumpyGatedModel(self.bundle)
        self.profile=DeveloperProfileV2(profile_key='prepared-test',display_name='Test',service_track='newcomer',preferred_languages=('Python',),
            operating_systems=('linux',),preferred_task_types=('testing',),max_code_difficulty=1,max_setup_difficulty=1,desired_skill_stretch=0,skills={'python':2})
        self.tasks=[candidate(1),candidate(2)];self.cache=self.root/'cache';self.make_cache()

    def make_cache(self,*,scope='live',as_of=None,expires=None,developer_key=None):
        now=datetime.now(timezone.utc);at=as_of or now-timedelta(minutes=1);end=expires or now+timedelta(hours=1)
        rng=np.random.default_rng(3);u=rng.normal(size=32).astype(np.float32);u/=np.linalg.norm(u);v=np.tile(u,(2,1))
        coder=np.column_stack([np.tile(u,(2,1)),v,np.tile(u,(2,1))*v,np.abs(np.tile(u,(2,1))-v)])
        features=self.root/'prepared.npz';np.savez_compressed(features,issue_keys=np.asarray(['example/demo#1','example/demo#2']),
            content_hashes=np.asarray([content_hash(t) for t in self.tasks]),coder_features=coder,pair_features=np.zeros((2,16),dtype=np.float32),coder_logits=np.asarray([-.1,.1],dtype=np.float32))
        profile_path=self.root/'profile.json';profile_path.write_text(json.dumps(asdict(self.profile)))
        task_path=self.root/'candidates.json';task_path.write_text(json.dumps(self.tasks))
        provenance=dict(context_key=context_key(self.profile,developer_key),feature_schema=FEATURE_SCHEMA,backbone_sha256=self.model.manifest['backbone_sha256'],
            features_sha256=sha_file(features),evidence_manifest_sha256='b'*64,scope=scope,as_of=at.isoformat(),expires_at=end.isoformat())
        proof=self.root/'provenance.json';proof.write_text(json.dumps(provenance))
        prepare(self.bundle,profile_path,task_path,features,proof,self.cache,developer_key)

    def test_complete_score_and_candidate_subset_recomputed(self):
        scorer=CachedGatedScorer(self.bundle,self.cache)
        batch=scorer.score(profile=self.profile,developer_key=None,candidates=self.tasks)
        self.assertEqual([r.task_candidate_id for r in batch.scores],[1,2])
        self.assertTrue(all(0<=r.score<=1 for r in batch.scores))
        self.assertEqual(batch.model_version,self.model.manifest['model_version'])
        self.assertEqual(len(scorer.score(profile=self.profile,developer_key=None,candidates=self.tasks[:1]).scores),1)

    def test_profile_identity_and_candidate_mutation_fail_closed(self):
        scorer=CachedGatedScorer(self.bundle,self.cache)
        for profile,login,tasks in [(replace(self.profile,skills={'python':3}),None,self.tasks),(self.profile,'another-user',self.tasks),
            (self.profile,None,[dict(self.tasks[0],title='changed')]),(self.profile,None,[candidate(99)])]:
            with self.assertRaises(RuntimeError):scorer.score(profile=profile,developer_key=login,candidates=tasks)

    def test_stale_historical_and_foreign_backbone_rejected(self):
        key=context_key(self.profile,None);path=self.cache/(key+'.json');meta=json.loads(path.read_text())
        meta['scope']='historical_replay';path.write_text(json.dumps(meta))
        with self.assertRaises(RuntimeError):CachedGatedScorer(self.bundle,self.cache).score(profile=self.profile,developer_key=None,candidates=self.tasks)
        meta['scope']='live';meta['expires_at']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat();path.write_text(json.dumps(meta))
        with self.assertRaises(RuntimeError):CachedGatedScorer(self.bundle,self.cache).score(profile=self.profile,developer_key=None,candidates=self.tasks)
        meta['backbone_sha256']='c'*64;path.write_text(json.dumps(meta))
        with self.assertRaises(ValueError):CachedGatedScorer(self.bundle,self.cache).score(profile=self.profile,developer_key=None,candidates=self.tasks)

    def test_model_corruption_nonfinite_and_duplicate_inputs_rejected(self):
        cloned=self.root/'broken';shutil.copytree(self.bundle,cloned)
        with (cloned/'weights.npz').open('ab') as f:f.write(b'corrupt')
        with self.assertRaises(ValueError):NumpyGatedModel(cloned)
        with self.assertRaises(ValueError):self.model.predict(np.full((1,128),np.nan),np.zeros((1,16)),np.zeros(1))
        with self.assertRaises(ValueError):CachedGatedScorer(self.bundle,self.cache).score(profile=self.profile,developer_key=None,candidates=[self.tasks[0]]*2)

    def test_service_filters_before_model_and_keeps_feedback(self):
        nonmatching=candidate(3,'Rust');stale=candidate(4);stale['github_verified_at']='2000-01-01T00:00:00Z'
        store=Store(self.profile,self.tasks+[nonmatching,stale]);service=RecommendationService(store,model_scorer=CachedGatedScorer(self.bundle,self.cache))
        batch=service.recommend_batch(profile=self.profile,limit=2,feedback_context='preset:test')
        self.assertEqual({x.task_candidate_id for x in batch.items},{1,2})
        self.assertTrue(all(r['model_version']==self.model.manifest['model_version'] for r in batch.rankings))
        store.feedback[1]='not_suitable';adjusted=service.recommend_batch(profile=self.profile,limit=2,feedback_context='preset:test')
        item=next(x for x in adjusted.items if x.task_candidate_id==1)
        self.assertEqual(item.feedback_state,'not_suitable')
        self.assertTrue(any(r.code==REASON_CODE_NEGATIVE_FEEDBACK for r in item.reasons))

    def test_real_api_route_uses_scorer_and_reports_missing_cache(self):
        store=Store(self.profile,self.tasks);service=RecommendationService(store,model_scorer=CachedGatedScorer(self.bundle,self.cache));api=RecommendationApi(store,recommendation_service=service)
        response=api.handle('GET','/api/v1/recommendations',query={'profile_key':[self.profile.profile_key],'limit':['2']})
        self.assertEqual(response.status,200,response.body)
        self.assertEqual(response.body['count'],2)
        self.assertTrue(all(r['model_version']==self.model.manifest['model_version'] for r in response.body['rankings']))
        store.tasks[0]['body_text']='new body'
        response=api.handle('GET','/api/v1/recommendations',query={'profile_key':[self.profile.profile_key]})
        self.assertEqual(response.status,503)
        self.assertEqual(response.body['error']['code'],'model_features_required')

    def test_authenticated_api_uses_owned_profile_and_bound_login(self):
        from oss_mentor.services.auth_service import AuthService,AuthSettings,SESSION_COOKIE_NAME
        from oss_mentor.services.profile_service import ProfileService
        from oss_mentor.storage.profiles import SQLiteProfileStorage
        from oss_mentor.storage.identity import IdentityStore
        storage=SQLiteProfileStorage(self.root/'auth.sqlite3',ROOT/'db/sqlite/001_mvp.sql');storage.initialize()
        identities=IdentityStore(storage);uid=identities.create_user(github_user_id=7001,github_login='cached-user')
        identities.create_session(user_id=uid,session_id='gated-test-session',expires_at='2099-01-01T00:00:00Z')
        auth=AuthService(identities,AuthSettings(session_secret='isolated-test-secret'))
        profiles=ProfileService(storage);payload=asdict(self.profile);payload.pop('profile_source')
        profiles.save_manual_profile(payload,user_id=uid)
        self.profile=profiles.profile_for_user_contract(uid);self.make_cache(developer_key='cached-user')
        store=Store(self.profile,self.tasks);service=RecommendationService(store,model_scorer=CachedGatedScorer(self.bundle,self.cache),auth_service=auth,profile_service=profiles)
        api=RecommendationApi(store,auth_service=auth,profile_service=profiles,recommendation_service=service)
        response=api.handle('GET','/api/v1/me/recommendations',cookies={SESSION_COOKIE_NAME:'gated-test-session'})
        self.assertEqual(response.status,200,response.body)
        self.assertEqual(response.body['count'],2)
        self.assertTrue(all(r['model_version']==self.model.manifest['model_version'] for r in response.body['rankings']))
        self.assertEqual(api.handle('GET','/api/v1/me/recommendations').status,401)


    def test_detail_uses_same_pool_score_and_feedback_as_list(self):
        store=Store(self.profile,self.tasks)
        service=RecommendationService(store,model_scorer=CachedGatedScorer(self.bundle,self.cache))
        for feedback in [None,'not_suitable']:
            store.feedback[1]=feedback
            batch=service.recommend_batch(profile=self.profile,feedback_context='test',limit=2)
            detail=service.recommendation_detail(profile=self.profile,task_candidate_id=1,feedback_context='test')
            listed=next(item for item in batch.items if item.task_candidate_id==1)
            self.assertEqual(detail.score,listed.score)
            self.assertEqual(batch.rankings[0]['scoring_engine'],'coder_gated')
            self.assertIn('rule_reasons_are_context_only',detail.warnings)
            self.assertTrue(all(r.score_delta==0 for r in detail.reasons if r.code!=REASON_CODE_NEGATIVE_FEEDBACK))

    def test_changed_profile_creates_deduplicated_preparation_request(self):
        requests=self.root/'requests'
        scorer=CachedGatedScorer(self.bundle,self.cache,request_dir=requests)
        store=Store(self.profile,self.tasks)
        service=RecommendationService(store,model_scorer=scorer)
        changed=replace(self.profile,skills={'python':3})
        for _ in range(2):
            with self.assertRaises(ModelUnavailableError) as raised:
                service.recommend_batch(profile=changed)
            self.assertEqual(raised.exception.code,'model_features_required')
        paths=list(requests.glob('*.json'))
        self.assertEqual(len(paths),1)
        request=json.loads(paths[0].read_text(encoding='utf-8'))
        self.assertEqual(request['profile']['skills'],{'python':3})
        self.assertEqual(len(request['candidates']),2)
        self.assertFalse(request['automatic_encoding_available'])
        self.assertEqual(request['state'],'required')
        self.assertFalse(list(requests.glob('*.tmp')))
        # B's saved profile and existing usable context remain intact.
        self.assertEqual(store.profile,self.profile)
        self.assertEqual(len(service.recommend_batch(profile=self.profile).items),2)

    def test_new_account_immediately_uses_explicit_bootstrap_then_switches_to_model(self):
        from shutil import copyfile
        store=Store(self.profile,self.tasks)
        scorer=CachedGatedScorer(self.bundle,self.root/'new-cache',request_dir=self.root/'new-requests')
        service=RecommendationService(store,model_scorer=scorer,allow_preparing_recommendations=True)
        batch=service.recommend_batch(profile=self.profile)
        self.assertEqual(len(batch.items),2)
        self.assertIn('model_features_preparing',batch.warnings)
        self.assertEqual(batch.rankings[0]['scoring_engine'],'profile_bootstrap_rules')
        detail=service.recommendation_detail(profile=self.profile,task_candidate_id=1)
        self.assertEqual(detail.score,next(i.score for i in batch.items if i.task_candidate_id==1))
        scorer.cache.mkdir()
        key=context_key(self.profile,None)
        for suffix in ['.npz','.json']:copyfile(self.cache/(key+suffix),scorer.cache/(key+suffix))
        ready=service.recommend_batch(profile=self.profile)
        self.assertNotIn('model_features_preparing',ready.warnings)
        self.assertEqual(ready.rankings[0]['scoring_engine'],'coder_gated')

    def test_export_pool_equals_inference_pool(self):
        from unittest.mock import patch
        from export_gated_inputs import export_inputs
        tasks = self.tasks + [candidate(3, language='Rust'), dict(candidate(4), github_verified_at='2020-01-01T00:00:00Z')]
        store = Store(self.profile, tasks)
        service = RecommendationService(store, model_scorer=CachedGatedScorer(self.bundle,self.cache))
        self.assertEqual([t['task_candidate_id'] for t in service.eligible_candidates(self.profile)], [1,2])
        database = self.root/'existing.sqlite3';database.touch()
        with patch('export_gated_inputs.ReadOnlyStore', return_value=store):
            report = export_inputs(database,self.bundle,self.cache,self.root/'export',self.root/'profile.json')
        self.assertTrue(report['ready'])
        self.assertEqual(report['contexts'][0]['eligible_candidates'],2)
        self.assertEqual(len(service.recommend_batch(profile=self.profile).rankings),2)

    def test_official_cli_selects_gated_without_original_artifacts(self):
        from contextlib import redirect_stdout
        import io
        from unittest.mock import patch
        from oss_mentor.cli import main
        from oss_mentor.sqlite_store import SQLiteCandidateStore
        database = self.root/'cli.sqlite3'
        SQLiteCandidateStore(database,ROOT/'db/sqlite/001_mvp.sql').initialize()
        output = io.StringIO()
        with patch('oss_mentor.cli.serve') as serve, redirect_stdout(output):
            result = main(['serve-api','--database',str(database),'--gated-bundle',str(self.bundle),
                           '--gated-cache',str(self.cache),'--refresh-interval-seconds','0'])
        self.assertEqual(result,0)
        service = serve.call_args.kwargs['recommendation_service']
        self.assertIsInstance(service.model_scorer,CachedGatedScorer)
        self.assertEqual(json.loads(output.getvalue())['ranking_engine'],'coder_gated')

    def test_cli_owns_background_worker_lifecycle(self):
        from contextlib import redirect_stdout
        import io
        from unittest.mock import patch
        from oss_mentor.cli import main
        from oss_mentor.sqlite_store import SQLiteCandidateStore
        database=self.root/'worker-cli.sqlite3'
        SQLiteCandidateStore(database,ROOT/'db/sqlite/001_mvp.sql').initialize()
        config=self.root/'worker.json'
        config.write_text(json.dumps(dict(bundle=str(self.bundle),cache=str(self.cache),requests=str(self.root/'requests'))))
        with patch('oss_mentor.cli.subprocess.Popen') as popen, patch('oss_mentor.cli.subprocess.run') as stop, patch('oss_mentor.cli.serve') as serve, redirect_stdout(io.StringIO()):
            self.assertEqual(main(['serve-api','--database',str(database),'--gated-bundle',str(self.bundle),
                '--gated-cache',str(self.cache),'--gated-worker-config',str(config),'--refresh-interval-seconds','0']),0)
        self.assertIn('run_gated_feature_worker.py',popen.call_args.args[0][1])
        if os.name=='nt':
            self.assertIn('/T',stop.call_args.args[0])
        else:
            popen.return_value.terminate.assert_called_once()
        popen.return_value.wait.assert_called_once()
        self.assertTrue(serve.call_args.kwargs['recommendation_service'].model_scorer.automatic_encoding)

    def test_cli_rejects_incomplete_or_conflicting_model_selection(self):
        from contextlib import redirect_stderr
        import io
        from oss_mentor.cli import main
        for flags in [[], ['--gated-cache',str(self.cache),'--legacy-ranking']]:
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(['serve-api','--gated-bundle',str(self.bundle)]+flags)

    def test_gated_model_and_advanced_adapter_mode_are_explicitly_exclusive(self):
        from contextlib import redirect_stderr
        import io
        from oss_mentor.cli import main
        from oss_mentor.sqlite_store import SQLiteCandidateStore
        database=self.root/'database.sqlite3'
        SQLiteCandidateStore(database,ROOT/'db/sqlite/001_mvp.sql').initialize()
        error=io.StringIO()
        with redirect_stderr(error),self.assertRaises(SystemExit):
            main(['serve-api','--database',str(database),
                  '--gated-bundle',str(self.bundle),'--gated-cache',str(self.cache),
                  '--advanced-adapter-mode','shadow','--profile-adapter-dir',str(self.root)])
        # Configuration is rejected before a missing bundle can select another engine.
        self.assertIn('Gated ranking cannot run advanced adapter',error.getvalue())


if __name__=='__main__':unittest.main()
