from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from oss_mentor.api import RecommendationApi
from oss_mentor.candidate_sync import CandidateDetail
from oss_mentor.collector.github_client import GitHubApiError, RateLimitExceeded
from oss_mentor.sqlite_store import SQLiteCandidateStore

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db" / "sqlite" / "001_mvp.sql"
FIXTURE = ROOT / "fixtures" / "oss_mentor_demo.sqlite3"

CONTRACT_KEYS = {
    "task_candidate_id",
    "repository_full_name",
    "issue_number",
    "title",
    "body_text",
    "html_url",
    "availability",
    "availability_reasons",
    "verified_at",
    "refreshed",
    "api_version",
    "request_id",
}


class FakeCandidateService:
    """Records calls instead of touching GitHub."""

    def __init__(self, detail: CandidateDetail | None = None, error: Exception | None = None) -> None:
        self.detail = detail
        self.error = error
        self.calls: list[int] = []

    def candidate_detail(self, task_candidate_id: int) -> CandidateDetail:
        self.calls.append(task_candidate_id)
        if self.error is not None:
            raise self.error
        assert self.detail is not None
        return self.detail


def refreshed_detail(task_candidate_id: int) -> CandidateDetail:
    return CandidateDetail(
        task_candidate_id=task_candidate_id,
        repository_full_name="example/demo",
        issue_number=7,
        title="Re-verified task",
        body_text="Fresh body from GitHub",
        html_url="https://github.com/example/demo/issues/7",
        availability="available",
        availability_reasons=(),
        verified_at="2026-10-14T00:00:00Z",
        refreshed=True,
    )


class TaskDetailApiTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database_path = Path(temporary.name) / "oss_mentor.sqlite3"
        shutil.copy(FIXTURE, self.database_path)
        self.store = SQLiteCandidateStore(self.database_path, MIGRATION)
        self.store.initialize()
        self.task_candidate_id = self._first_candidate_id()

    def _first_candidate_id(self) -> int:
        with closing(sqlite3.connect(self.database_path)) as connection:
            row = connection.execute(
                "SELECT task_candidate_id FROM task_candidate ORDER BY task_candidate_id LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(row, "demo fixture must contain at least one candidate")
        return int(row[0])

    def _row(self) -> dict:
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT tc.task_candidate_id, tc.issue_number, tc.title, tc.body_text,
                       tc.html_url, tc.candidate_availability, tc.github_verified_at,
                       r.full_name AS repository
                FROM task_candidate AS tc
                JOIN repository AS r USING (repository_id)
                WHERE tc.task_candidate_id = ?
                """,
                (self.task_candidate_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        return dict(row)

    def api(self, candidate_service=None) -> RecommendationApi:
        return RecommendationApi(self.store, candidate_service=candidate_service)

    def test_local_detail_matches_the_stored_snapshot(self) -> None:
        response = self.api().handle("GET", f"/api/v1/tasks/{self.task_candidate_id}")
        self.assertEqual(200, response.status)
        self.assertEqual(CONTRACT_KEYS, set(response.body))
        self.assertEqual("v0.5", response.body["api_version"])
        self.assertFalse(response.body["refreshed"])
        self.assertEqual(self.task_candidate_id, response.body["task_candidate_id"])
        expected = self._row()
        self.assertEqual(expected["repository"], response.body["repository_full_name"])
        self.assertEqual(int(expected["issue_number"]), response.body["issue_number"])
        self.assertEqual(expected["title"], response.body["title"])
        self.assertEqual(expected["body_text"], response.body["body_text"])
        self.assertEqual(expected["html_url"], response.body["html_url"])
        self.assertEqual(expected["candidate_availability"], response.body["availability"])
        self.assertEqual(expected["github_verified_at"], response.body["verified_at"])
        self.assertIsInstance(response.body["availability_reasons"], list)

    def test_local_detail_never_calls_the_candidate_service(self) -> None:
        service = FakeCandidateService(refreshed_detail(self.task_candidate_id))
        response = self.api(service).handle("GET", f"/api/v1/tasks/{self.task_candidate_id}")
        self.assertEqual(200, response.status)
        self.assertEqual([], service.calls)

    def test_unknown_ids_and_paths_return_not_found(self) -> None:
        api = self.api()
        for path in (
            "/api/v1/tasks/99999999",
            "/api/v1/tasks/0",
            "/api/v1/tasks/abc",
            "/api/v1/tasks/",
        ):
            with self.subTest(path=path):
                response = api.handle("GET", path)
                self.assertEqual(404, response.status)
                self.assertEqual("not_found", response.body["error"]["code"])

    def test_invalid_query_parameters_are_rejected(self) -> None:
        api = self.api()
        path = f"/api/v1/tasks/{self.task_candidate_id}"
        for query in ({"refresh": ["maybe"]}, {"limit": ["1"]}, {"refresh": [""], "x": ["1"]}):
            with self.subTest(query=query):
                response = api.handle("GET", path, query=query)
                self.assertEqual(400, response.status)
                self.assertEqual("invalid_request", response.body["error"]["code"])

    def test_non_get_methods_are_rejected(self) -> None:
        response = self.api().handle("POST", f"/api/v1/tasks/{self.task_candidate_id}")
        self.assertEqual(405, response.status)
        self.assertEqual("method_not_allowed", response.body["error"]["code"])

    def test_refresh_requires_a_configured_service(self) -> None:
        response = self.api().handle(
            "GET",
            f"/api/v1/tasks/{self.task_candidate_id}",
            query={"refresh": ["true"]},
        )
        self.assertEqual(503, response.status)
        self.assertEqual("service_not_ready", response.body["error"]["code"])

    def test_refresh_delegates_to_the_candidate_service(self) -> None:
        service = FakeCandidateService(refreshed_detail(self.task_candidate_id))
        response = self.api(service).handle(
            "GET",
            f"/api/v1/tasks/{self.task_candidate_id}",
            query={"refresh": ["1"]},
        )
        self.assertEqual(200, response.status)
        self.assertEqual([self.task_candidate_id], service.calls)
        self.assertTrue(response.body["refreshed"])
        self.assertEqual("available", response.body["availability"])
        self.assertEqual([], response.body["availability_reasons"])
        self.assertEqual("Re-verified task", response.body["title"])

    def test_refresh_false_is_explicitly_accepted(self) -> None:
        service = FakeCandidateService(refreshed_detail(self.task_candidate_id))
        response = self.api(service).handle(
            "GET",
            f"/api/v1/tasks/{self.task_candidate_id}",
            query={"refresh": ["false"]},
        )
        self.assertEqual(200, response.status)
        self.assertEqual([], service.calls)
        self.assertFalse(response.body["refreshed"])

    def test_refresh_maps_upstream_failures_to_documented_codes(self) -> None:
        cases = (
            (
                RateLimitExceeded("slow down", reset_at=None, url="https://api.github.com/x"),
                429,
                "rate_limited",
            ),
            (GitHubApiError("boom", status_code=500), 502, "github_upstream_error"),
            (KeyError("missing"), 404, "not_found"),
        )
        for error, status, code in cases:
            with self.subTest(error=type(error).__name__):
                service = FakeCandidateService(error=error)
                response = self.api(service).handle(
                    "GET",
                    f"/api/v1/tasks/{self.task_candidate_id}",
                    query={"refresh": ["true"]},
                )
                self.assertEqual(status, response.status)
                self.assertEqual(code, response.body["error"]["code"])
                self.assertEqual([self.task_candidate_id], service.calls)


class HealthCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_health_is_ready_for_a_migrated_database(self) -> None:
        database_path = self.directory / "ready.sqlite3"
        shutil.copy(FIXTURE, database_path)
        store = SQLiteCandidateStore(database_path, MIGRATION)
        store.initialize()
        response = RecommendationApi(store).handle("GET", "/health")
        self.assertEqual(200, response.status)
        self.assertEqual("ok", response.body["status"])
        self.assertTrue(response.body["database_ready"])
        self.assertNotIn("database_error", response.body)

    def test_health_reports_a_missing_database_as_not_ready(self) -> None:
        store = SQLiteCandidateStore(self.directory / "missing.sqlite3", MIGRATION)
        response = RecommendationApi(store).handle("GET", "/health")
        self.assertEqual(503, response.status)
        self.assertEqual("not_ready", response.body["status"])
        self.assertFalse(response.body["database_ready"])
        self.assertEqual("database_missing", response.body["database_error"])
        self.assertFalse(store.database_path.is_file())


if __name__ == "__main__":
    unittest.main()
