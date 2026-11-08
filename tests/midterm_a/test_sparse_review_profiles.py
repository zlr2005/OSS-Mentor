"""Exercise reproducible research-preview delivery using a small synthetic dataset."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/midterm_a'))
import build_sparse_review_profiles as builder


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + '\n', encoding='utf-8')


class SparseReviewBuildTests(unittest.TestCase):
    def test_preview_delivery_is_repeatable_and_rejects_nonempty_output(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            base = Path(directory)
            source = base / 'sparse_expansion_20261104_handoff_v6'
            merged = base / 'merged_ab_history_2024_2026_v6'
            history = base / 'history'
            history.mkdir()
            content = b'print("hello")\n'
            (history / 'hello.py').write_bytes(content)
            record = dict(github_numeric_id=42, repository='example/repo', commit_sha='abc',
                          path='hello.py', event_at='2020-01-01T00:00:00Z',
                          content_location='hello.py', evidence_ref='evidence.json',
                          content_sha256=hashlib.sha256(content).hexdigest())
            write_json(history / 'historical_contributions.jsonl', record)
            write_json(history / 'manifest.json', {})
            write_json(merged / 'manifest.json', {})
            (merged / 'file_contributions.jsonl').write_text('', encoding='utf-8')
            write_json(source / 'manifest.json', {})
            write_json(source / 'source_manifest.json', {'source_roots': {'history': history.relative_to(ROOT).as_posix()}})
            write_json(source / 'outcome_index.jsonl', dict(outcome_id='one', github_numeric_id=42,
                       profile_as_of='2021-01-01T00:00:00Z', developer_key='example'))
            write_json(source / 'comparison_index.jsonl', dict(outcome_id='one', github_numeric_id=42,
                       as_of='2021-01-01T00:00:00Z'))

            def run(output):
                with patch.object(builder.b, 'OUT', base / 'original'), patch.object(sys, 'argv', [
                    'build', '--output', str(output), '--observed-at', '2026-11-05T00:00:00Z']):
                    builder.main()

            first, second = base / 'first', base / 'second'
            original_read = Path.read_bytes
            dependency_paths = {ROOT / p for p in (
                'src/oss_mentor/profiling/evidence.py', 'src/oss_mentor/profiling/assessment.py',
                'config/midterm_profile/questionnaire_v0.1.json', 'scripts/midterm_a/build_sparse_review_profiles.py',
                'scripts/midterm_a/backfill_adapter_2024_2026.py', 'src/oss_mentor/profiling/delivery.py',
                'src/oss_mentor/datasets/code_languages.py')}

            def windows_newlines(path):
                data = original_read(path)
                return data.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n') if path in dependency_paths else data

            def unix_newlines(path):
                data = original_read(path)
                return data.replace(b'\r\n', b'\n') if path in dependency_paths else data

            with patch.object(Path, 'read_bytes', unix_newlines):
                run(first)
            with patch.object(Path, 'read_bytes', windows_newlines):
                run(second)
            names = {p.name for p in first.iterdir()}
            self.assertEqual(names, {p.name for p in second.iterdir()})
            for name in names:
                self.assertEqual((first / name).read_bytes(), (second / name).read_bytes(), name)
            manifest = json.loads((first / 'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(set(manifest['files_sha256']), names - {'manifest.json'})
            for name, digest in manifest['files_sha256'].items():
                self.assertEqual(hashlib.sha256((first / name).read_bytes()).hexdigest(), digest)
            profile = json.loads((first / 'profile_previews.jsonl').read_text(encoding='utf-8'))
            other_profile = json.loads((second / 'profile_previews.jsonl').read_text(encoding='utf-8'))
            self.assertEqual(profile['profile_version'], other_profile['profile_version'])
            self.assertEqual(profile['schema_version'], 'enhanced-profile-export-v0.1')
            self.assertEqual(profile['profile_state'], 'research_preview')
            self.assertFalse(profile['training_ready'])
            self.assertEqual(profile['b_review_status'], 'pending')
            with self.assertRaisesRegex(ValueError, 'must be empty'):
                run(first)


if __name__ == '__main__':
    unittest.main()
