from __future__ import annotations

import unittest
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from oss_mentor.candidate_sync import CandidateSynchronizer
from oss_mentor.collector.github_client import GitHubApiError
from oss_mentor.sqlite_store import SQLiteCandidateStore


class CandidateSyncTests(unittest.TestCase):
    def test_transferred_issue_is_treated_as_unavailable(self) -> None:
        fetched_at = datetime(2026, 7, 16, tzinfo=timezone.utc)

        class GitHub:
            def get(self, path):
                return SimpleNamespace(
                    payload={
                        "number": 146867,
                        "repository_url": "https://api.github.com/repos/rust-lang/rust",
                    },
                    fetched_at=fetched_at,
                )

        synchronizer = CandidateSynchronizer(
            SimpleNamespace(), GitHub(), SimpleNamespace()
        )
        with self.assertRaises(GitHubApiError) as raised:
            synchronizer.fetch_current_issue("rust-lang/rust-clippy", 15730)

        self.assertEqual(410, raised.exception.status_code)

    def test_detects_only_open_cross_referenced_pull_request(self) -> None:
        events = [
            {
                "event": "cross-referenced",
                "source": {"issue": {"state": "closed", "pull_request": {}}},
            },
            {"event": "assigned", "assignee": {"login": "example"}},
        ]
        self.assertFalse(CandidateSynchronizer._has_linked_open_pr(events))

        events.append(
            {
                "event": "cross-referenced",
                "source": {"issue": {"state": "open", "pull_request": {}}},
            }
        )
        self.assertTrue(CandidateSynchronizer._has_linked_open_pr(events))

    def test_repeating_same_sync_does_not_duplicate_candidates(self) -> None:
        fetched_at = datetime(2026, 7, 14, tzinfo=timezone.utc)
        issue = {
            "number": 7,
            "id": 123,
            "html_url": "https://github.com/example/demo/issues/7",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-07-01T00:00:00Z",
            "author_association": "NONE",
            "title": "Example",
            "body": "Details",
            "labels": [{"name": "good first issue"}],
            "state": "open",
            "assignees": [],
            "locked": False,
            "comments": 0,
        }

        class Ecosystems:
            def get_repository(self, full_name):
                return SimpleNamespace(
                    payload={"last_synced_at": "2026-07-14T00:00:00Z"},
                    fetched_at=fetched_at,
                )

            def get_issues(self, full_name, **kwargs):
                return SimpleNamespace(payload=[issue], fetched_at=fetched_at)

        class GitHub:
            def get(self, path):
                payload = (
                    {
                        "id": 1,
                        "html_url": "https://github.com/example/demo",
                        "archived": False,
                        "disabled": False,
                        "pushed_at": "2026-07-13T00:00:00Z",
                    }
                    if path == "/repos/example/demo"
                    else issue
                )
                return SimpleNamespace(payload=payload, fetched_at=fetched_at)

            def iter_pages(self, path, params=None):
                return iter([SimpleNamespace(payload=[])])

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            store = SQLiteCandidateStore(
                Path(temporary) / "sync.sqlite3",
                root / "db" / "sqlite" / "001_mvp.sql",
            )
            synchronizer = CandidateSynchronizer(Ecosystems(), GitHub(), store)
            synchronizer.sync("example/demo", limit=20)
            synchronizer.sync("example/demo", limit=20)
            self.assertEqual(1, store.summary()["candidate_count"])

    def test_issue_404_is_excluded_without_failing_repository_sync(self) -> None:
        fetched_at = datetime(2026, 7, 15, tzinfo=timezone.utc)

        def issue(number: int) -> dict[str, object]:
            return {
                "number": number,
                "id": 100 + number,
                "html_url": f"https://github.com/example/demo/issues/{number}",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-07-01T00:00:00Z",
                "author_association": "NONE",
                "title": f"Issue {number}",
                "body": "Details",
                "labels": [{"name": "good first issue"}],
                "state": "open",
                "assignees": [],
                "locked": False,
                "comments": 0,
            }

        class Ecosystems:
            def get_repository(self, full_name):
                return SimpleNamespace(payload={}, fetched_at=fetched_at)

            def get_issues(self, full_name, **kwargs):
                return SimpleNamespace(
                    payload=[issue(7), issue(8)], fetched_at=fetched_at
                )

        class GitHub:
            def get(self, path):
                if path == "/repos/example/demo":
                    return SimpleNamespace(
                        payload={
                            "id": 1,
                            "html_url": "https://github.com/example/demo",
                            "archived": False,
                            "disabled": False,
                        },
                        fetched_at=fetched_at,
                    )
                if path.endswith("/7"):
                    raise GitHubApiError("gone", status_code=404, url=path)
                return SimpleNamespace(payload=issue(8), fetched_at=fetched_at)

            def iter_pages(self, path, params=None):
                return iter([SimpleNamespace(payload=[])])

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            store = SQLiteCandidateStore(
                Path(temporary) / "sync.sqlite3",
                root / "db" / "sqlite" / "001_mvp.sql",
            )
            result = CandidateSynchronizer(Ecosystems(), GitHub(), store).sync(
                "example/demo", limit=20
            )
            candidates = store.list_candidates(limit=10)

        self.assertEqual(2, result.discovered_count)
        self.assertEqual(1, result.hydrated_count)
        self.assertEqual(1, result.unavailable_count)
        self.assertEqual(2, len(candidates))
        unavailable = next(item for item in candidates if item["issue_number"] == 7)
        self.assertEqual("excluded", unavailable["candidate_eligibility"])
        self.assertEqual(["github_unavailable"], unavailable["ineligibility_reasons"])

    def test_current_github_label_discovery_precedes_stale_ecosystems_results(self) -> None:
        fetched_at = datetime(2026, 7, 16, tzinfo=timezone.utc)

        def issue(number: int, title: str) -> dict[str, object]:
            return {
                "number": number,
                "id": 100 + number,
                "html_url": f"https://github.com/example/demo/issues/{number}",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-07-16T00:00:00Z",
                "author_association": "NONE",
                "title": title,
                "body": "Details",
                "labels": [{"name": "good first issue"}],
                "state": "open",
                "assignees": [],
                "locked": False,
                "comments": 0,
            }

        stale = issue(1, "Recent generic issue")
        current = issue(2, "Refactor current code")

        class Ecosystems:
            def get_repository(self, full_name):
                return SimpleNamespace(payload={}, fetched_at=fetched_at)

            def get_issues(self, full_name, **kwargs):
                return SimpleNamespace(payload=[stale], fetched_at=fetched_at)

        class GitHub:
            def get(self, path, params=None):
                if path == "/repos/example/demo":
                    payload = {
                        "id": 1,
                        "html_url": "https://github.com/example/demo",
                        "archived": False,
                        "disabled": False,
                    }
                elif path == "/repos/example/demo/issues":
                    payload = [current]
                else:
                    payload = current
                return SimpleNamespace(payload=payload, fetched_at=fetched_at)

            def iter_pages(self, path, params=None):
                return iter([SimpleNamespace(payload=[])])

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            store = SQLiteCandidateStore(
                Path(temporary) / "sync.sqlite3",
                root / "db" / "sqlite" / "001_mvp.sql",
            )
            result = CandidateSynchronizer(Ecosystems(), GitHub(), store).sync(
                "example/demo",
                limit=1,
                candidate_labels=("good first issue",),
                primary_language="Java",
            )
            candidates = store.list_candidates(limit=10)

        self.assertEqual(1, result.discovered_count)
        self.assertEqual([2], [candidate["issue_number"] for candidate in candidates])


if __name__ == "__main__":
    unittest.main()
