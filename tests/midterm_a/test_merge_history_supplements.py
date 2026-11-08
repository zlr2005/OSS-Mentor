from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/midterm_a'))
import merge_ab_history as module


class SupplementMergeTests(unittest.TestCase):
    def test_duplicate_outcome_preserves_sources_and_rejects_changed_identity(self):
        row = dict(outcome_id='id', github_numeric_id=42, issue_key='o/r#1',
                   profile_as_of='t1', outcome_at='t2', issue_content={'content_sha256': 'same'}, evidence_ref='a.json')
        with patch.object(Path, 'exists', return_value=True), patch.object(module, 'rows', side_effect=[[row], [row]]):
            result = module.merge_outcomes({'backfill': Path('a'), 'outcome_1': Path('b')})
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]['evidence_sources']), 2)
        with patch.object(Path, 'exists', return_value=True), patch.object(module, 'rows', side_effect=[[row], [dict(row, github_numeric_id=99)]]):
            with self.assertRaisesRegex(ValueError, 'duplicate outcome'):
                module.merge_outcomes({'backfill': Path('a'), 'outcome_1': Path('b')})

    def test_keep_duplicate_candidate_provenance_and_reject_conflict(self):
        row = dict(issue_key='o/r#1', as_of='2025-01-01', content_sha256='same', evidence_ref='a.json')
        with patch.object(module, 'rows', side_effect=[[row], [dict(row, evidence_ref='b.json')]]):
            result = module.merge_controls({'backfill': Path('a'), 'depth_1': Path('b')})
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]['evidence_sources']), 2)
        with patch.object(module, 'rows', side_effect=[[row], [dict(row, content_sha256='different')]]):
            with self.assertRaisesRegex(ValueError, 'snapshot conflict'):
                module.merge_controls({'backfill': Path('a'), 'depth_1': Path('b')})

    def test_comparisons_respect_repository_cutoff_and_all_known_positives(self):
        outcomes = [dict(outcome_id='one', github_numeric_id=42, repository='O/R', profile_as_of='t1', issue_key='o/r#1'),
                    dict(outcome_id='two', github_numeric_id=42, repository='o/r', profile_as_of='t2', issue_key='o/r#2')]
        controls = [dict(repository='o/r', as_of='t1', issue_key=k) for k in ('o/r#1', 'o/r#2', 'o/r#3')]
        controls.append(dict(repository='else/repo', as_of='t1', issue_key='else/repo#1'))
        result = module.comparison_groups(outcomes, controls)
        self.assertEqual(result[0]['comparison_issue_keys'], ['o/r#3'])
        self.assertEqual(result[1]['comparison_issue_keys'], [])


if __name__ == '__main__':
    unittest.main()
