"""Audit accounting must distinguish repository context from task-level evidence."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone, timedelta


spec = importlib.util.spec_from_file_location(
    'audit_model_readiness', Path(__file__).resolve().parents[1] / 'scripts/audit_model_readiness.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class ModelReadinessAuditTests(unittest.TestCase):
    def test_repository_overlap_is_not_issue_overlap(self):
        rows = [{'repository': 'Owner/Repo', 'issue_number': 1},
                {'repository': 'owner/repo', 'issue_number': 2},
                {'repository': 'Other/Project', 'issue_number': 3}]
        cache = {'repositories': {'owner/repo': {}}, 'issue_keys': {'owner/repo#1'}}
        result = audit.coverage(rows, cache)
        self.assertEqual(result['repository_count'], 2)
        self.assertEqual(result['paper_repository_overlap'], 1)
        self.assertEqual(result['candidates_in_paper_repositories'], 2)
        self.assertEqual(result['paper_issue_identity_overlap'], 1)
        self.assertEqual(result['missing_paper_repository_cache'], ['other/project'])

    def test_empty_ready_pool_has_explicit_zero_denominator(self):
        result = audit.coverage([], {'repositories': {}, 'issue_keys': set()})
        self.assertEqual(result['candidate_count'], 0)
        self.assertEqual(result['paper_repository_overlap'], 0)

    def test_archive_inspection_does_not_extract_upstream_programs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cache.zip'
            prefix = 'issue-pilot/repo_analysis_output/Owner/Repo/'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr(prefix + 'issues.json', json.dumps({
                    'repository_info': {'extracted_at': '2025-10-01T00:00:00'},
                    'issues': [{'number': 1}]}))
                archive.writestr(prefix + 'metadata.json', json.dumps({'directory_structure': {'x': None}}))
                archive.writestr('../never_execute.py', 'raise RuntimeError()')
            result = audit.paper_cache(path)
            self.assertEqual(result['issue_keys'], {'owner/repo#1'})
            self.assertTrue(result['repositories']['owner/repo']['directory_structure_present'])
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_read_database_never_creates_missing_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'missing.sqlite3'
            with self.assertRaises(sqlite3.OperationalError):
                audit.read_database(path, 'task_candidate')
            self.assertFalse(path.exists())

    def test_inventory_clock_keeps_original_expiry_and_rejects_future(self):
        now = datetime(2026, 10, 25, tzinfo=timezone.utc)
        for verified, expiry in [(now - timedelta(hours=25), now + timedelta(hours=1)),
                                 (now + timedelta(seconds=1), now + timedelta(hours=24)),
                                 (now - timedelta(hours=1), now)]:
            self.assertFalse(audit.verification_is_current(
                {'verified_at': verified.isoformat(), 'expires_at': expiry.isoformat()}, now=now))


if __name__ == '__main__':
    unittest.main()
