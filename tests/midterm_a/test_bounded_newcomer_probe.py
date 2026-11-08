from pathlib import Path
import sys
import unittest
import hashlib
import json
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/midterm_a'))
from optimized_teacher_collection import make_batch
from run_bounded_newcomer_probe import probe_sql,select_tasks,MAX_TASK_QUERIES,MAX_ROWS,MAX_BYTES,INTERVAL


class BoundedProbeTests(unittest.TestCase):
    def test_probe_only_lowers_existing_query_limits_and_keeps_scope(self):
        batch=make_batch(dict(repository='example/repo',repo_id=10,owner_id=20,
            discovery_filter='newcomer',query_layout='action_prewhere_v2',event_action='labeled'),202601,1767196800,1769875200)
        query=probe_sql(batch)
        for value in ("action='labeled'",'repo_id=10','LIMIT 20','max_threads=1',
                      'max_rows_to_read=100000','max_bytes_to_read=8388608','max_execution_time=3'):
            self.assertIn(value,query)
        self.assertTrue(query.startswith('SELECT DISTINCT '));self.assertNotIn(';',query)
        self.assertEqual(MAX_TASK_QUERIES,2);self.assertEqual(MAX_ROWS,100000)
        self.assertEqual(MAX_BYTES,8388608);self.assertGreaterEqual(INTERVAL,15)


class ProbeSelectionTests(unittest.TestCase):
    def setUp(self):
        parent=Path.cwd()/'tmp'/'bounded_probe_selection_tests';parent.mkdir(parents=True,exist_ok=True)
        self.temporary=tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.previous=Path(self.temporary.name)/'previous';self.previous.mkdir()
        self.batch=make_batch(dict(repository='example/repo',repo_id=10,owner_id=20,
            discovery_filter='all_opened',query_layout='action_prewhere_v2',event_action='opened'),202610,1792598400,1792684800)
        checkpoint=self.previous/'checkpoint.json';checkpoint.write_text(json.dumps(dict(pending=[self.batch])),encoding='utf-8')
        self.plan=dict(source_run=str(self.previous),source_checkpoint_sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest(),batches=[self.batch])
        self.path=Path(self.temporary.name)/'plan.json'

    def save(self):self.path.write_text(json.dumps(self.plan),encoding='utf-8')

    def test_explicit_day_scope_is_preserved_and_default_selection_still_works(self):
        self.save()
        self.assertEqual(select_tasks(self.previous,self.path),[self.batch])
        self.assertEqual(select_tasks(self.previous),[self.batch])
        self.assertEqual(self.batch['end_epoch']-self.batch['start_epoch'],86400)

    def test_changed_main_checkpoint_rejects_selection(self):
        self.save();(self.previous/'checkpoint.json').write_text('{}',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'checkpoint changed'):select_tasks(self.previous,self.path)

    def test_more_than_two_queries_is_rejected(self):
        self.plan['batches']=[self.batch]*3;self.save()
        with self.assertRaisesRegex(ValueError,'cap exceeded'):select_tasks(self.previous,self.path)

    def test_modified_sql_cannot_raise_guards(self):
        self.plan['batches']=[dict(self.batch,sql=self.batch['sql'].replace('max_execution_time=15','max_execution_time=150'))]
        self.save()
        with self.assertRaisesRegex(ValueError,'fixed template'):select_tasks(self.previous,self.path)

    def test_plan_for_another_source_is_rejected(self):
        self.plan['source_run']=str(self.previous.parent/'another');self.save()
        with self.assertRaisesRegex(ValueError,'source mismatch'):select_tasks(self.previous,self.path)


if __name__=='__main__':unittest.main()
