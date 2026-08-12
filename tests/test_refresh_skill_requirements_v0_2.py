from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.refresh_skill_requirements_v0_2 import file_sha256, refresh_database


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE repository (
    repository_id INTEGER PRIMARY KEY,
    full_name TEXT NOT NULL UNIQUE,
    github_repository_id INTEGER,
    html_url TEXT NOT NULL,
    ecosystem TEXT,
    primary_language TEXT,
    is_archived INTEGER NOT NULL DEFAULT 0,
    is_disabled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE task_candidate (
    task_candidate_id INTEGER PRIMARY KEY,
    repository_id INTEGER NOT NULL REFERENCES repository(repository_id),
    issue_number INTEGER NOT NULL,
    github_issue_id INTEGER,
    html_url TEXT NOT NULL,
    created_at TEXT NOT NULL,
    author_association TEXT,
    title TEXT NOT NULL,
    body_text TEXT,
    labels_json TEXT NOT NULL DEFAULT '[]',
    state TEXT NOT NULL,
    assignment_state TEXT NOT NULL,
    is_locked INTEGER NOT NULL DEFAULT 0,
    has_linked_open_pr INTEGER,
    comment_count INTEGER NOT NULL DEFAULT 0,
    last_activity_at TEXT,
    source_system TEXT NOT NULL,
    source_fetched_at TEXT NOT NULL,
    github_verified_at TEXT,
    candidate_eligibility TEXT NOT NULL,
    ineligibility_reasons_json TEXT NOT NULL DEFAULT '[]',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    newcomer_label_signal INTEGER NOT NULL DEFAULT 0,
    feature_definition_version TEXT NOT NULL,
    has_reproduction_steps INTEGER,
    has_acceptance_criteria INTEGER,
    has_expected_behavior INTEGER,
    has_affected_module_hint INTEGER,
    task_types_json TEXT NOT NULL DEFAULT '[]',
    text_clarity_score REAL,
    estimated_code_difficulty INTEGER,
    estimated_setup_difficulty INTEGER,
    estimated_project_context_difficulty INTEGER,
    estimated_collaboration_difficulty INTEGER,
    estimated_effort_bucket TEXT,
    novice_fit_probability REAL,
    newcomer_score REAL,
    growth_value_score REAL,
    feature_evidence_json TEXT NOT NULL DEFAULT '{}',
    feature_extracted_at TEXT,
    task_feature_version TEXT
);
CREATE TABLE task_skill_requirement (
    task_candidate_id INTEGER NOT NULL REFERENCES task_candidate(task_candidate_id),
    skill_name TEXT NOT NULL COLLATE NOCASE,
    minimum_level INTEGER NOT NULL,
    importance REAL NOT NULL,
    requirement_source TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    PRIMARY KEY (task_candidate_id, skill_name)
);
"""


class RefreshSkillRequirementsV02Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.baseline = self.root / "baseline.sqlite3"
        self.candidate = self.root / "candidate.sqlite3"
        connection = sqlite3.connect(self.baseline)
        try:
            connection.executescript(SCHEMA)
            connection.execute(
                "INSERT INTO repository VALUES (1, 'example/project', 1, 'https://github.com/example/project', 'pypi', 'Python', 0, 0)"
            )
            connection.execute(
                """
                INSERT INTO task_candidate (
                    task_candidate_id, repository_id, issue_number, html_url, created_at,
                    title, body_text, labels_json, state, assignment_state, is_locked,
                    comment_count, source_system, source_fetched_at, candidate_eligibility,
                    ineligibility_reasons_json, warnings_json, newcomer_label_signal,
                    feature_definition_version, task_types_json, feature_evidence_json,
                    task_feature_version
                ) VALUES (1, 1, 1, 'https://github.com/example/project/issues/1',
                    '2026-01-01T00:00:00+00:00', 'Fix parser bug',
                    'Expected behavior: parser returns the correct value.', '[]', 'open',
                    'unassigned', 0, 0, 'github_rest', '2026-01-01T00:00:00+00:00',
                    'eligible', '[]', '[]', 0, 'candidate-rules-v0.1', '[]', '{}',
                    'task-features-v0.3')
                """
            )
            connection.commit()
        finally:
            connection.close()
        shutil.copyfile(self.baseline, self.candidate)
        self.expected = file_sha256(self.baseline)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_refresh_preserves_input_snapshot_and_baseline(self) -> None:
        report = refresh_database(
            self.candidate,
            baseline_path=self.baseline,
            expected_baseline_sha256=self.expected,
        )
        self.assertTrue(report["baseline"]["unchanged"])
        self.assertTrue(report["input_snapshot"]["unchanged"])
        self.assertEqual(1, report["candidate_count_refreshed"])
        self.assertNotEqual(report["database"]["sha256_before"], report["database"]["sha256_after"])
        self.assertEqual(self.expected, file_sha256(self.baseline))

    def test_refuses_non_identical_pre_refresh_candidate(self) -> None:
        connection = sqlite3.connect(self.candidate)
        try:
            connection.execute("UPDATE task_candidate SET title = 'changed' WHERE task_candidate_id = 1")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(RuntimeError, "untouched baseline copy"):
            refresh_database(
                self.candidate,
                baseline_path=self.baseline,
                expected_baseline_sha256=self.expected,
            )

    def test_refuses_baseline_as_working_database(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must not be the baseline"):
            refresh_database(
                self.baseline,
                baseline_path=self.baseline,
                expected_baseline_sha256=self.expected,
            )


if __name__ == "__main__":
    unittest.main()
