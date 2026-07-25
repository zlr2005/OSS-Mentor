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
            summary = store.feedback_summary()
            self.assertEqual(1, summary["current"]["total"])
            self.assertEqual(1, summary["current"]["started"])
            self.assertEqual(1, summary["by_track"]["newcomer"]["started"])
            self.assertEqual(1, summary["transitions"]["interested_to_started"])
            self.assertEqual(0, summary["transitions"]["started_to_completed"])
    def test_data_quality_records_group_requirements_without_duplicate_tasks(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[1]

        with tempfile.TemporaryDirectory() as temporary:
            store = SQLiteCandidateStore(
                Path(temporary) / "quality.sqlite3",
                root / "db" / "sqlite" / "001_mvp.sql",
            )
            store.initialize()

            base_record = {
                "github_issue_id": None,
                "created_at": "2026-01-01T00:00:00+00:00",
                "author_association": "NONE",
                "body_text": "Steps to reproduce the problem.",
                "labels": ["bug"],
                "state": "open",
                "assignment_state": "unassigned",
                "is_locked": False,
                "has_linked_open_pr": False,
                "comment_count": 1,
                "last_activity_at": "2026-01-02T00:00:00+00:00",
                "source_system": "github_rest",
                "source_fetched_at": "2026-01-03T00:00:00+00:00",
                "github_verified_at": "2026-01-03T00:00:00+00:00",
                "is_pull_request": False,
            }

            with store.connect() as connection:
                repository_id = store.upsert_repository(
                    connection,
                    full_name="example/quality",
                    github_repository_id=99,
                    html_url="https://github.com/example/quality",
                    ecosystems_last_synced_at=None,
                    ecosystem="pypi",
                    primary_language="Python",
                    github_verified_at="2026-01-03T00:00:00+00:00",
                )

                for issue_number, title in (
                    (1, "Fix parser bug"),
                    (2, "Add tests"),
                ):
                    record = {
                        **base_record,
                        "issue_number": issue_number,
                        "html_url": (
                            "https://github.com/example/quality/issues/"
                            f"{issue_number}"
                        ),
                        "title": title,
                    }
                    store.upsert_candidate(
                        connection,
                        repository_id=repository_id,
                        record=record,
                        eligibility=evaluate_candidate(record),
                    )

                candidate_rows = connection.execute(
                    """
                    SELECT task_candidate_id, issue_number
                    FROM task_candidate
                    ORDER BY issue_number
                    """
                ).fetchall()

                first_id = int(
                    candidate_rows[0]["task_candidate_id"]
                )
                second_id = int(
                    candidate_rows[1]["task_candidate_id"]
                )

                connection.execute(
                    """
                    UPDATE task_candidate SET
                        task_types_json = ?,
                        text_clarity_score = 80,
                        estimated_code_difficulty = 1,
                        estimated_setup_difficulty = 1,
                        estimated_project_context_difficulty = 1,
                        estimated_collaboration_difficulty = 0,
                        estimated_effort_bucket = 'half_day',
                        novice_fit_probability = 0.8,
                        newcomer_score = 80,
                        growth_value_score = 40,
                        feature_evidence_json = ?,
                        feature_extracted_at = ?,
                        task_feature_version = ?
                    """,
                    (
                        '["bug_fix"]',
                        '{"source":"test"}',
                        "2026-01-04T00:00:00+00:00",
                        "task-features-v0.1",
                    ),
                )

                connection.executemany(
                    """
                    INSERT INTO task_skill_requirement (
                        task_candidate_id,
                        skill_name,
                        minimum_level,
                        importance,
                        requirement_source,
                        feature_version
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            first_id,
                            "Python",
                            1,
                            1.0,
                            "repository_primary_language",
                            "task-features-v0.1",
                        ),
                        (
                            first_id,
                            "testing",
                            1,
                            0.6,
                            "inferred_task_type",
                            "task-features-v0.1",
                        ),
                        (
                            second_id,
                            "Python",
                            1,
                            1.0,
                            "repository_primary_language",
                            "task-features-v0.1",
                        ),
                    ),
                )

            records = store.data_quality_records()

            self.assertEqual(2, len(records))
            self.assertEqual(
                [first_id, second_id],
                [
                    row["task_candidate_id"]
                    for row in records
                ],
            )

            first_record = records[0]
            second_record = records[1]

            self.assertEqual(
                "example/quality",
                first_record["repository"],
            )
            self.assertEqual(
                "Python",
                first_record["primary_language"],
            )

            # 原始 JSON 字符串不能在 Store 层被静默解析或修正。
            self.assertEqual(
                '["bug_fix"]',
                first_record["task_types_json"],
            )
            self.assertEqual(
                '{"source":"test"}',
                first_record["feature_evidence_json"],
            )

            # 第一条任务有两个技能，第二条有一个；
            # 候选任务仍然只能返回两条，不能被 JOIN 放大为三条。
            self.assertEqual(
                2,
                first_record["skill_requirement_count"],
            )
            self.assertEqual(
                1,
                second_record["skill_requirement_count"],
            )

            self.assertEqual(
                ["Python", "testing"],
                [
                    item["skill_name"]
                    for item in first_record["requirements"]
                ],
            )
            self.assertEqual(
                ["Python"],
                [
                    item["skill_name"]
                    for item in second_record["requirements"]
                ],
            )
if __name__ == "__main__":
    unittest.main()
