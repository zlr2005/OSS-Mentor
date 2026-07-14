from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from oss_mentor.candidate_rules import evaluate_candidate
from oss_mentor.sqlite_store import SQLiteCandidateStore


class SQLiteStoreTests(unittest.TestCase):
    def test_upsert_is_idempotent_and_github_hydration_wins(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            store = SQLiteCandidateStore(
                Path(temporary) / "test.sqlite3",
                root / "db" / "sqlite" / "001_mvp.sql",
            )
            store.initialize()
            discovery = {
                "issue_number": 7,
                "github_issue_id": None,
                "html_url": "https://github.com/example/demo/issues/7",
                "created_at": "2026-01-01T00:00:00+00:00",
                "author_association": "NONE",
                "title": "Example",
                "body_text": None,
                "labels": ["good first issue"],
                "state": "open",
                "assignment_state": "unassigned",
                "is_locked": False,
                "has_linked_open_pr": None,
                "comment_count": 0,
                "last_activity_at": "2026-01-02T00:00:00+00:00",
                "source_system": "ecosystems",
                "source_fetched_at": "2026-01-03T00:00:00+00:00",
                "github_verified_at": None,
                "is_pull_request": False,
            }
            with store.connect() as connection:
                repository_id = store.upsert_repository(
                    connection,
                    full_name="example/demo",
                    github_repository_id=None,
                    html_url="https://github.com/example/demo",
                    ecosystems_last_synced_at="2026-01-03T00:00:00Z",
                )
                store.upsert_candidate(
                    connection,
                    repository_id=repository_id,
                    record=discovery,
                    eligibility=evaluate_candidate(discovery),
                )
                hydrated = {
                    **discovery,
                    "github_issue_id": 123,
                    "body_text": "Details",
                    "source_system": "github_rest",
                    "source_fetched_at": "2026-01-04T00:00:00+00:00",
                    "github_verified_at": "2026-01-04T00:00:00+00:00",
                }
                store.upsert_candidate(
                    connection,
                    repository_id=repository_id,
                    record=hydrated,
                    eligibility=evaluate_candidate(hydrated),
                )
                row = connection.execute("SELECT * FROM task_candidate").fetchone()

            self.assertEqual(1, store.summary()["candidate_count"])
            self.assertEqual(123, row["github_issue_id"])
            self.assertEqual("eligible", row["candidate_eligibility"])
            self.assertEqual(["good first issue"], json.loads(row["labels_json"]))
            listed = store.list_candidates(eligibility="eligible")
            self.assertEqual(1, len(listed))
            self.assertEqual(7, listed[0]["issue_number"])
            self.assertTrue(listed[0]["newcomer_label_signal"])

            first = store.record_feedback(
                task_candidate_id=int(row["task_candidate_id"]),
                feedback_context="custom:12345678-1234-4234-8234-123456789abc:newcomer",
                service_track="newcomer",
                feedback_state="interested",
            )
            duplicate = store.record_feedback(
                task_candidate_id=int(row["task_candidate_id"]),
                feedback_context="custom:12345678-1234-4234-8234-123456789abc:newcomer",
                service_track="newcomer",
                feedback_state="interested",
            )
            changed = store.record_feedback(
                task_candidate_id=int(row["task_candidate_id"]),
                feedback_context="custom:12345678-1234-4234-8234-123456789abc:newcomer",
                service_track="newcomer",
                feedback_state="started",
            )
            states = store.feedback_states(
                "custom:12345678-1234-4234-8234-123456789abc:newcomer",
                [int(row["task_candidate_id"])],
            )
            with store.connect() as connection:
                event_count = connection.execute(
                    "SELECT COUNT(*) FROM recommendation_feedback_event"
                ).fetchone()[0]
            self.assertTrue(first["changed"])
            self.assertFalse(duplicate["changed"])
            self.assertTrue(changed["changed"])
            self.assertEqual("started", states[int(row["task_candidate_id"])])
            self.assertEqual(2, event_count)


if __name__ == "__main__":
    unittest.main()
