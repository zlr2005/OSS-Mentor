from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from oss_mentor.candidate_refresh import CandidateRefresher
from oss_mentor.candidate_report import build_candidate_report
from oss_mentor.candidate_rules import evaluate_candidate
from oss_mentor.collector.github_client import GitHubApiError
from oss_mentor.sqlite_store import SQLiteCandidateStore


def record(**overrides):
    value = {
        "issue_number": 7,
        "github_issue_id": 123,
        "html_url": "https://github.com/example/demo/issues/7",
        "created_at": "2026-01-01T00:00:00+00:00",
        "author_association": "NONE",
        "title": "Example",
        "body_text": "Details",
        "labels": ["good first issue"],
        "state": "open",
        "assignment_state": "unassigned",
        "is_locked": False,
        "has_linked_open_pr": False,
        "comment_count": 0,
        "last_activity_at": "2026-01-02T00:00:00+00:00",
        "source_system": "github_rest",
        "source_fetched_at": "2026-01-03T00:00:00+00:00",
        "github_verified_at": "2026-01-03T00:00:00+00:00",
        "is_pull_request": False,
    }
    value.update(overrides)
    return value


class FakeSynchronizer:
    def __init__(self, current):
        self.current = current

    def fetch_repository_health(self, full_name):
        return {
            "github_repository_id": 1,
            "html_url": f"https://github.com/{full_name}",
            "github_verified_at": "2026-07-14T00:00:00+00:00",
            "is_archived": False,
            "is_disabled": False,
            "pushed_at": "2026-07-13T00:00:00Z",
        }

    def fetch_current_issue(self, full_name, issue_number):
        if isinstance(self.current, Exception):
            raise self.current
        return self.current


class CandidateRefreshReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(__file__).resolve().parents[1]
        self.store = SQLiteCandidateStore(
            Path(self.temporary.name) / "test.sqlite3",
            root / "db" / "sqlite" / "001_mvp.sql",
        )
        self.store.initialize()
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
            initial = record(github_verified_at=None)
            self.store.upsert_candidate(
                connection,
                repository_id=repository_id,
                record=initial,
                eligibility=evaluate_candidate(initial),
            )
        self.repository = SimpleNamespace(
            full_name="example/demo", ecosystem="pypi", primary_language="Python"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _refresh(self, current):
        return CandidateRefresher(FakeSynchronizer(current), self.store).refresh(
            [self.repository], older_than_hours=24, limit=500
        )

    def _make_stale(self):
        with self.store.connect() as connection:
            connection.execute("UPDATE task_candidate SET github_verified_at = NULL")

    def _candidate(self):
        with self.store.connect() as connection:
            return connection.execute("SELECT * FROM task_candidate").fetchone()

    def test_closed_assigned_locked_linked_and_recovery_states(self) -> None:
        cases = [
            (record(state="closed"), "excluded", "not_open"),
            (record(assignment_state="assigned"), "temporarily_ineligible", "already_assigned"),
            (record(is_locked=True), "temporarily_ineligible", "locked"),
            (record(has_linked_open_pr=True), "temporarily_ineligible", "linked_open_pr"),
            (record(), "eligible", None),
        ]
        for current, expected_status, expected_reason in cases:
            with self.subTest(expected_status=expected_status, reason=expected_reason):
                self._make_stale()
                self._refresh(current)
                candidate = self._candidate()
                self.assertEqual(expected_status, candidate["candidate_eligibility"])
                if expected_reason:
                    self.assertIn(expected_reason, candidate["ineligibility_reasons_json"])

    def test_404_marks_candidate_github_unavailable(self) -> None:
        self._refresh(GitHubApiError("gone", status_code=404))
        candidate = self._candidate()
        self.assertEqual("excluded", candidate["candidate_eligibility"])
        self.assertIn("github_unavailable", candidate["ineligibility_reasons_json"])

    def test_only_stale_candidates_are_selected(self) -> None:
        self._refresh(record(github_verified_at=datetime.now(timezone.utc).isoformat()))
        result = self._refresh(record(state="closed"))
        self.assertEqual(0, result.selected_count)
        self.assertEqual("eligible", self._candidate()["candidate_eligibility"])

    def test_archived_repository_is_excluded_from_matching_and_report_totals_balance(self) -> None:
        with self.store.connect() as connection:
            connection.execute(
                "UPDATE task_candidate SET task_feature_version = 'test'"
            )
            connection.execute("UPDATE repository SET is_archived = 1")
        self.assertEqual([], self.store.matchable_candidates())
        report = build_candidate_report(
            self.store, now=datetime(2026, 7, 14, tzinfo=timezone.utc)
        )
        counts = report["candidate_summary"]["eligibility_counts"]
        self.assertEqual(report["candidate_summary"]["total_count"], sum(counts.values()))
        self.assertEqual(1, report["repository_summary"]["archived_or_disabled_count"])
        self.assertEqual(0, report["warning_counts"]["eligible_linked_pr_not_checked"])

    def test_maintenance_inactive_repository_is_excluded_from_matching(self) -> None:
        with self.store.connect() as connection:
            connection.execute(
                "UPDATE task_candidate SET task_feature_version = 'test'"
            )
            connection.execute(
                """
                UPDATE repository
                SET maintenance_status = 'inactive',
                    maintenance_reason = 'no_repository_push_within_180_days'
                """
            )

        self.assertEqual([], self.store.matchable_candidates())
        report = build_candidate_report(
            self.store, now=datetime(2026, 7, 29, tzinfo=timezone.utc)
        )
        self.assertEqual(
            1,
            report["repository_summary"]["maintenance_inactive_count"],
        )
        self.assertEqual(
            0,
            report["candidate_summary"]["verified_eligible_count"],
        )

    def test_report_exposes_language_and_task_inventory_for_both_tracks(self) -> None:
        with self.store.connect() as connection:
            connection.execute(
                """
                UPDATE task_candidate
                SET task_feature_version = 'test',
                    task_types_json = '["documentation"]'
                """
            )
        report = build_candidate_report(
            self.store, now=datetime(2026, 7, 14, tzinfo=timezone.utc)
        )
        self.assertEqual("candidate_pool_report_v0.3", report["schema_version"])
        for track in ("newcomer", "growth"):
            coverage = report["recommendation_coverage"][track]
            self.assertEqual(1, coverage["total_count"])
            self.assertEqual(1, coverage["language_counts"]["python"])
            self.assertEqual(1, coverage["task_type_counts"]["documentation"])
            self.assertEqual(
                1,
                coverage["language_task_type_counts"]["python"]["documentation"],
            )
            self.assertNotIn("python:documentation", coverage["zero_combinations"])
        self.assertEqual(0, report["warning_counts"]["eligible_linked_pr_not_checked"])

    def test_report_includes_sync_request_costs_and_failures(self) -> None:
        report = build_candidate_report(
            self.store,
            now=datetime(2026, 7, 14, tzinfo=timezone.utc),
            operation_reports={
                "sync": {
                    "status": "partial",
                    "repository_count": 2,
                    "successful_repository_count": 1,
                    "failed_repository_count": 1,
                    "github_request_count": 7,
                    "ecosystems_request_count": 3,
                    "repositories": [
                        {"repository": "example/demo", "status": "completed"},
                        {"repository": "example/fail", "status": "failed"},
                    ],
                    "errors": [
                        {
                            "repository": "example/fail",
                            "error_type": "TimeoutError",
                            "error": "timed out",
                        }
                    ],
                }
            },
        )
        operation = report["operation_summary"]["sync"]
        self.assertEqual(7, operation["github_request_count"])
        self.assertEqual(3, operation["ecosystems_request_count"])
        self.assertEqual("example/fail", operation["errors"][0]["repository"])

    def test_empty_database_applies_every_sqlite_migration(self) -> None:
        with self.store.connect() as connection:
            migrations = {
                row[0]
                for row in connection.execute(
                    "SELECT migration_name FROM schema_migration"
                ).fetchall()
            }
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(repository)")
            }
        self.assertEqual(
            {
                "001_mvp.sql",
                "002_candidate_features.sql",
                "003_personalized_matching.sql",
                "004_recommendation_feedback.sql",
                "005_candidate_refresh.sql",
                "006_repository_activity.sql",
            },
            migrations,
        )
        self.assertIn("last_candidate_refresh_at", columns)
        self.assertIn("maintenance_status", columns)


if __name__ == "__main__":
    unittest.main()
