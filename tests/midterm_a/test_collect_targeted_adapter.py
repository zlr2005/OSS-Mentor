from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/midterm_a'))
import collect_targeted_adapter as module


class TargetedOutcomeTests(unittest.TestCase):
    def test_expanded_search_covers_quarters_without_link_filter(self):
        class API:
            def __init__(self):
                self.queries = []
            def call(self, *, query):
                self.queries.append(query)
                return {'data': {'q0': {'issueCount': 0, 'nodes': [],
                    'pageInfo': {'hasNextPage': False, 'endCursor': None}}}}, 'cache.json'
        api = API()
        records, coverage = module.discover_expanded(api, {'developer_key': 'test', 'github_numeric_id': 42})
        self.assertEqual(records, [])
        self.assertEqual(len(coverage), 7)
        self.assertEqual(coverage[0]['start'], '2024-04-01')
        self.assertEqual(coverage[-1]['end'], '2025-12-31')
        self.assertTrue(all('linked:issue' not in q for q in api.queries))

    def test_expanded_search_splits_saturated_partition(self):
        class API:
            calls = 0
            def call(self, *, query):
                self.calls += 1
                return {'data': {'q0': {'issueCount': 1001 if self.calls == 1 else 0, 'nodes': [],
                    'pageInfo': {'hasNextPage': self.calls == 1, 'endCursor': 'cursor'}}}}, 'cache.json'
        _, coverage = module.discover_expanded(API(), {'developer_key': 'test', 'github_numeric_id': 42})
        leaves = [r for r in coverage if not r['partition_split']]
        self.assertEqual(len(leaves), 8)
        for left, right in zip(leaves, leaves[1:]):
            self.assertEqual(module.b.dt(right['start']+'T00:00:00Z') - module.b.dt(left['end']+'T00:00:00Z'), module.b.timedelta(days=1))

    def fixture(self, year=2024):
        url = 'https://github.com/source/repo/pull/1'
        month = '04' if year == 2024 else '07'
        closed = f'{year}-{month}-20T00:00:00Z'
        issue = dict(url='https://github.com/target/repo/issues/2', repository={'nameWithOwner': 'target/repo'},
                     number=2, createdAt=f'{year}-03-01T00:00:00Z', closedAt=closed, state='CLOSED',
                     title='fix bug', body='historical body', lastEditedAt=None,
                     timelineItems={'pageInfo': {'hasNextPage': False}, 'nodes': [
                         {'__typename': 'ClosedEvent', 'createdAt': closed,
                          'closer': {'__typename': 'PullRequest', 'url': url, 'mergedAt': closed}}]})
        pr = dict(url=url, merged=True, createdAt=f'{year}-{month}-02T00:00:00Z', mergedAt=closed,
                  author={'databaseId': 42, 'login': 'developer'},
                  closingIssuesReferences={'nodes': [issue], 'pageInfo': {'hasNextPage': False}})
        seed = dict(url=url, repository='source/repo', author={'databaseId': 42}, seed_identity={})
        return pr, seed

    def test_cross_repository_outcome_uses_actual_issue_repository(self):
        pr, seed = self.fixture()
        accepted, _ = module.outcome_rows(pr, seed, 'cache.json',
            {'sparse_ids': {42}, 'training_repositories': set()}, set())
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]['repository'], 'target/repo')
        self.assertEqual(accepted[0]['pr_repository'], 'source/repo')
        pr['closingIssuesReferences']['nodes'][0]['timelineItems']['nodes'][0]['closer']['url'] += 'wrong'
        self.assertFalse(module.outcome_rows(pr, seed, 'cache.json',
            {'sparse_ids': {42}, 'training_repositories': set()}, set())[0])

    def test_earlier_years_require_explicit_admission_scope(self):
        pr, seed = self.fixture(2023)
        targets = {'sparse_ids': {42}, 'training_repositories': set(), 'allow_test': False}
        self.assertFalse(module.outcome_rows(pr, seed, 'cache.json', targets, set())[0])
        accepted, _ = module.outcome_rows(pr, seed, 'cache.json',
            dict(targets, training_years=(2022, 2023)), set())
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]['year'], 2023)

    def test_preinteraction_cutoff_requires_issue_to_exist_before_interaction(self):
        pr, seed = self.fixture()
        issue = pr['closingIssuesReferences']['nodes'][0]
        issue['createdAt'] = '2024-04-01T12:00:00Z'
        targets = {'sparse_ids': {42}, 'training_repositories': set(), 'allow_test': False}
        self.assertFalse(module.outcome_rows(pr, seed, 'cache.json', targets, set())[0])
        targets['profile_cutoff_policy'] = 'pre_interaction'
        accepted, _ = module.outcome_rows(pr, seed, 'cache.json', targets, set())
        self.assertEqual(accepted[0]['profile_as_of'], '2024-04-01T23:59:59Z')
        issue['createdAt'] = pr['createdAt']
        self.assertFalse(module.outcome_rows(pr, seed, 'cache.json', targets, set())[0])

    def test_new_repository_test_scope_respects_frozen_training_repositories(self):
        pr, seed = self.fixture(2026)
        targets = {'sparse_ids': set(), 'training_repositories': {'target/repo'}}
        self.assertFalse(module.outcome_rows(pr, seed, 'cache.json', targets, set())[0])
        targets['training_repositories'] = set()
        accepted, _ = module.outcome_rows(pr, seed, 'cache.json', targets, set())
        self.assertEqual(accepted[0]['intended_split'], 'test')

    def test_training_visible_history_and_new_training_labels_remove_novelty(self):
        outcomes = [dict(intended_split='test', repository=repo) for repo in ('new/r', 'history/r', 'label/r')]
        outcomes.append(dict(intended_split='train', repository='label/r', github_numeric_id=42,
                             profile_as_of='2025-04-01T00:00:00Z'))
        history = [dict(repository='history/r', github_numeric_id=42, event_at='2025-03-01T00:00:00Z'),
                   dict(repository='new/r', github_numeric_id=42, event_at='2026-01-01T00:00:00Z')]
        self.assertEqual([r['repository'] for r in module.repository_novelty(outcomes, history, set(), {})], ['new/r'])


if __name__ == '__main__':
    unittest.main()
