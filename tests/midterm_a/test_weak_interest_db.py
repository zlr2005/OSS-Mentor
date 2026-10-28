"""Offline database tests; all records here are synthetic and never delivered as real data."""
import hashlib
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('weak_db',ROOT/'scripts/midterm_a/import_b_weak_interest_db.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class WeakInterestDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.db=self.root/'existing.sqlite3';self.cache=self.root/'cache';self.cache.mkdir()
        self.parent={'dataset_version':'parent','schema_version':'b-profile-evidence-v1','fixture_only':False,
                     'time_coverage':{'start':'2026-01-01T00:00:00Z','end':'2026-10-01T00:00:00Z'},
                     'not_delivered':['star','fork','owned_repository_interest','CODER_inference']}
        with closing(sqlite3.connect(self.db)) as c, c:
            c.executescript((ROOT/'db/sqlite/011_midterm_datasets.sql').read_text())
            c.execute('INSERT INTO midterm_dataset_snapshot VALUES (?,?,?,?,?,?)',('parent','test','hash',json.dumps(self.parent),0,'now'))
            c.execute('INSERT INTO midterm_dataset_import VALUES (?,?,?,?,?,?,?,?)',('batch','parent','completed',1,1,'{}',None,'now'))
            c.execute('INSERT INTO midterm_dataset_developer VALUES (?,?,?,?)',('parent','dev',0,json.dumps({'developer_key':'dev','identity_status':'verified','github_numeric_id':42})))
            c.execute('INSERT INTO midterm_dataset_asset VALUES (?,?,?,?)',('parent','old.txt',hashlib.sha256(b'old').hexdigest(),b'old'))
            c.execute('CREATE TABLE sentinel (value TEXT)');c.execute("INSERT INTO sentinel VALUES ('keep')")
        self.row={'developer_key':'dev','repository_key':'repo','behavior_type':'star','event_at':'2025-01-01T00:00:00Z',
                  'event_time_unknown':False,'observed_at':'2026-10-28T00:00:00Z','source':'github_rest',
                  'source_location':'evidence/page.json','identity_source_location':'evidence/user.json'}
        self.make_cache([self.row])

    def make_cache(self, rows, numeric_id=42):
        data={'repositories':[{'repository_key':'repo','source_repository_id':'123','primary_language':'Python'}],
              'weak_interest_behaviors':rows,'identity_checks':[{'developer_key':'dev','github_numeric_id':numeric_id,'identity_status':'verified'}],
              'coverage':[{'developer_key':'dev','endpoint':'starred','status':'completed','truncated':True}]}
        assets={f'tables/{kind}.jsonl':''.join(json.dumps(row)+'\n' for row in rs).encode() for kind,rs in data.items()}
        assets.update({'evidence/page.json':b'{}','evidence/user.json':b'{}'})
        manifest={'fixture_only':False,'parent_dataset_version':'parent','parent_manifest_sha256':'input-hash',
                  'behavior_counts':{'star':len(rows)},'limitations':['bounded sample'],'files':[]}
        for name,content in assets.items():
            path=self.cache/name;path.parent.mkdir(exist_ok=True);path.write_bytes(content)
            manifest['files'].append({'path':name,'sha256':hashlib.sha256(content).hexdigest(),'size_bytes':len(content)})
        raw=json.dumps(manifest).encode();(self.cache/'manifest.json').write_bytes(raw)
        (self.cache/'manifest.sha256').write_text(hashlib.sha256(raw).hexdigest())

    def test_new_snapshot_preserves_parent_and_is_idempotent(self):
        result=module.import_cache(self.db,self.cache)
        self.assertEqual(result['status'],'completed');self.assertTrue(Path(result['backup']).exists())
        self.assertEqual(module.import_cache(self.db,self.cache)['status'],'already_imported')
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT value FROM sentinel').fetchone(),('keep',))
            self.assertEqual(c.execute('SELECT COUNT(*) FROM midterm_dataset_behavior').fetchone(),(1,))
            self.assertEqual(json.loads(c.execute('SELECT manifest_json FROM midterm_dataset_snapshot WHERE dataset_version=?',('parent',)).fetchone()[0]),self.parent)
            manifest=json.loads(c.execute('SELECT manifest_json FROM midterm_dataset_snapshot WHERE dataset_version=?',(result['dataset_version'],)).fetchone()[0])
            self.assertEqual(manifest['time_coverage']['start'],'2025-01-01T00:00:00Z')
            self.assertIn('fork',manifest['not_delivered']);self.assertNotIn('star',manifest['not_delivered'])
            payload=json.loads(c.execute('SELECT payload_json FROM midterm_dataset_behavior').fetchone()[0])
            self.assertEqual(payload['source_location'],'weak_interest/evidence/page.json')

    def test_duplicate_behavior_rolls_back_entire_snapshot(self):
        self.make_cache([self.row,self.row])
        with self.assertRaises(sqlite3.IntegrityError): module.import_cache(self.db,self.cache)
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM midterm_dataset_snapshot').fetchone(),(1,))
            self.assertEqual(c.execute('SELECT COUNT(*) FROM midterm_dataset_behavior').fetchone(),(0,))

    def test_identity_mismatch_is_rejected(self):
        self.make_cache([self.row],numeric_id=999)
        with self.assertRaisesRegex(ValueError,'identity mismatch'): module.import_cache(self.db,self.cache)

    def test_tampered_evidence_is_rejected(self):
        (self.cache/'evidence/page.json').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'asset mismatch'): module.import_cache(self.db,self.cache)

    def test_unknown_time_cannot_invent_event(self):
        self.make_cache([{**self.row,'event_time_unknown':True}])
        with self.assertRaisesRegex(ValueError,'Unknown time'): module.import_cache(self.db,self.cache)

    def test_acquisition_parent_must_match_database(self):
        path=self.root/'different_manifest.json';path.write_text('{}')
        with self.assertRaisesRegex(ValueError,'Parent manifest differs'):
            module.import_cache(self.db,self.cache,path)

if __name__=='__main__': unittest.main()
