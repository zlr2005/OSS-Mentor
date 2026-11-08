"""验证历史回放边界，禁止未来证据或未核实正文进入试采样本。"""
import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('adapter_pilot_samples', ROOT / 'scripts/midterm_a/advance_adapter_pilot_samples.py')
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


class HistoricalReplayTests(unittest.TestCase):
    def test_title_undoes_only_post_cutoff_renames(self):
        events = [
            {'id': 1, 'event': 'renamed', 'created_at': '2025-03-01T00:00:00Z', 'rename': {'from': 'original', 'to': 'at cutoff'}},
            {'id': 2, 'event': 'renamed', 'created_at': '2025-05-01T00:00:00Z', 'rename': {'from': 'at cutoff', 'to': 'today'}},
        ]
        self.assertEqual(pilot.replay_title('today', events, '2025-04-01T00:00:00Z'), 'at cutoff')

    def test_title_refuses_inconsistent_or_truncated_history(self):
        event = {'id': 1, 'event': 'renamed', 'created_at': '2025-05-01T00:00:00Z', 'rename': {'from': 'old', 'to': 'unexpected'}}
        with self.assertRaises(ValueError):
            pilot.replay_title('today', [event], '2025-04-01T00:00:00Z')
        with self.assertRaises(ValueError):
            pilot.replay_title('today', [event] * 100, '2025-04-01T00:00:00Z')

    def test_replay_preserves_unknowns_and_rejects_future_contribution(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp', prefix='adapter-replay-test-') as directory:
            root = Path(directory)
            (root / 'evidence').mkdir()
            (root / 'evidence/selected_0_2025_history.json').write_text(json.dumps({'observed_at': '2026-11-02 13:47:33 UTC'}))
            content = b'print(1)\n'
            (root / 'source.py').write_bytes(content)
            sample = {'sample_key': 'selected_0_2025', 'year': 2025, 'github_login': 'Example', 'repository': 'owner/repo'}
            history = {'github_login': 'Example', 'github_numeric_id': 42, 'developer_key': 'opaque-42',
                'repository': 'owner/repo', 'as_of': '2025-04-01T00:00:00+00:00',
                'event_at': '2025-03-01T00:00:00Z', 'author_at': '2025-03-01T00:00:00Z',
                'commit_sha': 'a' * 40, 'evidence_ref': 'evidence/selected_0_2025_history.json',
                'code_documents': [{'path': 'src/main.py', 'content_location': 'source.py',
                    'content_sha256': hashlib.sha256(content).hexdigest()}]}
            q = pilot.load_questionnaire(pilot.QUESTIONNAIRE)
            with patch.object(pilot, 'SOURCE', root):
                first, _, _ = pilot.replay_one(sample, [history], q)
                second, _, _ = pilot.replay_one(sample, [history], q)
                self.assertEqual(first, second)
                self.assertEqual(sum(first['observed_mask']), 2)
                self.assertEqual(first['current_interests'], [])
                self.assertTrue(all(v == 0 for v, mask in zip(first['values'], first['observed_mask']) if not mask))
                self.assertEqual(first['profile_state'], 'research_preview')
                history['event_at'] = history['as_of']
                with self.assertRaises(AssertionError):
                    pilot.replay_one(sample, [history], q)


class HistoricalBodyImportTests(unittest.TestCase):
    def run_import(self, edited_at='2025-03-01T00:00:00Z', omit_last_edited=False):
        record = {'source_url': 'https://github.com/o/r/issues/1', 'as_of': '2025-04-01T00:00:00Z',
            'title_at_cutoff': 'historic title', 'body_at_cutoff': None, 'training_ready': False}
        issue = {'url': record['source_url'], 'createdAt': '2025-01-01T00:00:00Z',
            'title': 'current title', 'body': 'historical body', 'lastEditedAt': edited_at}
        if omit_last_edited:
            del issue['lastEditedAt']
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp', prefix='adapter-body-test-') as directory:
            root = Path(directory)
            (root / 'response.json').write_text(json.dumps({'data': {'q0': {'issue': issue}}}))
            with patch.object(pilot, 'OUT', root):
                pilot.lines('historical_issue_content.jsonl', [record])
                pilot.import_graphql_response(root / 'response.json')
                return pilot.rows(root / 'historical_issue_content.jsonl')[0]

    def test_explicit_pre_cutoff_edit_accepts_body_but_not_training(self):
        row = self.run_import()
        self.assertEqual(row['body_at_cutoff'], 'historical body')
        self.assertTrue(row['model_text_verified'])
        self.assertFalse(row['training_ready'])

    def test_explicit_null_edit_time_is_distinct_from_missing_field(self):
        self.assertEqual(self.run_import(None)['body_at_cutoff'], 'historical body')
        missing = self.run_import(omit_last_edited=True)
        self.assertIsNone(missing['body_at_cutoff'])
        self.assertEqual(missing['body_status'], 'explicit_last_edited_field_missing')

    def test_post_cutoff_edit_does_not_backfill_current_body(self):
        row = self.run_import('2025-04-02T00:00:00Z')
        self.assertIsNone(row['body_at_cutoff'])
        self.assertNotIn('model_text_verified', row)


if __name__ == '__main__':
    unittest.main()
