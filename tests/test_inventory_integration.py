from datetime import datetime, timedelta, timezone
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

from oss_mentor.candidate_freshness import verification_is_current
from oss_mentor.candidate_refresh import CandidateRefresher
from oss_mentor.candidate_refresh_worker import CandidateRefreshWorker
from oss_mentor.candidate_sync import CandidateSynchronizer
from oss_mentor.candidate_rules import evaluate_availability
from oss_mentor.contracts import DeveloperProfileV2
from oss_mentor.developer_profiles import ALLOWED_LANGUAGES, LANGUAGE_DISPLAY_NAMES, custom_profile_for_matching
from oss_mentor.inventory_import import import_inventory, import_prepared_inventory
from oss_mentor.services.recommendation_service import RecommendationService
from oss_mentor.storage.candidates import SQLiteCandidateStorage

ROOT = Path(__file__).resolve().parents[1]


class InventoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.now = datetime.now(timezone.utc)
        self.store = SQLiteCandidateStorage(self.directory / 'platform.sqlite3', ROOT / 'db/sqlite/001_mvp.sql')
        self.snapshot = self.directory / 'inventory.sqlite3'
        self.raw = dict(repository='example/demo', issue_number=7, github_issue_id='123',
                        title='Fix crash when parsing an empty file', body='Steps to reproduce: parse an empty file. Expected behavior: return an empty list.',
                        language='Python', current_labels=['bug'], state='open', assignees=[], locked=False,
                        timeline_complete=True, open_pr_references=[], claim_signals=[])
        with closing(sqlite3.connect(self.snapshot)) as c, c:
            c.execute('CREATE TABLE issue_inventory(issue_key TEXT, github_issue_id TEXT, repository TEXT, issue_number INTEGER, review_status TEXT, observed_at TEXT, expires_at TEXT, source TEXT, record_json TEXT)')
        self.write_snapshot()

    def write_snapshot(self, *, verified=None, expiry=None, status='availability_passed', raw=None):
        verified = verified or self.now - timedelta(hours=2)
        expiry = expiry or verified + timedelta(hours=24)
        with closing(sqlite3.connect(self.snapshot)) as c, c:
            c.execute('DELETE FROM issue_inventory')
            c.execute('INSERT INTO issue_inventory VALUES(?,?,?,?,?,?,?,?,?)',
                      ('example/demo#7', '123', 'example/demo', 7, status, verified.isoformat(), expiry.isoformat(), 'retained_audit.json', json.dumps(raw or self.raw)))

    def test_import_produces_serving_features_requirements_and_original_lease(self):
        result = import_inventory(self.snapshot, self.store)
        self.assertEqual(1, result['counts']['imported'])
        tasks = self.store.matchable_candidates()
        self.assertEqual(1, len(tasks))
        task = tasks[0]
        self.assertIn('bug_fix', task['task_types'])
        self.assertTrue(task['requirements'])
        self.assertIsInstance(task['estimated_code_difficulty'], int)
        self.assertIsInstance(task['estimated_setup_difficulty'], int)
        self.assertEqual((self.now + timedelta(hours=22)).isoformat(), task['verification_expires_at'])
        self.assertIn('difficulty_assessment', json.loads(task['feature_evidence_json']))
        profile = DeveloperProfileV2(profile_key='growth', display_name='growth', service_track='growth',
                                    preferred_languages=('Python',), operating_systems=('linux',), preferred_task_types=('bug_fix',),
                                    max_code_difficulty=3, max_setup_difficulty=3, desired_skill_stretch=1,
                                    skills={'Python': 3, 'debugging': 3})
        batch = RecommendationService(self.store).recommend_batch(profile=profile)
        self.assertEqual(1, len(batch.items))

    def test_repeat_import_is_idempotent_and_does_not_renew_evidence(self):
        import_inventory(self.snapshot, self.store)
        before = self.store.matchable_candidates()[0]['github_verified_at']
        again = import_inventory(self.snapshot, self.store)
        self.assertEqual(1, again['counts']['skipped_equal_or_newer'])
        self.assertEqual(before, self.store.matchable_candidates()[0]['github_verified_at'])
        with self.store.connect() as c:
            self.assertEqual(1, c.execute('SELECT count(*) FROM task_candidate').fetchone()[0])

    def test_expired_snapshot_is_stored_but_never_recommended(self):
        self.write_snapshot(verified=self.now - timedelta(days=2))
        import_inventory(self.snapshot, self.store)
        self.assertEqual([], self.store.matchable_candidates())
        self.assertEqual([], self.store.rank_candidates(track='growth'))

    def test_shorter_original_expiry_blocks_a_recent_observation(self):
        self.write_snapshot(expiry=self.now - timedelta(minutes=1))
        import_inventory(self.snapshot, self.store)
        self.assertEqual([], self.store.matchable_candidates())
        selected = self.store.stale_candidates(repositories=['example/demo'], older_than_hours=24, limit=10)
        self.assertEqual(1, len(selected))

    def test_newer_closed_state_cannot_be_resurrected_by_older_import(self):
        import_inventory(self.snapshot, self.store)
        with self.store.connect() as c:
            c.execute("UPDATE task_candidate SET state='closed', candidate_availability='closed', github_verified_at=?", (self.now.isoformat(),))
        self.assertEqual(1, import_inventory(self.snapshot, self.store)['counts']['skipped_equal_or_newer'])
        self.assertEqual([], self.store.matchable_candidates())

    def test_historical_inventory_cannot_be_approved_by_a_recent_timestamp(self):
        self.write_snapshot(status='historical_requires_refresh')
        import_inventory(self.snapshot, self.store)
        self.assertEqual([], self.store.matchable_candidates())

    def test_identity_conflict_rolls_back(self):
        import_inventory(self.snapshot, self.store)
        with self.store.connect() as c:
            c.execute('UPDATE task_candidate SET github_issue_id=999')
        with self.assertRaisesRegex(ValueError, 'identity conflict'):
            import_inventory(self.snapshot, self.store)
        with self.store.connect() as c:
            self.assertEqual(999, c.execute('SELECT github_issue_id FROM task_candidate').fetchone()[0])

    def test_successful_refresh_recomputes_features_and_requirements(self):
        self.write_snapshot(verified=self.now - timedelta(days=2))
        import_inventory(self.snapshot, self.store)
        current = dict(github_issue_id=123, issue_number=7, html_url='https://github.com/example/demo/issues/7',
                       created_at='2026-01-01T00:00:00Z', title='Document configuration options in README',
                       body_text='Update `README.md` documentation and examples for configuration. Acceptance criteria: show the default value for every option.', labels=['documentation'],
                       state='open', assignment_state='unassigned', is_locked=False, has_linked_open_pr=False,
                       source_system='github_rest', source_fetched_at=self.now.isoformat(), github_verified_at=self.now.isoformat())
        sync = SimpleNamespace(fetch_repository_health=lambda _: dict(github_repository_id=1, html_url='https://github.com/example/demo',
            github_verified_at=self.now.isoformat(), is_archived=False, is_disabled=False, pushed_at=self.now.isoformat(), maintenance_status='active'),
            fetch_current_issue=lambda *_: current)
        result = CandidateRefresher(sync, self.store).refresh([SimpleNamespace(full_name='example/demo', ecosystem=None, primary_language='Python')])
        self.assertEqual(1, result.refreshed_count)
        task = self.store.matchable_candidates()[0]
        self.assertIn('documentation', task['task_types'])
        self.assertNotIn('bug_fix', task['task_types'])
        self.assertEqual((self.now + timedelta(hours=24)).isoformat(), task['verification_expires_at'])

    def test_refresh_error_never_renews_expired_evidence(self):
        self.write_snapshot(verified=self.now - timedelta(days=2))
        import_inventory(self.snapshot, self.store)
        def fail(*_):
            raise OSError('network unavailable')
        sync = SimpleNamespace(fetch_repository_health=fail)
        result = CandidateRefresher(sync, self.store).refresh([SimpleNamespace(full_name='example/demo')])
        self.assertEqual(1, result.failed_count)
        self.assertEqual([], self.store.matchable_candidates())

    def test_detail_rejects_expiry_even_with_a_permissive_store(self):
        import_inventory(self.snapshot, self.store)
        task = self.store.matchable_candidates()[0]
        task['verification_expires_at'] = (self.now - timedelta(seconds=1)).isoformat()
        service = RecommendationService(SimpleNamespace(matchable_candidates=lambda: [task]))
        profile = DeveloperProfileV2(profile_key='test', display_name='test', service_track='growth',
                                    preferred_languages=('Python',), operating_systems=('linux',), preferred_task_types=('bug_fix',),
                                    max_code_difficulty=3, max_setup_difficulty=3, desired_skill_stretch=1, skills={'Python': 3})
        self.assertEqual((), service.recommend(profile=profile).items)
        with self.assertRaises(KeyError):
            service.recommendation_detail(profile=profile, task_candidate_id=task['task_candidate_id'])

    def test_invalid_future_missing_and_exact_expiry_fail_closed(self):
        for record in ({}, {'github_verified_at': 'invalid'}, {'github_verified_at': (self.now + timedelta(minutes=1)).isoformat()},
                       {'github_verified_at': self.now.isoformat(), 'verification_expires_at': self.now.isoformat()}):
            self.assertFalse(verification_is_current(record, now=self.now))

    def test_unchecked_pr_state_never_becomes_available(self):
        record = dict(source_system='github_rest',github_issue_id=123,github_verified_at=self.now.isoformat(),
                      state='open',assignment_state='unassigned',is_locked=False,has_linked_open_pr=None)
        self.assertEqual('temporarily_unverified',evaluate_availability(record,now=self.now).availability)

    def test_prepared_import_remaps_ids_preserves_other_rows_and_is_idempotent(self):
        import_inventory(self.snapshot, self.store)
        target = SQLiteCandidateStorage(self.directory / 'target.sqlite3', ROOT / 'db/sqlite/001_mvp.sql')
        target.initialize()
        with target.connect() as c:
            repo_id = target.upsert_repository(c, full_name='other/repo', github_repository_id=99,
                html_url='https://github.com/other/repo', ecosystems_last_synced_at=None)
            record = dict(self.store.matchable_candidates()[0])
            columns = [k for k in record if k not in {'repository','primary_language','maintenance_status','pushed_at','has_contributing_guide','labels','task_types','requirements'}]
            record.update(repository_id=repo_id, github_issue_id=999, title='Preserve existing task')
            c.execute(f'INSERT INTO task_candidate({",".join(columns)}) VALUES({",".join("?" for _ in columns)})', [record[k] for k in columns])
        result = import_prepared_inventory(self.snapshot, self.store.database_path, target)
        self.assertEqual(1, result['counts']['imported'])
        with target.connect() as c:
            task = c.execute('SELECT * FROM task_candidate WHERE github_issue_id=123').fetchone()
            self.assertNotEqual(1, task['task_candidate_id'])
            self.assertTrue(c.execute('SELECT 1 FROM task_skill_requirement WHERE task_candidate_id=?', (task['task_candidate_id'],)).fetchone())
            self.assertEqual('Preserve existing task', c.execute('SELECT title FROM task_candidate WHERE github_issue_id=999').fetchone()[0])
        self.assertEqual(1, import_prepared_inventory(self.snapshot, self.store.database_path, target)['counts']['skipped_equal_or_newer'])

    def test_prepared_import_rejects_mismatched_snapshot(self):
        import_inventory(self.snapshot, self.store)
        self.write_snapshot(status='historical_requires_refresh')
        target = SQLiteCandidateStorage(self.directory / 'target.sqlite3', ROOT / 'db/sqlite/001_mvp.sql')
        with self.assertRaisesRegex(ValueError, 'does not match'):
            import_prepared_inventory(self.snapshot, self.store.database_path, target)

    def test_prepared_import_can_fill_a_missing_global_id_for_the_same_issue(self):
        import_inventory(self.snapshot, self.store)
        target = SQLiteCandidateStorage(self.directory / 'target.sqlite3', ROOT / 'db/sqlite/001_mvp.sql')
        import_prepared_inventory(self.snapshot, self.store.database_path, target)
        with target.connect() as c:
            original_id = c.execute('SELECT task_candidate_id FROM task_candidate').fetchone()[0]
            c.execute('UPDATE task_candidate SET github_issue_id=NULL,github_verified_at=NULL')
        import_prepared_inventory(self.snapshot, self.store.database_path, target)
        with target.connect() as c:
            row = c.execute('SELECT task_candidate_id,github_issue_id FROM task_candidate').fetchone()
            self.assertEqual((original_id,123),tuple(row))

    def test_background_refresh_checks_a_lease_before_it_expires(self):
        self.write_snapshot(verified=self.now - timedelta(hours=23, minutes=30))
        import_inventory(self.snapshot, self.store)
        current = dict(github_issue_id=123, issue_number=7, html_url='https://github.com/example/demo/issues/7',
            created_at='2026-01-01T00:00:00Z', title=self.raw['title'], body_text=self.raw['body'], labels=['bug'],
            state='closed', assignment_state='unassigned', is_locked=False, has_linked_open_pr=False,
            source_system='github_rest', source_fetched_at=self.now.isoformat(), github_verified_at=self.now.isoformat())
        sync = SimpleNamespace(fetch_repository_health=lambda _: dict(github_repository_id=1,
            html_url='https://github.com/example/demo', github_verified_at=self.now.isoformat(),
            is_archived=False, is_disabled=False, pushed_at=self.now.isoformat(), maintenance_status='active'),
            fetch_current_issue=lambda *_: current)
        worker = CandidateRefreshWorker(self.store, lambda: sync, limit=1)
        state = worker.run_once()
        self.assertEqual(1, state['selected_count'])
        self.assertEqual(1, state['refreshed_count'])
        self.assertEqual('completed', worker.status()['status'])
        self.assertEqual([], self.store.matchable_candidates())

    def test_background_refresh_keeps_historical_backlog_held(self):
        self.write_snapshot(status='historical_requires_refresh', verified=self.now - timedelta(days=2))
        import_inventory(self.snapshot, self.store)
        worker = CandidateRefreshWorker(self.store, lambda: SimpleNamespace())
        self.assertEqual(0, worker.run_once()['selected_count'])
        self.assertEqual([], self.store.matchable_candidates())

    def test_help_wanted_is_not_a_novice_track_approval(self):
        raw = {**self.raw, 'current_labels': ['help wanted']}
        self.write_snapshot(raw=raw)
        import_inventory(self.snapshot, self.store)
        self.assertFalse(self.store.matchable_candidates()[0]['newcomer_label_signal'])

    def test_all_inventory_languages_can_be_used_in_a_platform_profile(self):
        payload = dict(display_name='Language coverage', service_track='growth',
            preferred_languages=[LANGUAGE_DISPLAY_NAMES[x] for x in sorted(ALLOWED_LANGUAGES)],
            operating_systems=['linux'], preferred_task_types=['bug_fix'], max_code_difficulty=3,
            max_setup_difficulty=3, desired_skill_stretch=1, skills={x:3 for x in ALLOWED_LANGUAGES})
        result = custom_profile_for_matching(payload)
        self.assertEqual(9, len(result['preferred_languages']))
        self.assertIn('C++', result['preferred_languages'])

    def test_unclear_scope_is_retained_for_review(self):
        raw = {**self.raw, 'title': 'Question about configuration', 'body': 'Can someone explain this behavior?', 'current_labels': []}
        self.write_snapshot(raw=raw)
        import_inventory(self.snapshot, self.store)
        self.assertEqual([], self.store.matchable_candidates())
        with self.store.connect() as c:
            self.assertTrue(c.execute('SELECT task_feature_version FROM task_candidate').fetchone()[0])

    def test_refresh_defers_comment_claims_and_rechecks_closed_pr_references(self):
        detail = dict(id=123, number=7, repository_url='https://api.github.com/repos/example/demo',
                      html_url='https://github.com/example/demo/issues/7', created_at='2026-01-01T00:00:00Z',
                      title='Fix parser', body='Fix parsing behavior.', state='open', assignees=[], locked=False, labels=[])
        events = [dict(event='commented', body='I am working on this.'),
                  dict(event='cross-referenced', source={'issue': {'state': 'closed', 'pull_request': {'url': 'https://api.github.com/repos/example/demo/pulls/10'}}})]
        class GitHub:
            def get(inner, path, **_):
                return SimpleNamespace(payload=({'state': 'open'} if '/pulls/' in path else detail), fetched_at=self.now)
            def iter_pages(inner, path, **_):
                return iter([SimpleNamespace(payload=events)])
        sync = CandidateSynchronizer(SimpleNamespace(), GitHub(), SimpleNamespace())
        record = sync.fetch_current_issue('example/demo', 7)
        self.assertTrue(record['has_linked_open_pr'])
        self.assertIn('claim_or_implementation_text_requires_review', record['context_review_reasons'])


if __name__ == '__main__':
    unittest.main()
