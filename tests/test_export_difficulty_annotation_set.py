from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.export_difficulty_annotation_set import (
    FIXED_SAMPLE_SPECS,
    MAX_BODY_EXCERPT_CHARS,
    build_annotation_documents,
    connect_readonly,
    make_body_excerpt,
    write_json,
)


class ExportDifficultyAnnotationSetTests(unittest.TestCase):
    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _create_database(
        path: Path,
        *,
        omit_last: bool = False,
        malformed_first: bool = False,
        first_ineligible: bool = False,
    ) -> None:
        connection = sqlite3.connect(path)
        try:
            connection.executescript(
                """
                CREATE TABLE repository (
                    repository_id INTEGER PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    primary_language TEXT,
                    is_archived INTEGER NOT NULL DEFAULT 0,
                    is_disabled INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE task_candidate (
                    task_candidate_id INTEGER PRIMARY KEY,
                    repository_id INTEGER NOT NULL,
                    issue_number INTEGER NOT NULL,
                    html_url TEXT,
                    title TEXT,
                    body_text,
                    labels_json TEXT,
                    task_types_json TEXT,
                    comment_count INTEGER,
                    candidate_eligibility TEXT,
                    text_clarity_score REAL,
                    estimated_code_difficulty INTEGER,
                    estimated_setup_difficulty INTEGER,
                    estimated_project_context_difficulty INTEGER,
                    estimated_collaboration_difficulty INTEGER,
                    estimated_effort_bucket TEXT,
                    novice_fit_probability REAL,
                    newcomer_score REAL,
                    growth_value_score REAL,
                    task_feature_version TEXT
                );
                """
            )
            repositories = sorted(
                {spec.repository for spec in FIXED_SAMPLE_SPECS},
                key=str.casefold,
            )
            repository_ids = {
                repository: index + 1
                for index, repository in enumerate(repositories)
            }
            connection.executemany(
                """
                INSERT INTO repository (
                    repository_id, full_name, primary_language,
                    is_archived, is_disabled
                ) VALUES (?, ?, 'Python', 0, 0)
                """,
                [
                    (repository_id, repository)
                    for repository, repository_id in repository_ids.items()
                ],
            )

            specs = list(FIXED_SAMPLE_SPECS)
            if omit_last:
                specs = specs[:-1]
            for index, spec in enumerate(specs, start=1):
                labels_json = '["good first issue", "bug"]'
                body_text: object = (
                    "### Description\nUseful explanation.\n"
                    "```python\nprint('secret code')\n```\n"
                    "2026-01-01 12:00:00 ERROR noisy log\n"
                    "at package.Class.method(File.java:1)\n"
                    "Final useful sentence."
                )
                if malformed_first and index == 1:
                    labels_json = '["broken"'
                    body_text = 12345
                candidate_eligibility = (
                    "ineligible" if first_ineligible and index == 1 else "eligible"
                )
                connection.execute(
                    """
                    INSERT INTO task_candidate (
                        task_candidate_id, repository_id, issue_number,
                        html_url, title, body_text, labels_json,
                        task_types_json, comment_count,
                        candidate_eligibility, text_clarity_score,
                        estimated_code_difficulty,
                        estimated_setup_difficulty,
                        estimated_project_context_difficulty,
                        estimated_collaboration_difficulty,
                        estimated_effort_bucket,
                        novice_fit_probability, newcomer_score,
                        growth_value_score, task_feature_version
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        index,
                        repository_ids[spec.repository],
                        spec.issue_number,
                        f"https://github.com/{spec.repository}/issues/{spec.issue_number}",
                        f"Issue {spec.issue_number}",
                        body_text,
                        labels_json,
                        '["bug_fix"]',
                        index % 12,
                        candidate_eligibility,
                        50.0,
                        1,
                        1,
                        1,
                        0,
                        "half_day",
                        0.5,
                        50.0,
                        50.0,
                        "task-features-v0.2",
                    ),
                )
            connection.commit()
        finally:
            connection.close()

    def test_database_is_read_only_and_hash_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "source.sqlite3"
            self._create_database(database)
            before = self._sha256(database)

            connection = connect_readonly(database)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute(
                        "UPDATE task_candidate SET title='changed' WHERE 1=1"
                    )
            finally:
                connection.close()

            build_annotation_documents(database)
            self.assertEqual(before, self._sha256(database))

    def test_fixed_36_samples_exist_and_are_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "source.sqlite3"
            self._create_database(database)
            annotation, predictions = build_annotation_documents(database)

            self.assertEqual(36, annotation["sample_count"])
            self.assertEqual(36, predictions["sample_count"])
            keys = [
                (
                    record["annotation_input"]["repository"].casefold(),
                    record["annotation_input"]["issue_number"],
                )
                for record in annotation["records"]
            ]
            self.assertEqual(sorted(keys), keys)
            self.assertEqual(len(keys), len(set(keys)))

    def test_repeated_output_is_byte_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "source.sqlite3"
            self._create_database(database)

            first_annotation, first_predictions = build_annotation_documents(
                database
            )
            second_annotation, second_predictions = build_annotation_documents(
                database
            )
            self.assertEqual(first_annotation, second_annotation)
            self.assertEqual(first_predictions, second_predictions)

            first_path = write_json(first_annotation, root / "first.json")
            second_path = write_json(second_annotation, root / "second.json")
            self.assertEqual(first_path.read_bytes(), second_path.read_bytes())

    def test_body_excerpt_is_bounded_and_removes_bulky_noise(self) -> None:
        body = (
            "<!-- hidden template -->\n"
            "### Problem\nUseful summary.\n"
            "```python\n" + "x = 1\n" * 2000 + "```\n"
            "2026-01-01 12:00:00 ERROR noisy line\n"
            "at package.Class.method(File.java:1)\n"
            "_No response_\n"
            "Final explanation.\n"
            + "tail " * 2000
        )
        excerpt = make_body_excerpt(body)
        self.assertLessEqual(len(excerpt), MAX_BODY_EXCERPT_CHARS)
        self.assertIn("Useful summary", excerpt)
        self.assertIn("[code block omitted]", excerpt)
        self.assertIn("[log lines omitted]", excerpt)
        self.assertNotIn("hidden template", excerpt)
        self.assertNotIn("x = 1", excerpt)
        self.assertNotIn("_No response_", excerpt)

    def test_annotation_input_hides_current_prediction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "source.sqlite3"
            self._create_database(database)
            annotation, predictions = build_annotation_documents(database)

            forbidden = {
                "estimated_code_difficulty",
                "estimated_setup_difficulty",
                "estimated_project_context_difficulty",
                "estimated_collaboration_difficulty",
                "estimated_effort_bucket",
                "current_prediction",
                "text_clarity_score",
                "newcomer_score",
                "growth_value_score",
            }
            for record in annotation["records"]:
                self.assertTrue(
                    forbidden.isdisjoint(record["annotation_input"].keys())
                )
                self.assertEqual(
                    {
                        "annotation_confidence": None,
                        "code_difficulty": None,
                        "collaboration_difficulty": None,
                        "effort_bucket": None,
                        "evidence": [],
                        "project_context_difficulty": None,
                        "rationale": "",
                        "setup_difficulty": None,
                    },
                    record["human_annotation"],
                )

            self.assertIn(
                "current_prediction", predictions["records"][0]
            )

    def test_malformed_labels_and_body_do_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "source.sqlite3"
            self._create_database(database, malformed_first=True)
            annotation, _ = build_annotation_documents(database)
            malformed = next(
                record["annotation_input"]
                for record in annotation["records"]
                if record["annotation_input"]["task_candidate_id"] == 1
            )
            self.assertEqual([], malformed["labels"])
            self.assertEqual("", malformed["body_excerpt"])

    def test_missing_fixed_task_fails_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "source.sqlite3"
            self._create_database(database, omit_last=True)
            with self.assertRaisesRegex(
                RuntimeError, "Fixed calibration tasks are missing"
            ):
                build_annotation_documents(database)

    def test_ineligible_fixed_task_fails_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "source.sqlite3"
            self._create_database(database, first_ineligible=True)
            with self.assertRaisesRegex(
                RuntimeError, "Fixed calibration tasks are not eligible"
            ):
                build_annotation_documents(database)

    def test_body_excerpt_limit_rejects_invalid_values(self) -> None:
        with self.assertRaises(ValueError):
            make_body_excerpt("body", max_chars=0)
        with self.assertRaises(ValueError):
            make_body_excerpt(
                "body", max_chars=MAX_BODY_EXCERPT_CHARS + 1
            )

    def test_sample_groups_are_present_in_both_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "source.sqlite3"
            self._create_database(database)
            annotation, predictions = build_annotation_documents(database)

            annotation_groups = {
                (
                    record["annotation_input"]["repository"],
                    record["annotation_input"]["issue_number"],
                ): record["annotation_input"]["sample_groups"]
                for record in annotation["records"]
            }
            prediction_groups = {
                (record["repository"], record["issue_number"]): record[
                    "sample_groups"
                ]
                for record in predictions["records"]
            }
            self.assertEqual(annotation_groups, prediction_groups)


if __name__ == "__main__":
    unittest.main()