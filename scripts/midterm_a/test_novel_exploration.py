"""Boundary checks for scene eligibility and conditional denominators."""
import unittest
import numpy as np
from evaluate_novel_exploration import exploration_pool, metrics, rank_of


class ExplorationTests(unittest.TestCase):
    def test_excludes_case_variants_without_injecting_target(self):
        keys = ['Org/Familiar#1', 'other/new#2', 'other/new#3']
        self.assertEqual(exploration_pool(keys, [('org/familiar', 'sha', 'file')]), keys[1:])
        self.assertEqual(exploration_pool(keys, []), keys)
        self.assertEqual(exploration_pool(keys[:1], [('org/familiar', 'sha', 'file')]), [])

    def test_absent_target_and_conservative_ties(self):
        self.assertIsNone(rank_of([], np.array([]), 'absent#1'))
        self.assertEqual(rank_of(['a#1', 'b#2'], np.array([1., 1.]), 'a#1'), 2)

    def test_coverage_and_conditional_denominators(self):
        records = [dict(github_numeric_id=1, exploration_rank=2, original_rank=10),
                   dict(github_numeric_id=2, exploration_rank=None, original_rank=1)]
        result = metrics(records)
        self.assertEqual(result['target_coverage'], .5)
        self.assertEqual(result['conditional_recall5'], 1)
        self.assertEqual(result['all_outcome_recall5'], .5)
        self.assertEqual(result['same_cohort_unfiltered_recall5'], 0)
        self.assertIsNone(metrics(records[1:])['conditional_mrr'])


if __name__ == '__main__':
    unittest.main()
