from dataclasses import asdict
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import sys
import unittest
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'tests')]
import test_gated_scoring as fixtures
from run_gated_feature_worker import process,atomic_json
from oss_mentor.gated_scoring import CachedGatedScorer,context_key,content_hash,FEATURE_SCHEMA


class FeatureWorkerTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.GatedScoringTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        f=self.fixture;self.root=f.root
        self.config=dict(bundle=str(f.bundle),cache=str(self.root/'published'),requests=str(self.root/'requests'),
                         assets=str(self.root),evidence_by_login={'sample':str(self.root)})
        self.request=dict(context_key=context_key(f.profile,'sample'),profile=asdict(f.profile),developer_key='sample',
                          candidates=f.tasks,model_version=f.model.manifest['model_version'],
                          backbone_sha256=f.model.manifest['backbone_sha256'],feature_schema=FEATURE_SCHEMA)
        self.path=self.root/'requests'/'request.json';atomic_json(self.path,self.request)

    def encoder(self,request,*args):
        with np.load(self.root/'prepared.npz',allow_pickle=False) as z:arrays={k:z[k].copy() for k in z.files}
        arrays['content_hashes']=np.asarray([content_hash(t) for t in request['candidates']])
        now=datetime.now(timezone.utc)
        return arrays,dict(evidence_manifest_sha256='b'*64,as_of=now.isoformat(),expires_at=(now+timedelta(hours=1)).isoformat())

    def test_atomic_generation_hot_reload_and_idempotency(self):
        scorer=CachedGatedScorer(Path(self.config['bundle']),Path(self.config['cache']))
        self.assertEqual(process(self.path,self.config,self.encoder)['state'],'ready')
        first=scorer.score(profile=self.fixture.profile,developer_key='sample',candidates=self.request['candidates'])
        pointer=Path(self.config['cache'])/(self.request['context_key']+'.current.json')
        original=pointer.read_bytes()
        self.assertEqual(process(self.path,self.config,lambda *a: self.fail('Valid cache must not re-encode'))['state'],'ready')
        self.assertEqual(pointer.read_bytes(),original)
        self.request['candidates'][0]['title']='Updated task'
        atomic_json(self.path,self.request)
        self.assertEqual(process(self.path,self.config,self.encoder)['state'],'ready')
        self.assertNotEqual(pointer.read_bytes(),original)
        second=scorer.score(profile=self.fixture.profile,developer_key='sample',candidates=self.request['candidates'])
        self.assertEqual(first.scores,second.scores)

    def test_encoder_failure_keeps_previous_generation(self):
        self.assertEqual(process(self.path,self.config,self.encoder)['state'],'ready')
        pointer=Path(self.config['cache'])/(self.request['context_key']+'.current.json');original=pointer.read_bytes()
        self.request['candidates'][0]['title']='Changed';atomic_json(self.path,self.request)
        def failed(*args):raise RuntimeError('encoder unavailable')
        self.assertEqual(process(self.path,self.config,failed)['state'],'failed')
        self.assertEqual(pointer.read_bytes(),original)

    def test_wrong_model_and_bad_output_never_publish(self):
        self.request['backbone_sha256']='0'*64;atomic_json(self.path,self.request)
        self.assertEqual(process(self.path,self.config,self.encoder)['state'],'failed')
        self.assertFalse(list(Path(self.config['cache']).glob('*.current.json')))
        self.request['backbone_sha256']=self.fixture.model.manifest['backbone_sha256'];atomic_json(self.path,self.request)
        def invalid(*args):
            arrays,audit=self.encoder(*args);arrays['coder_features']=np.zeros((2,1));return arrays,audit
        self.assertEqual(process(self.path,self.config,invalid)['state'],'failed')
        self.assertFalse(list(Path(self.config['cache']).glob('*.current.json')))
