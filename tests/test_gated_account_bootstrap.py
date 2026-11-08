from datetime import datetime,timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from gated_account_bootstrap import account_evidence
from run_gated_feature_worker import atomic_json
from oss_mentor.sqlite_store import SQLiteCandidateStore
from oss_mentor.storage.identity import IdentityStore


class AccountBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        store=SQLiteCandidateStore(self.root/'users.sqlite3',ROOT/'db/sqlite/001_mvp.sql');store.initialize()
        identity=IdentityStore(store)
        identity.create_user(github_user_id=7,github_login='new-user')
        identity.create_user(github_user_id=8,github_login='other-user')
        self.config=dict(database=str(store.database_path),requests=str(self.root/'requests'))

    def collect(self,login,directory,uid):
        atomic_json(directory/'evidence_manifest.json',dict(login=login,github_numeric_id=uid,
            as_of=datetime.now(timezone.utc).isoformat(),responses=[],documents=[],retained_X=[]))

    def test_any_registered_account_collects_once_and_remains_isolated(self):
        a=account_evidence({'developer_key':'new-user'},self.config,self.collect)
        b=account_evidence({'developer_key':'other-user'},self.config,self.collect)
        self.assertNotEqual(a,b)
        def unexpected(*args):self.fail('Fresh evidence should be reused')
        self.assertEqual(account_evidence({'developer_key':'NEW-USER'},self.config,unexpected),a)

    def test_network_failure_produces_marked_cold_evidence_not_another_user_history(self):
        def unavailable(*args):raise TimeoutError('upstream unavailable')
        directory=account_evidence({'developer_key':'new-user'},self.config,unavailable)
        manifest=json.loads((directory/'evidence_manifest.json').read_text())
        self.assertEqual(manifest['github_numeric_id'],7)
        self.assertEqual(manifest['documents'],[])
        self.assertEqual(manifest['collection_status'],'unavailable')

    def test_unregistered_or_mismatched_identity_is_rejected(self):
        with self.assertRaises(ValueError):account_evidence({'developer_key':'outsider'},self.config,self.collect)
        def wrong(login,directory,uid):self.collect(login,directory,999)
        with self.assertRaises(ValueError):account_evidence({'developer_key':'new-user'},self.config,wrong)

    def test_anonymous_profile_never_borrows_account_history_or_calls_github(self):
        def unexpected(*args):self.fail('Anonymous profiles must not query GitHub')
        directory=account_evidence({'developer_key':None,'context_key':'a'*64},self.config,unexpected)
        manifest=json.loads((directory/'evidence_manifest.json').read_text())
        self.assertEqual(manifest['scope'],'anonymous_profile_only')
        self.assertIsNone(manifest['github_numeric_id'])
        self.assertEqual(manifest['documents'],[])
