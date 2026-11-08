"""历史补采的时间、正文和采样边界测试。"""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import contextlib
import io
import base64
import hashlib

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('adapter_backfill', ROOT / 'scripts/midterm_a/backfill_adapter_2024_2025.py')
backfill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backfill)


class BackfillBoundaryTests(unittest.TestCase):
    def issue(self):
        return {'url': 'https://github.com/o/r/issues/1', 'createdAt': '2024-02-01T00:00:00Z',
            'title': 'current title', 'body': 'body before cutoff', 'lastEditedAt': '2024-03-01T00:00:00Z',
            'timelineItems': {'pageInfo': {'hasNextPage': False}, 'nodes': []}}

    def test_explicit_body_edit_time_and_rename_are_replayed_separately(self):
        issue = self.issue()
        issue['timelineItems']['nodes'] = [{'__typename': 'RenamedTitleEvent',
            'createdAt': '2024-05-01T00:00:00Z', 'previousTitle': 'historic title', 'currentTitle': 'current title'}]
        candidate, reason = backfill.issue_at(issue, backfill.dt('2024-04-01T00:00:00Z'))
        self.assertIsNone(reason)
        self.assertEqual(candidate['title_at_cutoff'], 'historic title')
        self.assertEqual(candidate['body_at_cutoff'], 'body before cutoff')

    def test_future_body_or_missing_edit_field_stays_excluded(self):
        issue = self.issue()
        issue['lastEditedAt'] = '2024-04-02T00:00:00Z'
        self.assertIsNone(backfill.issue_at(issue, backfill.dt('2024-04-01T00:00:00Z'))[0])
        del issue['lastEditedAt']
        self.assertIsNone(backfill.issue_at(issue, backfill.dt('2024-04-01T00:00:00Z'))[0])
        issue['lastEditedAt'] = None
        self.assertIsNotNone(backfill.issue_at(issue, backfill.dt('2024-04-01T00:00:00Z'))[0])

    def test_closed_then_reopened_candidate_availability(self):
        issue = self.issue()
        issue['timelineItems']['nodes'] = [
            {'__typename': 'ClosedEvent', 'createdAt': '2024-03-02T00:00:00Z'},
            {'__typename': 'ReopenedEvent', 'createdAt': '2024-05-01T00:00:00Z'}]
        self.assertEqual(backfill.issue_at(issue, backfill.dt('2024-04-01T00:00:00Z'))[1], 'issue_closed_at_cutoff')
        self.assertIsNotNone(backfill.issue_at(issue, backfill.dt('2024-06-01T00:00:00Z'))[0])

    def test_created_after_cutoff_and_truncated_timeline_are_excluded(self):
        issue = self.issue()
        issue['createdAt'] = '2024-04-02T00:00:00Z'
        self.assertIsNone(backfill.issue_at(issue, backfill.dt('2024-04-01T00:00:00Z'))[0])
        issue = self.issue()
        issue['timelineItems']['pageInfo']['hasNextPage'] = True
        self.assertIsNone(backfill.issue_at(issue, backfill.dt('2024-04-01T00:00:00Z'))[0])

    def test_stratified_files_do_not_collapse_to_first_source_file(self):
        files = [{'filename': p, 'status': 'modified'} for p in
                 ['src/main.rs', 'src/other.rs', 'tests/test_main.rs', 'docs/guide.md']]
        files.append({'filename': 'tests/deleted.rs', 'status': 'removed'})
        self.assertEqual([r['filename'] for r in backfill.choose_files(files)],
                         ['src/main.rs', 'tests/test_main.rs', 'docs/guide.md'])

    def test_quarter_cutoff_is_before_later_pr_creation(self):
        self.assertEqual(backfill.iso(backfill.quarter('2025-08-20T14:30:00Z')), '2025-07-01T00:00:00Z')
        with self.assertRaises(ValueError):
            backfill.dt('2025-01-01T00:00:00')

    def test_api_rejects_mutation_or_non_repository_rest_requests(self):
        client = backfill.API(network=False)
        with self.assertRaises(ValueError):
            client.call(query='mutation { deleteIssue(input:{id:"x"}) { clientMutationId } }')
        with self.assertRaises(ValueError):
            client.call(endpoint='/user')

    def test_profiles_merge_same_account_and_cutoff_across_repositories(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp', prefix='adapter-aggregate-') as directory:
            output = Path(directory)
            records = []
            for i, (repo, path) in enumerate([('o/source', 'src/main.py'), ('o/tests', 'tests/test_main.py')]):
                records.append({'developer_key': 'example', 'github_numeric_id': 42,
                    'repository': repo, 'as_of': '2024-04-01T00:00:00Z',
                    'file_version_key': f'v{i}', 'path': path, 'commit_sha': str(i) * 40,
                    'event_at': '2024-03-01T00:00:00Z', 'evidence_ref': 'source.json'})
            (output / 'source.json').write_text(json.dumps({'observed_at': '2026-11-02T00:00:00Z'}))
            (output / 'historical_contributions.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in records))
            with patch.object(backfill, 'OUT', output):
                backfill.build_profiles()
                profiles = backfill.rows(output / 'profile_previews.jsonl')
            self.assertEqual(len(profiles), 1)
            self.assertEqual(profiles[0]['repository_scopes'], ['o/source', 'o/tests'])
            self.assertEqual(sum(profiles[0]['observed_mask']), 4)
            self.assertEqual(profiles[0]['profile_state'], 'research_preview')

    def test_full_scope_includes_minor_languages(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp', prefix='adapter-scope-') as directory:
            root = Path(directory)
            source = root / 'source'
            (source / 'tables').mkdir(parents=True)
            repositories = [{'repository_key': str(i), 'full_name': f'o/r{i}', 'primary_language': language}
                            for i, language in enumerate(['Python', 'Go', 'C#'])]
            (source / 'tables/repositories.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in repositories))
            (source / 'tables/contributions.jsonl').write_text('')
            seeds = root / 'seeds.json'
            seeds.write_text('{}')
            with patch.object(backfill, 'OUT', root / 'output'), patch.object(backfill, 'SOURCE', source), patch.object(backfill, 'SEEDS', seeds):
                plan = backfill.plan(3)
            self.assertEqual({r['name'] for r in plan['repositories']}, {'o/r0', 'o/r1', 'o/r2'})

    def test_pagination_preserves_old_prs_and_replays_without_duplicate_selection(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp', prefix='adapter-pages-') as directory:
            root = Path(directory)
            seeds = root / 'seeds.json'
            seeds.write_text(json.dumps({'2': {'developer_key': 'opaque-2'}}))
            baseline = [{'url': 'https://github.com/o/r/pull/1', 'repository': 'o/r', 'year': 2025,
                         'author': {'databaseId': 1, 'login': 'old'}}]
            node = {'url': 'https://github.com/o/r/pull/2', 'number': 2, 'createdAt': '2025-02-01T00:00:00Z',
                    'mergedAt': '2025-02-02T00:00:00Z', 'author': {'databaseId': 2, 'login': 'new'}}

            class FakeAPI:
                def call(self, **kwargs):
                    return {'data': {'q0': {'nodes': [node], 'issueCount': 101,
                        'pageInfo': {'hasNextPage': False, 'endCursor': 'last'}}}}, 'api_cache/example.json'

            with patch.object(backfill, 'OUT', root), patch.object(backfill, 'SEEDS', seeds), contextlib.redirect_stdout(io.StringIO()):
                backfill.save('search_coverage.json', [{'repository': 'o/r', 'year': 2025,
                    'search_truncated': True, 'continuation_cursor': 'first'}])
                backfill.save('selected_prs.json', baseline)
                backfill.discover_pages(FakeAPI(), {'detail_limit_per_author_year': 4})
                first = (root / 'selected_prs.json').read_bytes()
                backfill.discover_pages(FakeAPI(), {'detail_limit_per_author_year': 4})
                self.assertEqual(first, (root / 'selected_prs.json').read_bytes())
                self.assertEqual(len(backfill.read(root / 'selected_prs.json')), 2)

    def test_control_batch_filters_actual_positive_issue(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp', prefix='adapter-controls-') as directory:
            root = Path(directory)
            positive = self.issue()
            positive['url'] = 'https://github.com/o/r/issues/1'
            control = copy.deepcopy(positive)
            control['url'] = 'https://github.com/o/r/issues/2'

            class FakeAPI:
                def call(self, **kwargs):
                    return {'data': {'q0': {'nodes': [positive, control]}}}, 'api_cache/example.json'

            with patch.object(backfill, 'OUT', root), contextlib.redirect_stdout(io.StringIO()):
                backfill.lines('verified_outcomes.jsonl', [{'repository': 'o/r',
                    'profile_as_of': '2024-04-01T00:00:00Z', 'issue_key': 'o/r#1'}])
                backfill.controls(FakeAPI(), {'control_candidates_per_repo_cutoff': 3})
                records = backfill.rows(root / 'historical_controls.jsonl')
            self.assertEqual([r['issue_key'] for r in records], ['o/r#2'])

    def test_cached_evidence_tamper_is_rejected_without_network(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp', prefix='adapter-cache-') as directory:
            root = Path(directory)
            request = {'query': None, 'endpoint': '/repos/o/r/commits?per_page=3'}
            identity = backfill.digest(backfill.canonical(request))
            envelope = {'request': request, 'payload': [], 'payload_sha256': backfill.digest(backfill.canonical([]))}
            with patch.object(backfill, 'OUT', root):
                backfill.save('api_cache/' + identity + '.json', envelope)
                client = backfill.API(network=False)
                self.assertEqual(client.call(endpoint=request['endpoint'])[0], [])
                envelope['payload'] = [{'sha': 'tampered'}]
                backfill.save('api_cache/' + identity + '.json', envelope)
                with self.assertRaises(ValueError):
                    client.call(endpoint=request['endpoint'])

    def test_new_repository_outcome_can_use_prior_other_repository_profile(self):
        outcome = {'outcome_id': 'o1', 'developer_key': 'example', 'github_numeric_id': 42,
            'repository': 'o/new', 'profile_as_of': '2025-04-01T00:00:00Z',
            'outcome_at': '2025-05-01T00:00:00Z', 'issue_key': 'o/new#1'}
        profile = {'github_numeric_id': 42, 'as_of': outcome['profile_as_of'],
            'profile_key': 'p1', 'profile_version': 'v1', 'repository_scopes': ['o/prior']}
        controls = [{'repository': 'o/new', 'as_of': outcome['profile_as_of'], 'issue_key': 'o/new#2'}]
        joined, excluded = backfill.join_samples([outcome], controls, [profile])
        self.assertEqual(len(joined), 1)
        self.assertFalse(joined[0]['target_repository_in_profile_history'])
        self.assertEqual(excluded, [])
        profile['github_numeric_id'] = 99
        self.assertEqual(backfill.join_samples([outcome], controls, [profile])[0], [])

    def test_missing_history_repair_uses_only_other_seed_repo_and_is_idempotent(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp', prefix='adapter-repair-') as directory:
            root = Path(directory)
            source = root / 'source'
            (source / 'tables').mkdir(parents=True)
            (source / 'tables/repositories.jsonl').write_text(json.dumps({'repository_key': 'r2', 'full_name': 'o/prior'}) + '\n')
            (source / 'tables/contributions.jsonl').write_text(json.dumps({'developer_key': 'opaque-42',
                'repository_key': 'r2', 'author_association_status': 'verified'}) + '\n')
            content = b'print(1)\n'
            blob_sha = hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest()

            class FakeAPI:
                count = 0

                def call(self, *, endpoint):
                    self.count += 1
                    if '/commits?' in endpoint:
                        self_repo.assertTrue(endpoint.startswith('/repos/o/prior/'))
                        return [{'sha': 'a' * 40}], 'source.json'
                    if '/commits/' in endpoint:
                        return {'sha': 'a' * 40, 'author': {'id': 42},
                            'commit': {'author': {'date': '2024-03-01T00:00:00Z'}, 'committer': {'date': '2024-03-01T00:00:00Z'}},
                            'files': [{'filename': 'src/main.py', 'status': 'modified', 'sha': blob_sha}],
                            'html_url': 'https://github.com/o/prior/commit/' + 'a' * 40}, 'source.json'
                    return {'encoding': 'base64', 'size': len(content), 'content': base64.b64encode(content).decode()}, 'source.json'

            self_repo = self
            with patch.object(backfill, 'OUT', root / 'output'), patch.object(backfill, 'SOURCE', source), contextlib.redirect_stdout(io.StringIO()):
                backfill.lines('verified_outcomes.jsonl', [{'repository': 'o/new', 'profile_as_of': '2025-04-01T00:00:00Z',
                    'github_numeric_id': 42, 'developer_key': 'example',
                    'seed_identity': {'developer_key': 'opaque-42', 'identity_evidence': 'identity.json'}}])
                backfill.lines('historical_controls.jsonl', [{'repository': 'o/new', 'as_of': '2025-04-01T00:00:00Z'}])
                backfill.lines('historical_contributions.jsonl', [])
                backfill.lines('history_exclusions.jsonl', [])
                api = FakeAPI()
                backfill.history(api, {}, repair=True)
                first = (backfill.OUT / 'historical_contributions.jsonl').read_bytes()
                self.assertEqual(backfill.rows(backfill.OUT / 'historical_contributions.jsonl')[0]['repository'], 'o/prior')
                calls = api.count
                backfill.history(api, {}, repair=True)
                self.assertEqual(api.count, calls)
                self.assertEqual(first, (backfill.OUT / 'historical_contributions.jsonl').read_bytes())

    def test_search_over_1000_splits_dates_and_deduplicates_prefix(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp', prefix='adapter-complete-') as directory:
            root = Path(directory)
            first = {'url': 'https://github.com/o/r/pull/1', 'createdAt': '2025-01-01T00:00:00Z'}
            second = {'url': 'https://github.com/o/r/pull/2', 'createdAt': '2025-09-01T00:00:00Z'}

            class FakeAPI:
                calls = 0

                def call(self, *, query):
                    self.calls += 1
                    if 'created:2025-01-01..2025-12-31' in query:
                        count, nodes = 1001, [first]
                    elif 'created:2025-01-01..' in query:
                        count, nodes = 1, [first]
                    else:
                        count, nodes = 1, [second]
                    return {'data': {'q0': {'issueCount': count, 'nodes': nodes,
                        'pageInfo': {'hasNextPage': count > 1000, 'endCursor': 'cursor'}}}}, 'cache/response.json'

            with patch.object(backfill, 'OUT', root), contextlib.redirect_stdout(io.StringIO()):
                backfill.save('pagination_remaining_jobs.json', [{'repository': 'o/r', 'year': 2025,
                    'total_matches': 1001, 'continuation_cursor': 'third-page'}])
                backfill.save('search_coverage.json', [{'repository': 'o/r', 'year': 2025, 'evidence_ref': 'cache/prefix.json'}])
                backfill.save('pagination_search_coverage.json', [])
                backfill.save('cache/prefix.json', {'payload': {'data': {'q0': {'nodes': [first]}}}})
                api = FakeAPI()
                backfill.complete_searches(api, {})
                result = backfill.read(root / 'complete_search_coverage.json')[0]
                self.assertTrue(result['complete'])
                self.assertEqual(result['unique_indexed_pr_urls'], 2)
                self.assertEqual(len(result['leaf_ranges']), 2)
                self.assertEqual(api.calls, 3)

    def test_portable_evidence_paths_preserve_git_names_and_body_bytes(self):
        source = {'evidence_ref': 'api_cache\\a.json',
            'seed_identity': {'identity_evidence': 'datasets\\b\\source.json'},
            'evidence_refs': ['api_cache\\b.json'],
            'path': 'src\\literal.py', 'body': 'literal \\n content'}
        result = backfill.portable_evidence_paths(source)
        self.assertEqual(result['evidence_ref'], 'api_cache/a.json')
        self.assertEqual(result['seed_identity']['identity_evidence'], 'datasets/b/source.json')
        self.assertEqual(result['evidence_refs'], ['api_cache/b.json'])
        self.assertEqual(result['path'], source['path'])
        self.assertEqual(result['body'], source['body'])
        self.assertEqual(backfill.portable_evidence_paths(result), result)


if __name__ == '__main__':
    unittest.main()
