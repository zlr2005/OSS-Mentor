import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import shutil
import sqlite3
from contextlib import closing

from oss_mentor.datasets.b_delivery import merge_developer, projection

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('b_verifier',ROOT/'scripts/midterm_a/verify_b_delivery.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class BDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        source=ROOT/'fixtures/midterm/a/snapshot_v0.1'
        self.tables=json.loads((source/'records.json').read_text(encoding='utf-8'))
        self.tables.pop('issues');self.tables.pop('issue_file_links')
        self.tables['weak_interest_behaviors']=[]
        self.tables['project_behaviors']=[]
        for i,row in enumerate(self.tables['contributions']):row['contribution_key']=str(i)
        for code in self.tables['code_documents']:
            if code['availability']=='available':
                path=self.root/code['content_location'];path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes((source/code['content_location']).read_bytes())
        self.write()

    def write(self):
        folder=self.root/'tables';folder.mkdir(exist_ok=True)
        for kind,rows in self.tables.items():
            (folder/(kind+'.jsonl')).write_text(''.join(json.dumps(row)+'\n' for row in rows),encoding='utf-8')
        files=[{'path':p.relative_to(self.root).as_posix(),'size_bytes':len(p.read_bytes()),
                'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(self.root.rglob('*'))
               if p.is_file() and p.name not in {'manifest.json','manifest.sha256'}]
        manifest={'schema_version':'b-profile-evidence-v1','dataset_version':'test-only','fixture_only':False,
                  'entity_counts':{k:len(v) for k,v in self.tables.items()},'quality':{},'files':files}
        data=json.dumps(manifest).encode();(self.root/'manifest.json').write_bytes(data)
        (self.root/'manifest.sha256').write_text(hashlib.sha256(data).hexdigest()+'  manifest.json\n')

    def test_valid_references_and_explicit_missing_code(self):
        self.assertEqual(module.verify(self.root)['status'],'delivery_verified')

    def test_changed_code_bytes_are_rejected(self):
        path=next((self.root/'content').rglob('*.txt'));path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'checksum'):module.verify(self.root)

    def test_rehashed_wrong_commit_is_still_rejected(self):
        self.tables['contributions'][0]['commit_sha']='f'*40;self.write()
        with self.assertRaisesRegex(ValueError,'Contribution/version'):module.verify(self.root)

    def test_rehashed_wrong_event_time_is_rejected(self):
        self.tables['contributions'][0]['event_at']='2026-09-03T12:00:00Z';self.write()
        with self.assertRaisesRegex(ValueError,'time mismatch'):module.verify(self.root)

    def test_missing_code_requires_reason(self):
        missing=next(row for row in self.tables['code_documents'] if row['availability']=='missing')
        missing['exclusion_reason']=None;self.write()
        with self.assertRaisesRegex(ValueError,'missing code'):module.verify(self.root)

    def test_rehashed_wrong_quality_count_is_rejected(self):
        path=self.root/'manifest.json';manifest=json.loads(path.read_text())
        manifest['quality']['developers_with_file_contributions']=999
        data=json.dumps(manifest).encode();path.write_bytes(data)
        (self.root/'manifest.sha256').write_text(hashlib.sha256(data).hexdigest()+'  manifest.json\n')
        with self.assertRaisesRegex(ValueError,'Quality statistic'):module.verify(self.root)

    def test_confirmed_developer_is_merged_only_if_identity_agrees(self):
        row={'developer_key':'a','source':'github','source_developer_id':'12','github_numeric_id':12,
             'identity_status':'verified','evidence_locations':['evidence/a']}
        other={**row,'evidence_locations':['evidence/b']}
        self.assertEqual(merge_developer(row,other)['evidence_locations'],['evidence/a','evidence/b'])
        with self.assertRaisesRegex(ValueError,'Conflicting developer'):
            merge_developer(row,{**other,'github_numeric_id':13})

    def test_projection_does_not_copy_git_author_email_or_message(self):
        envelope={'metadata':{'source_url':'https://api.github.com/repos/o/r/commits/abc','fetched_at':'2026-10-28T00:00:00Z',
                    'status_code':200,'response_sha256':'x'},
            'payload':{'sha':'abc','author':{'id':12,'email':'private@example.invalid'},
                       'commit':{'tree':{'sha':'tree'},'message':'do not export this message',
                                 'author':{'date':'2026-10-27T00:00:00Z','email':'private@example.invalid'},
                                 'committer':{'date':'2026-10-27T00:00:00Z'}}}}
        raw=json.dumps(envelope).encode();result=projection(envelope,raw)
        self.assertNotIn('private@example.invalid',json.dumps(result))
        self.assertNotIn('do not export',json.dumps(result))
        self.assertTrue(result['projection_not_raw'])
        self.assertEqual(result['raw_artifact_sha256'],hashlib.sha256(raw).hexdigest())

    def importer(self):
        for source,target in [('verify_b_delivery.py','verify.py'),('b_delivery_reader.py','reader.py'),
                              ('import_b_delivery_011.py','import_011.py')]:
            shutil.copyfile(ROOT/'scripts/midterm_a'/source,self.root/target)
        shutil.copyfile(ROOT/'db/sqlite/011_midterm_datasets.sql',self.root/'011_midterm_datasets.sql')
        self.write()
        spec=importlib.util.spec_from_file_location('test_b_importer',self.root/'import_011.py')
        importer=importlib.util.module_from_spec(spec);spec.loader.exec_module(importer)
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        return importer,Path(temp.name)/'test.sqlite3'

    def test_011_import_is_idempotent_and_preserves_unrelated_tables(self):
        importer,database=self.importer()
        with closing(sqlite3.connect(database)) as c:c.executescript("CREATE TABLE unrelated(value TEXT); INSERT INTO unrelated VALUES ('keep');")
        first=importer.import_delivery(self.root,database,2)
        second=importer.import_delivery(self.root,database,7)
        self.assertEqual(first,second);self.assertEqual(first['status'],'completed')
        self.assertEqual(first['entity_counts']['contributions'],2)
        with closing(sqlite3.connect(database)) as c:
            self.assertEqual(c.execute('SELECT value FROM unrelated').fetchone()[0],'keep')
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
        from oss_mentor.datasets.normalization import record_key
        reader=importer.sibling('reader')
        for kind in ('contributions','project_behaviors','file_aliases'):
            for row in self.tables[kind]:self.assertEqual(reader.record_key(kind,row),record_key(kind,row))

    def test_011_failure_keeps_checkpoint_and_resumes_after_reopen(self):
        importer,database=self.importer()
        with closing(sqlite3.connect(database)) as c:
            c.executescript((self.root/'011_midterm_datasets.sql').read_text())
            c.executescript("CREATE TRIGGER fail BEFORE INSERT ON midterm_dataset_contribution WHEN NEW.developer_key='dev-unknown' BEGIN SELECT RAISE(ABORT,'injected'); END;")
        with self.assertRaises(sqlite3.IntegrityError):importer.import_delivery(self.root,database,1)
        with closing(sqlite3.connect(database)) as c:
            before=c.execute('SELECT batch_id,status,checkpoint FROM midterm_dataset_import').fetchone()
            self.assertEqual(before[1],'failed');self.assertGreater(before[2],0)
            c.execute('DROP TRIGGER fail')
        result=importer.import_delivery(self.root,database,3)
        self.assertEqual(result['batch_id'],before[0]);self.assertEqual(result['status'],'completed')
        self.assertEqual(result['entity_counts']['contributions'],2)

    def test_011_rejects_changed_input_under_same_version(self):
        importer,database=self.importer();importer.import_delivery(self.root,database,3)
        self.tables['repositories'][0]['primary_language']='Java';self.write()
        with self.assertRaisesRegex(ValueError,'different content'):importer.import_delivery(self.root,database,3)

    def test_011_validates_before_creating_database(self):
        importer,database=self.importer()
        path=next((self.root/'content').rglob('*.txt'));path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'checksum'):importer.import_delivery(self.root,database,3)
        self.assertFalse(database.exists())


if __name__=='__main__':unittest.main()
