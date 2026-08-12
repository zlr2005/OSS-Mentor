from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from oss_mentor.candidate_rules import (
    CANDIDATE_AVAILABILITY_VALUES,
    evaluate_availability,
    evaluate_candidate,
)
from oss_mentor.candidate_sync import (
    CandidateService,
    CandidateSyncResult,
    CandidateSynchronizer,
)
from oss_mentor.candidate_report import build_candidate_report
from oss_mentor.storage.candidates import SQLiteCandidateStorage
from oss_mentor.storage.candidates import SyncAlreadyRunningError
from oss_mentor.sqlite_store import SQLiteCandidateStore


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db" / "sqlite" / "001_mvp.sql"


def issue_record(**overrides):
    now = datetime.now(timezone.utc).isoformat()
    value = {
        "issue_number": 7,
        "github_issue_id": 123,
        "html_url": "https://github.com/example/demo/issues/7",
        "created_at": "2026-07-01T00:00:00+00:00",
        "author_association": "NONE",
        "title": "Example candidate",
        "body_text": "Clear implementation details",
        "labels": ["good first issue"],
        "state": "open",
        "assignment_state": "unassigned",
        "is_locked": False,
        "has_linked_open_pr": False,
        "comment_count": 0,
        "last_activity_at": now,
        "source_system": "github_rest",
        "source_fetched_at": now,
        "github_verified_at": now,
        "is_pull_request": False,
    }
    value.update(overrides)
    return value


class CandidateAvailabilityTests(unittest.TestCase):
    def test_all_fixed_availability_states_are_reachable(self) -> None:
        now = datetime(2026, 7, 29, 12, 30, tzinfo=timezone.utc)
        verified = now.isoformat()
        cases = [
            (issue_record(github_verified_at=verified), {}, "available"),
            (issue_record(state="closed", github_verified_at=verified), {}, "closed"),
            (
                issue_record(
                    assignment_state="assigned",
                    github_verified_at=verified,
                ),
                {},
                "assigned",
            ),
            (
                issue_record(
                    has_linked_open_pr=True,
                    github_verified_at=verified,
                ),
                {},
                "linked_open_pr",
            ),
            (
                issue_record(is_locked=True, github_verified_at=verified),
                {},
                "locked",
            ),
            (
                issue_record(github_verified_at=verified),
                {"is_archived": 1},
                "repository_inactive",
            ),
            (
                issue_record(
                    github_verified_at=(now - timedelta(hours=25)).isoformat()
                ),
                {},
                "temporarily_unverified",
            ),
        ]
        reached = set()
        for record, repository, expected in cases:
            with self.subTest(expected=expected):
                actual = evaluate_availability(
                    record,
                    repository=repository,
                    now=now,
                )
                self.assertEqual(expected, actual.availability)
                reached.add(actual.availability)
        self.assertEqual(CANDIDATE_AVAILABILITY_VALUES, reached)


class CandidateStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = SQLiteCandidateStorage(
            Path(self.temporary.name) / "candidate.sqlite3",
            MIGRATION,
        )
        self.store.initialize()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def insert_candidate(self, record=None) -> int:
        value = record or issue_record()
        with self.store.connect() as connection:
            repository_id = self.store.upsert_repository(
                connection,
                full_name="example/demo",
                github_repository_id=1,
                html_url="https://github.com/example/demo",
                ecosystems_last_synced_at=None,
                ecosystem="pypi",
                primary_language="Python",
            )
            self.store.upsert_candidate(
                connection,
                repository_id=repository_id,
                record=value,
                eligibility=evaluate_candidate(value),
            )
            return int(
                connection.execute(
                    """
                    SELECT task_candidate_id
                    FROM task_candidate
                    WHERE repository_id = ? AND issue_number = ?
                    """,
                    (repository_id, value["issue_number"]),
                ).fetchone()[0]
            )

    def test_008_migration_contains_sync_tables_and_candidate_columns(self) -> None:
        with self.store.connect() as connection:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            candidate_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(task_candidate)")
            }
            repository_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(repository)")
            }
            migrations = {
                str(row[0])
                for row in connection.execute(
                    "SELECT migration_name FROM schema_migration"
                )
            }
        self.assertIn("sync_run", tables)
        self.assertIn("sync_repository_result", tables)
        self.assertIn("candidate_availability", candidate_columns)
        self.assertIn("availability_reasons_json", candidate_columns)
        self.assertIn("candidate_sync_cursor", repository_columns)
        self.assertIn("candidate_sync_etag", repository_columns)
        self.assertIn("008_sync_runs.sql", migrations)

    def test_008_migrates_a_v04_database_without_losing_candidates(self) -> None:
        old_migrations = Path(self.temporary.name) / "v04-migrations"
        old_migrations.mkdir()
        for number in range(1, 7):
            source = next((ROOT / "db" / "sqlite").glob(f"{number:03d}_*.sql"))
            shutil.copy2(source, old_migrations / source.name)
        database = Path(self.temporary.name) / "upgrade.sqlite3"
        old_store = SQLiteCandidateStore(
            database,
            old_migrations / "001_mvp.sql",
        )
        old_store.initialize()
        value = issue_record()
        with old_store.connect() as connection:
            repository_id = old_store.upsert_repository(
                connection,
                full_name="example/upgrade",
                github_repository_id=9,
                html_url="https://github.com/example/upgrade",
                ecosystems_last_synced_at=None,
            )
            old_store.upsert_candidate(
                connection,
                repository_id=repository_id,
                record=value,
                eligibility=evaluate_candidate(value),
            )

        upgraded = SQLiteCandidateStorage(database, MIGRATION)
        upgraded.initialize()
        with upgraded.connect() as connection:
            count = int(
                connection.execute("SELECT COUNT(*) FROM task_candidate").fetchone()[0]
            )
            availability = str(
                connection.execute(
                    "SELECT candidate_availability FROM task_candidate"
                ).fetchone()[0]
            )
        self.assertEqual(1, count)
        self.assertEqual("available", availability)

    def test_stale_candidate_is_removed_from_recommendation_input(self) -> None:
        old = datetime.now(timezone.utc) - timedelta(hours=25)
        candidate_id = self.insert_candidate(
            issue_record(
                source_fetched_at=old.isoformat(),
                github_verified_at=old.isoformat(),
            )
        )
        self.assertEqual([], self.store.available_candidates())
        detail = self.store.candidate_detail_row(candidate_id)
        self.assertEqual("temporarily_unverified", detail["candidate_availability"])
        self.assertIn(
            "verification_older_than_limit",
            detail["availability_reasons"],
        )

    def test_github_unavailable_is_consistently_closed(self) -> None:
        value = issue_record()
        with self.store.connect() as connection:
            repository_id = self.store.upsert_repository(
                connection,
                full_name="example/demo",
                github_repository_id=1,
                html_url="https://github.com/example/demo",
                ecosystems_last_synced_at=None,
            )
            self.store.upsert_candidate(
                connection,
                repository_id=repository_id,
                record=value,
                eligibility=SimpleNamespace(
                    eligibility="excluded",
                    reasons=("github_unavailable",),
                    warnings=(),
                    newcomer_label_signal=False,
                    feature_definition_version="test",
                ),
            )
        detail = self.store.candidate_detail_row(1)
        self.assertEqual("closed", detail["candidate_availability"])
        self.assertEqual(["github_unavailable"], detail["availability_reasons"])

    def test_blocking_label_is_available_but_not_recommendable(self) -> None:
        value = issue_record(labels=["status: blocked"])
        candidate_id = self.insert_candidate(value)
        detail = self.store.candidate_detail_row(candidate_id)
        self.assertEqual("available", detail["candidate_availability"])
        self.assertEqual("temporarily_ineligible", detail["candidate_eligibility"])
        self.assertEqual([], self.store.available_candidates())

    def test_candidate_pool_status_contains_required_distributions(self) -> None:
        self.insert_candidate()
        status = self.store.candidate_pool_status()
        self.assertEqual(1, status["candidate_total"])
        self.assertEqual(1, status["recommendable_count"])
        self.assertEqual(1, status["newcomer_count"])
        self.assertEqual({"Python": 1}, status["by_language"])
        self.assertEqual({"example/demo": 1}, status["by_repository"])

    def test_candidate_pool_status_reports_latest_failures_and_request_cost(self) -> None:
        run_id = self.store.create_sync_run(
            requested_by="scheduler",
            limit_per_repository=20,
            repository_count=2,
        )
        self.store.record_repository_result(
            sync_run_id=run_id,
            repository_full_name="example/ok",
            status="succeeded",
            started_at="2026-07-29T12:00:00Z",
            request_count=4,
            success_count=1,
        )
        self.store.record_repository_result(
            sync_run_id=run_id,
            repository_full_name="example/fail",
            status="failed",
            started_at="2026-07-29T12:00:00Z",
            request_count=3,
            failure_count=1,
            error_code="github_http_502",
            error_summary="GitHub request failed with HTTP 502",
        )
        self.store.complete_sync_run(
            sync_run_id=run_id,
            status="partially_succeeded",
            request_count=7,
            success_count=1,
            failure_count=1,
            skipped_count=0,
            rate_limit_remaining=4000,
            rate_limit_reset_at="2026-07-29T13:00:00Z",
            retry_count=2,
        )
        refresh_id = self.store.create_sync_run(
            requested_by="scheduler",
            limit_per_repository=100,
            repository_count=2,
            run_type="candidate_refresh",
        )
        self.store.complete_sync_run(
            sync_run_id=refresh_id,
            status="succeeded",
            request_count=2,
            success_count=2,
            failure_count=0,
            skipped_count=0,
            rate_limit_remaining=3998,
            rate_limit_reset_at="2026-07-29T13:00:00Z",
            retry_count=0,
        )
        status = self.store.candidate_pool_status()
        self.assertEqual(["example/fail"], status["failed_repositories"])
        self.assertEqual(3.5, status["average_github_requests_per_repository"])
        self.assertEqual("partially_succeeded", status["latest_sync"]["status"])
        self.assertEqual("succeeded", status["latest_refresh"]["status"])
        self.assertEqual("repository_sync", status["latest_sync"]["run_type"])

    def test_candidate_report_uses_v05_schema_with_v05_storage(self) -> None:
        self.insert_candidate()
        report = build_candidate_report(
            self.store,
            now=datetime.now(timezone.utc),
        )
        self.assertEqual("candidate_pool_report_v0.5", report["schema_version"])
        self.assertEqual(1, report["candidate_status_v0.5"]["recommendable_count"])

    def test_persisted_error_summary_is_redacted(self) -> None:
        run_id = self.store.create_sync_run(
            requested_by="test",
            limit_per_repository=20,
            repository_count=1,
        )
        self.store.record_repository_result(
            sync_run_id=run_id,
            repository_full_name="example/demo",
            status="failed",
            started_at="2026-07-29T12:00:00Z",
            failure_count=1,
            error_code="network_error",
            error_summary="Authorization: Bearer super-secret-token",
        )
        run = self.store.sync_run(run_id)
        serialized = json.dumps(run)
        self.assertNotIn("super-secret-token", serialized)
        self.assertIn("[redacted]", serialized)

    def test_only_one_sync_run_can_be_running(self) -> None:
        first = self.store.create_sync_run(
            requested_by="first",
            limit_per_repository=20,
            repository_count=1,
        )
        with self.assertRaises(SyncAlreadyRunningError):
            self.store.create_sync_run(
                requested_by="second",
                limit_per_repository=20,
                repository_count=1,
            )
        self.store.complete_sync_run(
            sync_run_id=first,
            status="succeeded",
            request_count=0,
            success_count=1,
            failure_count=0,
            skipped_count=0,
            rate_limit_remaining=None,
            rate_limit_reset_at=None,
            retry_count=0,
        )

    def test_abandoned_run_is_failed_before_a_new_run_starts(self) -> None:
        old = "2026-07-28T00:00:00+00:00"
        abandoned = self.store.create_sync_run(
            requested_by="old-worker",
            limit_per_repository=20,
            repository_count=1,
            started_at=old,
        )
        current = self.store.create_sync_run(
            requested_by="new-worker",
            limit_per_repository=20,
            repository_count=1,
        )
        self.assertNotEqual(abandoned, current)
        self.assertEqual("failed", self.store.sync_run(abandoned)["status"])
        self.assertEqual(
            "abandoned_run",
            self.store.sync_run(abandoned)["error_code"],
        )


class IncrementalSyncTests(unittest.TestCase):
    def test_cursor_query_does_not_reuse_etag_from_a_different_url(self) -> None:
        fetched_at = datetime(2026, 7, 29, 12, 30, tzinfo=timezone.utc)

        class GitHub:
            request_count = 0
            retry_count = 0
            rate_limit_remaining = 4999
            rate_limit_reset_at = fetched_at + timedelta(hours=1)

            def __init__(self):
                self.issue_request = None

            def get(self, path, *, params=None, extra_headers=None):
                self.request_count += 1
                if path == "/repos/example/demo":
                    return SimpleNamespace(
                        payload={
                            "id": 1,
                            "html_url": "https://github.com/example/demo",
                            "archived": False,
                            "disabled": False,
                            "pushed_at": "2026-07-29T12:00:00Z",
                            "updated_at": "2026-07-29T12:00:00Z",
                        },
                        fetched_at=fetched_at,
                    )
                self.issue_request = {
                    "params": params,
                    "headers": extra_headers,
                }
                return SimpleNamespace(
                    payload=[],
                    status_code=200,
                    headers={"etag": "\"fixture-etag\""},
                    fetched_at=fetched_at,
                )

        class Ecosystems:
            def get_repository(self, full_name):
                return SimpleNamespace(payload={}, fetched_at=fetched_at)

            def get_issues(self, full_name, **kwargs):
                return SimpleNamespace(payload=[], fetched_at=fetched_at)

        with tempfile.TemporaryDirectory() as temporary:
            store = SQLiteCandidateStorage(
                Path(temporary) / "incremental.sqlite3",
                MIGRATION,
            )
            store.initialize()
            with store.connect() as connection:
                repository_id = store.upsert_repository(
                    connection,
                    full_name="example/demo",
                    github_repository_id=1,
                    html_url="https://github.com/example/demo",
                    ecosystems_last_synced_at=None,
                )
                store.save_sync_checkpoint(
                    connection,
                    repository_id=repository_id,
                    cursor="2026-07-29T10:00:00Z",
                    etag="\"fixture-etag\"",
                    last_modified=None,
                )
            github = GitHub()
            result = CandidateSynchronizer(Ecosystems(), github, store).sync(
                "example/demo"
            )

        self.assertFalse(result.not_modified)
        self.assertEqual(
            "2026-07-29T10:00:00Z",
            github.issue_request["params"]["since"],
        )
        self.assertIsNone(github.issue_request["headers"])

    def test_stable_query_uses_etag_and_304_skips_fallback(self) -> None:
        fetched_at = datetime(2026, 7, 29, 12, 30, tzinfo=timezone.utc)

        class GitHub:
            request_count = 0
            retry_count = 0
            rate_limit_remaining = 4999
            rate_limit_reset_at = None

            def __init__(self):
                self.issue_request = None

            def get(self, path, *, params=None, extra_headers=None):
                self.request_count += 1
                if path == "/repos/example/demo":
                    return SimpleNamespace(
                        payload={
                            "id": 1,
                            "html_url": "https://github.com/example/demo",
                            "archived": False,
                            "disabled": False,
                            "pushed_at": "2026-07-29T12:00:00Z",
                        },
                        fetched_at=fetched_at,
                    )
                self.issue_request = {
                    "params": params,
                    "headers": extra_headers,
                }
                return SimpleNamespace(
                    payload=None,
                    status_code=304,
                    headers={"etag": "\"fixture-etag\""},
                    fetched_at=fetched_at,
                )

        class Ecosystems:
            def get_repository(self, full_name):
                raise AssertionError("304 must not fetch the fallback source")

            def get_issues(self, full_name, **kwargs):
                raise AssertionError("304 must not fetch the fallback source")

        with tempfile.TemporaryDirectory() as temporary:
            store = SQLiteCandidateStorage(
                Path(temporary) / "etag.sqlite3",
                MIGRATION,
            )
            store.initialize()
            with store.connect() as connection:
                repository_id = store.upsert_repository(
                    connection,
                    full_name="example/demo",
                    github_repository_id=1,
                    html_url="https://github.com/example/demo",
                    ecosystems_last_synced_at=None,
                )
                store.save_sync_checkpoint(
                    connection,
                    repository_id=repository_id,
                    cursor=None,
                    etag="\"fixture-etag\"",
                    last_modified=None,
                )
            github = GitHub()
            result = CandidateSynchronizer(Ecosystems(), github, store).sync(
                "example/demo"
            )
        self.assertTrue(result.not_modified)
        self.assertNotIn("since", github.issue_request["params"])
        self.assertEqual(
            "\"fixture-etag\"",
            github.issue_request["headers"]["If-None-Match"],
        )

    def test_incremental_pagination_advances_only_past_fully_processed_ties(
        self,
    ) -> None:
        fetched_at = datetime(2026, 7, 29, 12, 30, tzinfo=timezone.utc)

        def raw_issue(number: int, updated_at: str):
            return {
                "number": number,
                "id": 1000 + number,
                "repository_url": "https://api.github.com/repos/example/demo",
                "html_url": f"https://github.com/example/demo/issues/{number}",
                "created_at": "2026-07-01T00:00:00Z",
                "updated_at": updated_at,
                "author_association": "NONE",
                "title": f"Issue {number}",
                "body": "Details",
                "labels": [{"name": "good first issue"}],
                "state": "open",
                "assignees": [],
                "locked": False,
                "comments": 0,
            }

        issues = {
            1: raw_issue(1, "2026-07-29T10:01:00Z"),
            2: raw_issue(2, "2026-07-29T10:02:00Z"),
            3: raw_issue(3, "2026-07-29T10:02:00Z"),
            4: raw_issue(4, "2026-07-29T10:03:00Z"),
        }

        class GitHub:
            request_count = 0
            retry_count = 0
            rate_limit_remaining = 4800
            rate_limit_reset_at = None

            def get(self, path, *, params=None, extra_headers=None):
                self.request_count += 1
                if path == "/repos/example/demo":
                    return SimpleNamespace(
                        payload={
                            "id": 1,
                            "html_url": "https://github.com/example/demo",
                            "archived": False,
                            "disabled": False,
                            "pushed_at": "2026-07-29T12:00:00Z",
                        },
                        fetched_at=fetched_at,
                    )
                number = int(path.rsplit("/", maxsplit=1)[1])
                return SimpleNamespace(payload=issues[number], fetched_at=fetched_at)

            def iter_pages(self, path, *, params=None, extra_headers=None):
                self.request_count += 1
                if path.endswith("/timeline"):
                    yield SimpleNamespace(
                        payload=[],
                        headers={},
                        fetched_at=fetched_at,
                    )
                    return
                if params["since"] == "2026-07-29T10:00:00Z":
                    yield SimpleNamespace(
                        payload=[issues[1], issues[2]],
                        headers={},
                        fetched_at=fetched_at,
                    )
                    yield SimpleNamespace(
                        payload=[issues[3], issues[4]],
                        headers={},
                        fetched_at=fetched_at,
                    )
                else:
                    yield SimpleNamespace(
                        payload=[issues[4]],
                        headers={},
                        fetched_at=fetched_at,
                    )

        class Ecosystems:
            def get_repository(self, full_name):
                return SimpleNamespace(payload={}, fetched_at=fetched_at)

            def get_issues(self, full_name, **kwargs):
                return SimpleNamespace(payload=[], fetched_at=fetched_at)

        with tempfile.TemporaryDirectory() as temporary:
            store = SQLiteCandidateStorage(
                Path(temporary) / "pagination.sqlite3",
                MIGRATION,
            )
            store.initialize()
            with store.connect() as connection:
                repository_id = store.upsert_repository(
                    connection,
                    full_name="example/demo",
                    github_repository_id=1,
                    html_url="https://github.com/example/demo",
                    ecosystems_last_synced_at=None,
                )
                store.save_sync_checkpoint(
                    connection,
                    repository_id=repository_id,
                    cursor="2026-07-29T10:00:00Z",
                    etag=None,
                    last_modified=None,
                )
            synchronizer = CandidateSynchronizer(Ecosystems(), GitHub(), store)
            first = synchronizer.sync("example/demo", limit=2)
            first_numbers = {
                item["issue_number"] for item in store.list_candidates(limit=10)
            }
            second = synchronizer.sync("example/demo", limit=2)
            final_numbers = {
                item["issue_number"] for item in store.list_candidates(limit=10)
            }

        self.assertEqual("2026-07-29T10:02:00Z", first.sync_cursor)
        self.assertEqual({1, 2, 3}, first_numbers)
        self.assertEqual("2026-07-29T10:03:00Z", second.sync_cursor)
        self.assertEqual({1, 2, 3, 4}, final_numbers)


class CandidateServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = SQLiteCandidateStorage(
            Path(self.temporary.name) / "service.sqlite3",
            MIGRATION,
        )
        self.store.initialize()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_single_repository_failure_is_isolated_and_batch_is_partial(self) -> None:
        class Synchronizer:
            def __init__(self):
                self.request_count = 0
                self.retry_count = 0

            def _client_metrics(self):
                return (self.request_count, self.retry_count, 4000, None)

            def sync(self, full_name, **kwargs):
                self.request_count += 1
                if full_name == "example/fail":
                    raise OSError("Bearer secret-must-not-persist")
                return CandidateSyncResult(
                    repository=full_name,
                    discovered_count=2,
                    hydrated_count=2,
                    timeline_checked_count=2,
                    summary={},
                    request_count=1,
                )

        repositories = [
            SimpleNamespace(
                enabled=True,
                full_name="example/ok",
                candidate_labels=(),
                ecosystem="pypi",
                primary_language="Python",
            ),
            SimpleNamespace(
                enabled=True,
                full_name="example/fail",
                candidate_labels=(),
                ecosystem="npm",
                primary_language="JavaScript",
            ),
        ]
        result = CandidateService(
            synchronizer=Synchronizer(),
            store=self.store,
            repositories=repositories,
        ).sync_enabled_repositories(
            limit_per_repository=20,
            requested_by="test-user",
        )
        persisted = self.store.sync_run(result.sync_run_id)
        self.assertEqual("partially_succeeded", result.status)
        self.assertEqual(1, result.success_count)
        self.assertEqual(1, result.failure_count)
        self.assertEqual("partially_succeeded", persisted["status"])
        self.assertNotIn("secret-must-not-persist", json.dumps(persisted))

    def test_candidate_detail_performs_lightweight_refresh(self) -> None:
        old = datetime.now(timezone.utc) - timedelta(hours=25)
        stale = issue_record(
            source_fetched_at=old.isoformat(),
            github_verified_at=old.isoformat(),
        )
        with self.store.connect() as connection:
            repository_id = self.store.upsert_repository(
                connection,
                full_name="example/demo",
                github_repository_id=1,
                html_url="https://github.com/example/demo",
                ecosystems_last_synced_at=None,
                primary_language="Python",
            )
            self.store.upsert_candidate(
                connection,
                repository_id=repository_id,
                record=stale,
                eligibility=evaluate_candidate(stale),
            )
            candidate_id = int(
                connection.execute(
                    "SELECT task_candidate_id FROM task_candidate"
                ).fetchone()[0]
            )

        class Synchronizer:
            def fetch_current_issue(self, full_name, issue_number):
                return issue_record()

        detail = CandidateService(
            synchronizer=Synchronizer(),
            store=self.store,
            repositories=[],
        ).candidate_detail(candidate_id)
        self.assertTrue(detail.refreshed)
        self.assertEqual("available", detail.availability)

    def test_refresh_stale_candidates_records_a_refresh_batch(self) -> None:
        class Synchronizer:
            request_count = 0
            retry_count = 0

            def _client_metrics(self):
                return (self.request_count, self.retry_count, 3900, None)

            def fetch_repository_health(self, full_name):
                self.request_count += 1
                return {
                    "github_repository_id": 1,
                    "html_url": f"https://github.com/{full_name}",
                    "github_verified_at": datetime.now(timezone.utc).isoformat(),
                    "is_archived": False,
                    "is_disabled": False,
                    "pushed_at": datetime.now(timezone.utc).isoformat(),
                    "maintenance_status": "active",
                    "maintenance_reason": None,
                    "activity_checked_at": datetime.now(timezone.utc).isoformat(),
                }

        repository = SimpleNamespace(
            enabled=True,
            full_name="example/demo",
            ecosystem="pypi",
            primary_language="Python",
        )
        result = CandidateService(
            synchronizer=Synchronizer(),
            store=self.store,
            repositories=[repository],
        ).refresh_stale_candidates(
            older_than_hours=24,
            requested_by="test-user",
        )
        persisted = self.store.sync_run(result.sync_run_id)
        self.assertEqual("succeeded", result.status)
        self.assertEqual("candidate_refresh", persisted["run_type"])
        self.assertEqual("succeeded", persisted["repositories"][0]["status"])


class ContractFixtureTests(unittest.TestCase):
    def test_candidate_and_sync_fixtures_are_fixed_and_sanitized(self) -> None:
        candidate_path = ROOT / "fixtures" / "contracts" / "v0.5" / "candidates.json"
        sync_path = ROOT / "fixtures" / "contracts" / "v0.5" / "sync_results.json"
        candidates = json.loads(candidate_path.read_text(encoding="utf-8"))
        sync = json.loads(sync_path.read_text(encoding="utf-8"))
        ids = [
            item["task_candidate_id"]
            for item in candidates["candidates"]
        ]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(
            CANDIDATE_AVAILABILITY_VALUES,
            {item["availability"] for item in candidates["candidates"]},
        )
        for item in candidates["candidates"]:
            self.assertEqual(
                {"code", "setup"},
                set(item["difficulty"]),
            )
            self.assertTrue(item["operating_systems"])
            self.assertIn("skill_requirements", item)
            self.assertIn("feature_evidence", item)
            self.assertEqual(
                "task-features-v0.3",
                item["task_feature_version"],
            )
        serialized = json.dumps([candidates, sync]).casefold()
        for forbidden in ("access_token", "authorization", "private repository"):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual("partially_succeeded", sync["status"])

    def test_candidate_business_modules_do_not_open_database_connections(self) -> None:
        for relative in (
            "src/oss_mentor/candidate_sync.py",
            "src/oss_mentor/candidate_refresh.py",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn(".connect(", source)
            self.assertNotIn("sqlite3", source)


if __name__ == "__main__":
    unittest.main()
