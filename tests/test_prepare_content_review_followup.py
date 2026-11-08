import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('prepare_review', Path(__file__).resolve().parents[1] / 'scripts/prepare_content_review_followup.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PrepareReviewFollowupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        code = self.root / 'src/oss_mentor/task_features.py'
        code.parent.mkdir(parents=True); code.write_text('fixture-code')
        self.source = self.root / 'source'; (self.source / 'evidence').mkdir(parents=True)
        self.target = self.root / 'review'; self.target.mkdir()
        jobs = [{'repository': 'o/r', 'issue_number': number, 'github_issue_id': str(number),
                 'status': 'needs_task_review', 'feature_review_reasons': ['task_type_requires_review']}
                for number in [1, 2]]
        (self.source / 'jobs.json').write_text(json.dumps(jobs))
        summary = {'feature_code_sha256': hashlib.sha256(code.read_bytes()).hexdigest(), 'reviewed_count': 2}
        (self.target / 'review_summary.json').write_text(json.dumps(summary))
        entries = [{'identity': 'o/r#1', 'decision': 'passes_content_gate', 'content_sha256': 'one'},
                   {'identity': 'o/r#2', 'decision': 'scope_needs_evidence', 'content_sha256': 'two'}]
        (self.target / 'review_results.jsonl').write_text('\n'.join(json.dumps(e) for e in entries))
        (self.root / 'data').mkdir()
        for name in ['oss_mentor.sqlite3', 'oss_mentor_web.sqlite3']:
            with closing(sqlite3.connect(self.root / 'data' / name)) as conn:
                conn.execute('CREATE TABLE sentinel (value TEXT)')
                conn.execute("INSERT INTO sentinel VALUES ('preserved')")
                conn.commit()
        for name, url in {'health': '/repos/o/r', 'approved_detail': '/repos/o/r/issues/1',
                          'unresolved_detail': '/repos/o/r/issues/2',
                          'timeline': '/repos/o/r/issues/2/timeline', 'pr': '/repos/o/r/pulls/3'}.items():
            (self.source / 'evidence' / (name + '.json')).write_text(json.dumps({'url': 'https://api.github.com'+url}))

    def run_prepare(self):
        with patch.object(module, 'ROOT', self.root), patch.object(sys, 'argv',
                ['prepare', '--directory', str(self.target), '--source-directory', str(self.source)]):
            module.main()

    def test_newly_admissible_records_get_fresh_status_evidence(self):
        self.run_prepare()
        self.assertEqual([p.name for p in (self.target / 'evidence').iterdir()], ['unresolved_detail.json'])
        self.assertTrue((self.source / 'evidence/approved_detail.json').exists())
        for name in ['oss_mentor.sqlite3', 'oss_mentor_web.sqlite3']:
            with closing(sqlite3.connect(self.target / (name + '.before'))) as conn:
                self.assertEqual(conn.execute('SELECT value FROM sentinel').fetchone()[0], 'preserved')

    def test_changed_rule_source_is_rejected_before_preparing_or_backing_up(self):
        (self.root / 'src/oss_mentor/task_features.py').write_text('changed')
        with self.assertRaises(SystemExit):
            self.run_prepare()
        self.assertFalse((self.target / 'jobs.json').exists())
        self.assertFalse((self.target / 'evidence').exists())


if __name__ == '__main__':
    unittest.main()
