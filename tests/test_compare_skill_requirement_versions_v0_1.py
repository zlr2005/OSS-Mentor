from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.compare_skill_requirement_versions_v0_1 import compare_databases, file_sha256


SCHEMA = """
CREATE TABLE repository (
    repository_id INTEGER PRIMARY KEY,
    full_name TEXT NOT NULL UNIQUE,
    primary_language TEXT,
    is_archived INTEGER NOT NULL DEFAULT 0,
    is_disabled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE task_candidate (
    task_candidate_id INTEGER PRIMARY KEY,
    repository_id INTEGER NOT NULL,
    issue_number INTEGER NOT NULL,
    title TEXT,
    body_text TEXT,
    labels_json TEXT NOT NULL DEFAULT '[]',
    state TEXT NOT NULL,
    assignment_state TEXT NOT NULL,
    is_locked INTEGER NOT NULL DEFAULT 0,
    has_linked_open_pr INTEGER,
    comment_count INTEGER NOT NULL DEFAULT 0,
    candidate_eligibility TEXT NOT NULL,
    ineligibility_reasons_json TEXT NOT NULL DEFAULT '[]',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    newcomer_label_signal INTEGER NOT NULL DEFAULT 0,
    task_types_json TEXT NOT NULL DEFAULT '[]',
    estimated_code_difficulty INTEGER,
    estimated_setup_difficulty INTEGER,
    estimated_project_context_difficulty INTEGER,
    estimated_collaboration_difficulty INTEGER,
    estimated_effort_bucket TEXT,
    task_feature_version TEXT
);
CREATE TABLE task_skill_requirement (
    task_candidate_id INTEGER NOT NULL,
    skill_name TEXT NOT NULL,
    minimum_level INTEGER NOT NULL,
    importance REAL NOT NULL,
    requirement_source TEXT NOT NULL,
    feature_version TEXT NOT NULL
);
"""


class CompareSkillRequirementVersionsV01Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.baseline = self.root / "baseline.sqlite3"
        self.candidate = self.root / "candidate.sqlite3"
        connection = sqlite3.connect(self.baseline)
        try:
            connection.executescript(SCHEMA)
            connection.execute("INSERT INTO repository VALUES (1, 'example/project', 'Python', 0, 0)")
            connection.execute(
                """
                INSERT INTO task_candidate VALUES (
                    1, 1, 1, 'Fix parser bug', 'Expected behavior', '[]', 'open',
                    'unassigned', 0, 0, 0, 'eligible', '[]', '[]', 0, '["bug_fix"]',
                    1, 1, 1, 0, 'half_day', 'task-features-v0.3')
                """
            )
            connection.execute(
                "INSERT INTO task_skill_requirement VALUES (1, 'Python', 1, 1.0, 'repository_primary_language', 'task-features-v0.3')"
            )
            connection.commit()
        finally:
            connection.close()
        shutil.copyfile(self.baseline, self.candidate)
        self.expected = file_sha256(self.baseline)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_skill_only_change_keeps_hard_invariants(self) -> None:
        connection = sqlite3.connect(self.candidate)
        try:
            connection.execute("UPDATE task_candidate SET task_feature_version = 'task-features-v0.4'")
            connection.execute(
                "INSERT INTO task_skill_requirement VALUES (1, 'Docker', 1, 0.7, 'inferred_tool_requirement', 'task-features-v0.4')"
            )
            connection.commit()
        finally:
            connection.close()
        report = compare_databases(
            self.baseline,
            self.candidate,
            expected_baseline_sha256=self.expected,
        )
        self.assertTrue(report["hard_invariant_pass"])
        self.assertEqual(0, report["task_type_regression"]["change_count"])
        self.assertEqual(1, report["skill_requirement_changes"]["changed_task_count"])

    def test_difficulty_change_fails_hard_invariant(self) -> None:
        connection = sqlite3.connect(self.candidate)
        try:
            connection.execute("UPDATE task_candidate SET estimated_code_difficulty = 2")
            connection.commit()
        finally:
            connection.close()
        report = compare_databases(
            self.baseline,
            self.candidate,
            expected_baseline_sha256=self.expected,
        )
        self.assertFalse(report["hard_invariant_pass"])
        self.assertEqual(1, report["difficulty_regression"]["change_counts"]["estimated_code_difficulty"])

    def test_raw_input_change_fails_hard_invariant(self) -> None:
        connection = sqlite3.connect(self.candidate)
        try:
            connection.execute("UPDATE task_candidate SET title = 'Different title'")
            connection.commit()
        finally:
            connection.close()
        report = compare_databases(
            self.baseline,
            self.candidate,
            expected_baseline_sha256=self.expected,
        )
        self.assertFalse(report["hard_invariant_pass"])
        self.assertEqual(1, report["raw_input"]["change_count"])


if __name__ == "__main__":
    unittest.main()
