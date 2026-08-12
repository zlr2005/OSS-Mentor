from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.export_skill_requirement_diagnostics_v0_2 import build_diagnostics, file_sha256


class SkillRequirementDiagnosticsV02Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.database = self.root / "fixture.sqlite3"
        connection = sqlite3.connect(self.database)
        try:
            connection.executescript(
                """
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
                    html_url TEXT,
                    title TEXT,
                    body_text TEXT,
                    labels_json TEXT NOT NULL DEFAULT '[]',
                    task_types_json TEXT NOT NULL DEFAULT '[]',
                    candidate_eligibility TEXT NOT NULL,
                    newcomer_label_signal INTEGER NOT NULL DEFAULT 0,
                    estimated_code_difficulty INTEGER,
                    estimated_setup_difficulty INTEGER,
                    estimated_project_context_difficulty INTEGER,
                    estimated_collaboration_difficulty INTEGER,
                    estimated_effort_bucket TEXT,
                    feature_evidence_json TEXT NOT NULL DEFAULT '{}',
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
            )
            connection.execute("INSERT INTO repository VALUES (1, 'example/project', 'Python', 0, 0)")
            evidence = {
                "skill_requirement_evidence": {
                    "rules_version": "skill-requirements-v0.2",
                    "skills": {
                        "Python": {
                            "normalized_skill_name": "python",
                            "category": "programming_language",
                            "role": "core",
                            "decision": "included",
                            "matching_facing": True,
                            "minimum_level": 1,
                            "importance": 1.0,
                            "requirement_source": "repository_primary_language",
                            "evidence": [{
                                "source": "derived", "rule_id": "skill.language.repository_primary",
                                "matched_value": "Python", "normalized_value": "python",
                                "strength": "strong", "reason": "repository_primary_language"
                            }],
                        },
                        "Docker": {
                            "normalized_skill_name": "docker",
                            "category": "tool",
                            "role": "core",
                            "decision": "included",
                            "matching_facing": True,
                            "minimum_level": 1,
                            "importance": 0.7,
                            "requirement_source": "inferred_tool_requirement",
                            "evidence": [{
                                "source": "title", "rule_id": "skill.tool.docker.title.direct_target",
                                "matched_value": "Docker image", "normalized_value": "docker image",
                                "strength": "strong", "reason": "docker_image_is_direct_task_target"
                            }],
                        },
                    },
                    "rejected": [{
                        "skill_name": "platform:linux", "category": "platform", "source": "body",
                        "rule_id": "skill.platform.body.reporter_environment",
                        "matched_value": "OS: Linux", "normalized_value": "os linux",
                        "strength": "weak", "decision": "rejected_context_only",
                        "matching_facing": False,
                        "reason": "reporter_environment_is_not_task_requirement"
                    }],
                }
            }
            connection.execute(
                """
                INSERT INTO task_candidate VALUES (
                    1, 1, 1, 'https://github.com/example/project/issues/1',
                    'Update Docker image build', 'OS: Linux', '[]', '["build_tooling"]',
                    'eligible', 0, 1, 1, 1, 0, 'half_day', ?, 'task-features-v0.4')
                """,
                (json.dumps(evidence),),
            )
            connection.executemany(
                "INSERT INTO task_skill_requirement VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (1, "Python", 1, 1.0, "repository_primary_language", "task-features-v0.4"),
                    (1, "Docker", 1, 0.7, "inferred_tool_requirement", "task-features-v0.4"),
                ],
            )
            connection.commit()
        finally:
            connection.close()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_diagnostics_are_read_only_and_capture_tool_evidence(self) -> None:
        before = file_sha256(self.database)
        report = build_diagnostics(self.database)
        after = file_sha256(self.database)
        self.assertEqual(before, after)
        self.assertEqual(1, report["scope"]["eligible_candidate_count"])
        self.assertEqual(1, report["production_tool_requirements"]["Docker"]["task_count"])
        self.assertEqual(0, report["evidence_contract"]["anomaly_count"])
        self.assertEqual(0, report["anomaly_queues"]["holdback_leakage_count"])
        self.assertEqual(0, report["anomaly_queues"]["tool_importance_1_count"])

    def test_holdback_skill_is_flagged(self) -> None:
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "INSERT INTO task_skill_requirement VALUES (1, 'npm', 1, 0.5, 'inferred_tool_requirement', 'task-features-v0.4')"
            )
            connection.commit()
        finally:
            connection.close()
        report = build_diagnostics(self.database)
        self.assertEqual(1, report["anomaly_queues"]["holdback_leakage_count"])


if __name__ == "__main__":
    unittest.main()
