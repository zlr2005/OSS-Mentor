import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/midterm_a'))
spec = importlib.util.spec_from_file_location('continuation', ROOT / 'scripts/midterm_a/continue_adapter_backfill.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ContinuationTests(unittest.TestCase):
    def row(self, number, identity=42, created='2025-04-02T00:00:00Z'):
        return {'pr_url': f'https://github.com/o/r/pull/{number}', 'pr_number': number,
                'repository': 'o/r', 'created_at': created, 'merged_at': created,
                'github_login': f'user{identity}', 'github_numeric_id': identity,
                'verified_seed_author': True, 'evidence_refs': ['api_cache/a.json']}

    def test_skip_existing_duplicate_unverified_and_out_of_scope(self):
        index = [self.row(1), self.row(2), self.row(2), self.row(3, 99),
                 self.row(4, created='2024-02-02T00:00:00Z'),
                 self.row(5, created='2026-02-02T00:00:00Z')]
        selected = [{'url': self.row(1)['pr_url']}]
        added = module.choose(index, selected, {'42': {}}, 20)
        self.assertEqual([r['number'] for r in added], [2])
        self.assertEqual(len(selected), 1)

    def test_rotate_authors_before_second_pr_and_stable_resume_selection(self):
        index = [self.row(1), self.row(2), self.row(3, 43)]
        seeds = {'42': {}, '43': {}}
        first = module.choose(index, [], seeds, 2)
        self.assertEqual([r['number'] for r in first], [1, 3])
        self.assertEqual(module.choose(list(reversed(index)), [], seeds, 2), first)
        self.assertEqual([r['number'] for r in module.choose(index, first, seeds, 2)], [2])

    def test_explicit_three_year_scope_accepts_2026_but_not_2027(self):
        index = [self.row(6, created='2026-02-02T00:00:00Z'),
                 self.row(7, created='2027-02-02T00:00:00Z')]
        added = module.choose(index, [], {'42': {}}, 20, years=(2024, 2025, 2026))
        self.assertEqual([r['number'] for r in added], [6])

    def test_later_2026_balances_quarters_and_preserves_existing_selection(self):
        index = [self.row(i, created=f'2026-{month:02d}-02T00:00:00Z')
                 for i, month in enumerate((1, 4, 4, 7, 7, 10, 10), 1)]
        existing = [{'url': index[1]['pr_url']}]
        added = module.choose_later_2026(index, existing, {'42': {}}, 3)
        self.assertEqual([r['number'] for r in added], [3, 4, 6])
        self.assertEqual(len(existing), 1)

    def test_sparse_history_prioritizes_past_only_and_unique_windows(self):
        outcomes = [{'github_numeric_id': 43, 'outcome_id': 'past',
                     'outcome_at': '2025-02-01T00:00:00Z',
                     'profile_as_of': '2025-01-01T00:00:00Z'},
                    {'github_numeric_id': 42, 'outcome_id': 'future',
                     'outcome_at': '2025-06-01T00:00:00Z',
                     'profile_as_of': '2025-01-01T00:00:00Z'}]
        index = [self.row(1), self.row(2, 43), self.row(3, 43),
                 self.row(4, 43, '2026-04-02T00:00:00Z')]
        added = module.choose_sparse_history(index, [], {'42': {}, '43': {}}, outcomes, 10)
        self.assertEqual([r['number'] for r in added], [2, 1])
        self.assertEqual(module.choose_sparse_history(index, [], {'42': {}, '43': {}}, outcomes, 1)[0]['number'], 2)

    def test_sparse_history_falls_back_to_covered_but_excludes_more_than_two(self):
        outcomes = [{'github_numeric_id': 42, 'outcome_id': str(i),
                     'outcome_at': '2025-02-01T00:00:00Z',
                     'profile_as_of': '2025-01-01T00:00:00Z'} for i in range(3)]
        outcomes.append({'github_numeric_id': 43, 'outcome_id': 'covered',
                         'outcome_at': '2025-05-01T00:00:00Z',
                         'profile_as_of': '2025-04-01T00:00:00Z'})
        added = module.choose_sparse_history([self.row(1), self.row(2, 43)],
                         [], {'42': {}, '43': {}}, outcomes, 10)
        self.assertEqual([r['number'] for r in added], [2])

    def test_training_cohort_excludes_other_accounts_and_future_labels(self):
        accounts = [{'github_numeric_id': 42, 'developer_train_outcomes': 2},
                    {'github_numeric_id': 43, 'developer_train_outcomes': 3}]
        late = self.row(4, created='2025-12-01T00:00:00Z')
        late['merged_at'] = '2026-01-01T00:00:00Z'
        index = [self.row(1), self.row(2, 43), self.row(3, 99), late,
                 self.row(5, created='2026-04-02T00:00:00Z')]
        added = module.choose_training_cohort(index, [], {'42': {}, '43': {}, '99': {}}, accounts, 20)
        self.assertEqual([r['number'] for r in added], [1])

    def test_training_cohort_preserves_multiple_outcomes_per_window(self):
        accounts = [{'github_numeric_id': 42, 'developer_train_outcomes': 1}]
        added = module.choose_training_cohort([self.row(1), self.row(2), self.row(3)],
                    [{'url': self.row(1)['pr_url']}], {'42': {}}, accounts, 20)
        self.assertEqual([r['number'] for r in added], [2, 3])

    def test_explicit_new_account_selection_preserves_old_cohort_policy(self):
        index = [self.row(1)]
        self.assertEqual(module.choose_training_cohort(index, [], {'42': {}},
                         [{'github_numeric_id': 42, 'developer_train_outcomes': 0}], 10), [])
        added = module.choose_training_accounts(index, [], {'42': {}}, {42}, 10)
        self.assertEqual([r['number'] for r in added], [1])


if __name__ == '__main__':
    unittest.main()
