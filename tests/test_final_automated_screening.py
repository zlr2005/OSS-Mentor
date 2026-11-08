from contextlib import closing
from datetime import datetime, timezone, timedelta
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('final_screen', Path(__file__).resolve().parents[1]/'scripts/finalize_automated_screening.py')
screen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screen)


class FinalScreeningTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026,10,26,tzinfo=timezone.utc)

    def row(self, quality='unclear', types=None, ready=False):
        return {'repository':'o/r','github_issue_id':1001,'issue_number':1,'title':'Parser case',
            'body_text':'Frozen source body','labels_json':'[]','task_types_json':json.dumps(types or ['other']),
            'task_feature_version':'task-features-v0.5','estimated_code_difficulty':1,'estimated_setup_difficulty':1,
            'feature_evidence_json':json.dumps({'difficulty_assessment':{'information_quality':{'actionability':quality}}}),
            'requirements':[{'skill_name':'Python'}], 'candidate_eligibility':'eligible' if ready else 'temporarily_ineligible',
            'candidate_availability':'available' if ready else 'temporarily_unverified','state':'open',
            'assignment_state':'unassigned','is_locked':0,'has_linked_open_pr':0,'is_archived':0,'is_disabled':0,
            'maintenance_status':'active','github_verified_at':self.now.isoformat(),
            'verification_expires_at':(self.now+timedelta(hours=24)).isoformat()}

    def reference(self,row):
        return {'identity':screen.identity(row),'github_issue_id':str(row['github_issue_id']),
                'content_sha256':screen.content_hash(row),'decision':'scope_needs_evidence'}

    def test_uncertainty_is_quarantined_instead_of_becoming_a_recommendation(self):
        row=self.row()
        decision,reasons=screen.decide(row,self.reference(row),self.now)
        self.assertEqual(decision,'quarantine')
        self.assertIn('task_scope_requires_review',reasons)

    def test_tracker_is_excluded_as_an_independent_task(self):
        row=self.row(quality='non_actionable')
        self.assertEqual(screen.decide(row,self.reference(row),self.now)[0],'exclude')

    def test_current_confirmed_ready_item_is_preserved(self):
        row=self.row(quality='actionable',types=['bug_fix'],ready=True)
        self.assertEqual(screen.decide(row,self.reference(row),self.now)[0],'keep')

    def test_expired_evidence_never_gets_renewed_or_kept(self):
        row=self.row(quality='actionable',types=['bug_fix'],ready=True)
        row['verification_expires_at']=self.now.isoformat()
        self.assertEqual(screen.decide(row,self.reference(row),self.now)[0],'quarantine')
        self.assertEqual(row['verification_expires_at'],self.now.isoformat())

    def test_changed_source_cannot_be_excluded_using_old_tracker_judgement(self):
        row=self.row(quality='non_actionable');reference=self.reference(row)
        row['body_text']='New description requiring reassessment'
        self.assertEqual(screen.decide(row,reference,self.now),('quarantine',['source_changed_since_content_review']))


class FinalScreeningDatabaseTests(unittest.TestCase):
    row=FinalScreeningTests.row
    reference=FinalScreeningTests.reference
    def setUp(self):
        self.now=datetime(2026,10,26,tzinfo=timezone.utc)
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.path=Path(self.temporary.name)/'screen.sqlite3'
        row=self.row();self.ref=self.reference(row)
        with closing(sqlite3.connect(self.path)) as conn:
            conn.executescript('''
                CREATE TABLE repository(repository_id INTEGER PRIMARY KEY,full_name TEXT,is_archived INTEGER,is_disabled INTEGER,maintenance_status TEXT);
                INSERT INTO repository VALUES(1,'o/r',0,0,'active');
                CREATE TABLE task_candidate(task_candidate_id INTEGER PRIMARY KEY,repository_id INTEGER REFERENCES repository(repository_id),
                    github_issue_id INTEGER,issue_number INTEGER,title TEXT,body_text TEXT,labels_json TEXT,task_types_json TEXT,
                    task_feature_version TEXT,estimated_code_difficulty INTEGER,estimated_setup_difficulty INTEGER,
                    feature_evidence_json TEXT,candidate_eligibility TEXT,candidate_availability TEXT,state TEXT,assignment_state TEXT,
                    is_locked INTEGER,has_linked_open_pr INTEGER,github_verified_at TEXT,verification_expires_at TEXT,ineligibility_reasons_json TEXT);
                CREATE TABLE task_skill_requirement(task_candidate_id INTEGER REFERENCES task_candidate(task_candidate_id),skill_name TEXT);
                CREATE TABLE feedback(task_candidate_id INTEGER REFERENCES task_candidate(task_candidate_id),value TEXT);
                CREATE TABLE source_assets(name TEXT,content BLOB);
                INSERT INTO source_assets VALUES('binary-source',X'000102FF');
            ''')
            for number in [1,2]:
                current=self.row() if number==1 else self.row(quality='actionable',types=['bug_fix'],ready=True)
                current.update(task_candidate_id=number,repository_id=1,issue_number=number,github_issue_id=1000+number,ineligibility_reasons_json='[]')
                fields=[r[1] for r in conn.execute('PRAGMA table_info(task_candidate)')]
                conn.execute('INSERT INTO task_candidate VALUES ('+','.join('?' for _ in fields)+')',[current[f] for f in fields])
                conn.execute('INSERT INTO task_skill_requirement VALUES (?,?)',(number,'Python'))
            conn.execute("INSERT INTO feedback VALUES (2,'interested')");conn.commit()

    def test_apply_preserves_ready_pool_ids_feedback_and_source_deadlines(self):
        first=screen.apply_database(self.path,{'o/r#1':self.ref},self.now,'test-batch')
        second=screen.apply_database(self.path,{'o/r#1':self.ref},self.now,'test-batch')
        self.assertEqual(first,second)
        self.assertEqual(first['ready_identities'],['o/r#2'])
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute('SELECT value FROM feedback').fetchone()[0],'interested')
            self.assertEqual(conn.execute('SELECT content FROM source_assets').fetchone()[0],b'\x00\x01\x02\xff')
            row=conn.execute('SELECT github_verified_at,verification_expires_at,feature_evidence_json,ineligibility_reasons_json FROM task_candidate WHERE task_candidate_id=1').fetchone()
            self.assertEqual(row[0],self.now.isoformat())
            self.assertEqual(row[1],(self.now+timedelta(hours=24)).isoformat())
            self.assertFalse(json.loads(row[2])['automated_screening']['manual_review_required'])
            self.assertEqual(json.loads(row[3]).count('auto_screening_evidence_insufficient'),1)

    def test_missing_target_rolls_back_all_changes(self):
        bad={**self.ref,'identity':'o/r#99'}
        with self.assertRaises(ValueError):
            screen.apply_database(self.path,{'o/r#1':self.ref,'o/r#99':bad},self.now,'test-batch')
        with closing(sqlite3.connect(self.path)) as conn:
            evidence=json.loads(conn.execute('SELECT feature_evidence_json FROM task_candidate WHERE task_candidate_id=1').fetchone()[0])
            self.assertNotIn('automated_screening',evidence)


if __name__=='__main__':unittest.main()
