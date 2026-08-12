from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.export_skill_requirement_diagnostics import (
    CANDIDATE_SIGNAL_RULES,
    SkillDiagnosticsError,
    _bounded_body_excerpt,
    _nearest_rank,
    build_documents,
    classify_requirement,
    detect_body_only_platform_signals,
    detect_candidate_signals,
    file_sha256,
    requirement_validity,
)


class SkillRequirementDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.database = self.root / "fixture.sqlite3"
        self.profiles = self.root / "profiles.json"
        self._write_profiles()
        self._create_database()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _write_profiles(self) -> None:
        self.profiles.write_text(
            json.dumps(
                {
                    "schema_version": "developer-profiles-v0.1",
                    "profiles": [
                        {
                            "profile_key": "newcomer_python_linux",
                            "display_name": "Newcomer",
                            "service_track": "newcomer",
                            "preferred_languages": ["Python"],
                            "operating_systems": ["linux"],
                            "preferred_task_types": ["bug_fix", "testing"],
                            "max_code_difficulty": 1,
                            "max_setup_difficulty": 2,
                            "desired_skill_stretch": 0,
                            "profile_source": "demo",
                            "skills": {"Python": 1, "testing": 1, "git": 1},
                        },
                        {
                            "profile_key": "growth_python_js",
                            "display_name": "Growth",
                            "service_track": "growth",
                            "preferred_languages": ["Python", "JavaScript"],
                            "operating_systems": ["linux", "windows", "macos"],
                            "preferred_task_types": ["bug_fix", "testing", "build_tooling"],
                            "max_code_difficulty": 3,
                            "max_setup_difficulty": 3,
                            "desired_skill_stretch": 1,
                            "profile_source": "demo",
                            "skills": {
                                "Python": 2,
                                "JavaScript": 2,
                                "testing": 2,
                                "build_tooling": 1,
                                "git": 3,
                            },
                        },
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _create_database(self) -> None:
        connection = sqlite3.connect(self.database)
        try:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE repository (
                    repository_id INTEGER PRIMARY KEY,
                    full_name TEXT NOT NULL UNIQUE,
                    primary_language TEXT,
                    is_archived INTEGER NOT NULL DEFAULT 0,
                    is_disabled INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE task_candidate (
                    task_candidate_id INTEGER PRIMARY KEY,
                    repository_id INTEGER NOT NULL REFERENCES repository(repository_id),
                    issue_number INTEGER NOT NULL,
                    html_url TEXT NOT NULL,
                    title TEXT NOT NULL,
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
                    task_feature_version TEXT
                );
                CREATE TABLE task_skill_requirement (
                    task_candidate_id INTEGER NOT NULL REFERENCES task_candidate(task_candidate_id),
                    skill_name TEXT NOT NULL COLLATE NOCASE,
                    minimum_level INTEGER NOT NULL,
                    importance REAL NOT NULL,
                    requirement_source TEXT NOT NULL,
                    feature_version TEXT NOT NULL
                );
                CREATE TABLE developer_profile (
                    developer_profile_id INTEGER PRIMARY KEY,
                    profile_key TEXT NOT NULL UNIQUE,
                    service_track TEXT NOT NULL,
                    preferred_languages_json TEXT NOT NULL DEFAULT '[]',
                    preferred_task_types_json TEXT NOT NULL DEFAULT '[]',
                    max_code_difficulty INTEGER NOT NULL DEFAULT 3,
                    max_setup_difficulty INTEGER NOT NULL DEFAULT 3,
                    operating_systems_json TEXT NOT NULL
                );
                CREATE TABLE developer_skill (
                    developer_profile_id INTEGER NOT NULL REFERENCES developer_profile(developer_profile_id),
                    skill_name TEXT NOT NULL,
                    skill_level INTEGER NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT INTO repository VALUES (1, 'example/project', 'Python', 0, 0)"
            )
            connection.execute(
                "INSERT INTO repository VALUES (2, 'example/js', 'JavaScript', 0, 0)"
            )
            connection.commit()
        finally:
            connection.close()

    def _insert_task(
        self,
        task_id: int,
        *,
        repository_id: int = 1,
        issue_number: int | None = None,
        title: str = "Fix parser bug",
        body_text: str = "Expected behavior: parser returns the correct value.",
        labels: list[str] | None = None,
        task_types: list[str] | None = None,
        eligibility: str = "eligible",
        newcomer: int = 0,
        code: int = 1,
        setup: int = 1,
        context: int = 1,
        collaboration: int = 0,
        effort: str = "half_day",
        task_feature_version: str = "task-features-v0.3",
        requirements: list[tuple[str, int, float, str, str]] | None = None,
    ) -> None:
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                """
                INSERT INTO task_candidate (
                    task_candidate_id, repository_id, issue_number, html_url, title,
                    body_text, labels_json, task_types_json, candidate_eligibility,
                    newcomer_label_signal, estimated_code_difficulty,
                    estimated_setup_difficulty, estimated_project_context_difficulty,
                    estimated_collaboration_difficulty, estimated_effort_bucket,
                    task_feature_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    repository_id,
                    issue_number or task_id,
                    f"https://github.com/example/project/issues/{issue_number or task_id}",
                    title,
                    body_text,
                    json.dumps(labels or []),
                    json.dumps(task_types or ["bug_fix"]),
                    eligibility,
                    newcomer,
                    code,
                    setup,
                    context,
                    collaboration,
                    effort,
                    task_feature_version,
                ),
            )
            for skill_name, level, importance, source, version in requirements or []:
                connection.execute(
                    """
                    INSERT INTO task_skill_requirement (
                        task_candidate_id, skill_name, minimum_level, importance,
                        requirement_source, feature_version
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (task_id, skill_name, level, importance, source, version),
                )
            connection.commit()
        finally:
            connection.close()

    def _build(self):
        return build_documents(
            self.database,
            self.profiles,
            generated_at="2026-08-12T00:00:00+00:00",
            max_review_candidates=30,
            max_body_chars=300,
        )

    def test_empty_dataset_uses_null_rates(self) -> None:
        diagnostics, review = self._build()
        self.assertEqual(0, diagnostics["scope"]["eligible_candidate_count"])
        self.assertIsNone(diagnostics["skill_coverage"]["coverage_rate"])
        self.assertEqual(0, review["selection_summary"]["selected_count"])

    def test_zero_skill_task_is_not_covered(self) -> None:
        self._insert_task(1, requirements=[])
        diagnostics, _ = self._build()
        self.assertEqual(0, diagnostics["skill_coverage"]["covered_task_count"])
        self.assertEqual(1, diagnostics["skill_coverage"]["no_skill_task_count"])

    def test_primary_language_only_structure(self) -> None:
        self._insert_task(
            1,
            task_types=["other"],
            requirements=[
                ("Python", 1, 1.0, "repository_primary_language", "task-features-v0.3")
            ],
        )
        diagnostics, _ = self._build()
        self.assertEqual(1, diagnostics["baseline_structure"]["only_primary_language"])

    def test_language_plus_task_type_structure(self) -> None:
        self._insert_task(
            1,
            task_types=["testing"],
            requirements=[
                ("Python", 1, 1.0, "repository_primary_language", "task-features-v0.3"),
                ("testing", 1, 0.6, "inferred_task_type", "task-features-v0.3"),
            ],
        )
        diagnostics, _ = self._build()
        self.assertEqual(
            1,
            diagnostics["baseline_structure"]["only_primary_language_plus_task_type"],
        )

    def test_skill_count_statistics_include_p90(self) -> None:
        for task_id, skill_count in enumerate((0, 1, 2, 3, 5), start=1):
            requirements = [
                (
                    f"skill-{index}",
                    1,
                    0.5,
                    "fixture",
                    "task-features-v0.3",
                )
                for index in range(skill_count)
            ]
            self._insert_task(task_id, requirements=requirements)
        diagnostics, _ = self._build()
        self.assertEqual(5, diagnostics["skill_coverage"]["p90_skills_per_task"])
        self.assertEqual(5, diagnostics["skill_coverage"]["max_skills_per_task"])

    def test_nearest_rank_helper(self) -> None:
        self.assertEqual(5, _nearest_rank([0, 1, 2, 3, 5], 0.9))
        self.assertIsNone(_nearest_rank([], 0.9))

    def test_requirement_classification_language_task_type_platform_other(self) -> None:
        vocabulary = {"python"}
        self.assertEqual(
            "programming_language",
            classify_requirement({"skill_name": "Python"}, vocabulary),
        )
        self.assertEqual("task_type", classify_requirement({"skill_name": "testing"}, vocabulary))
        self.assertEqual(
            "platform",
            classify_requirement({"skill_name": "platform:linux"}, vocabulary),
        )
        self.assertEqual("other", classify_requirement({"skill_name": "Docker"}, vocabulary))

    def test_schema_invalid_minimum_level_is_separate_from_generator_outlier(self) -> None:
        invalid = requirement_validity(
            {
                "skill_name": "Python",
                "minimum_level": 5,
                "importance": 1.0,
                "requirement_source": "fixture",
                "feature_version": "v",
            }
        )
        self.assertFalse(invalid["schema_valid"])
        self.assertTrue(invalid["current_generator_contract_outlier"])
        level_zero = requirement_validity(
            {
                "skill_name": "Python",
                "minimum_level": 0,
                "importance": 1.0,
                "requirement_source": "fixture",
                "feature_version": "v",
            }
        )
        self.assertTrue(level_zero["schema_valid"])
        self.assertTrue(level_zero["current_generator_contract_outlier"])

    def test_invalid_importance_is_detected(self) -> None:
        validity = requirement_validity(
            {
                "skill_name": "Python",
                "minimum_level": 1,
                "importance": 0,
                "requirement_source": "fixture",
                "feature_version": "v",
            }
        )
        self.assertFalse(validity["schema_valid"])
        self.assertIn("importance_outside_range_0_1", validity["schema_invalid_reasons"])

    def test_missing_source_and_feature_version_are_detected(self) -> None:
        validity = requirement_validity(
            {
                "skill_name": "Python",
                "minimum_level": 1,
                "importance": 1.0,
                "requirement_source": "",
                "feature_version": "",
            }
        )
        self.assertIn("blank_requirement_source", validity["schema_invalid_reasons"])
        self.assertIn("blank_feature_version", validity["schema_invalid_reasons"])

    def test_platform_namespace_and_plain_platform_misuse(self) -> None:
        self._insert_task(
            1,
            requirements=[
                ("platform:linux", 1, 1.0, "explicit_platform_signal", "task-features-v0.3"),
                ("windows", 1, 1.0, "fixture", "task-features-v0.3"),
            ],
        )
        diagnostics, _ = self._build()
        self.assertEqual(1, diagnostics["platform_diagnostics"]["plain_platform_skill_count"])
        self.assertEqual(
            {"platform:linux": 1},
            diagnostics["platform_diagnostics"]["platform_requirement_distribution"],
        )

    def test_invalid_platform_namespace_is_reported(self) -> None:
        self._insert_task(
            1,
            requirements=[
                ("platform:freebsd", 1, 1.0, "fixture", "task-features-v0.3")
            ],
        )
        diagnostics, _ = self._build()
        self.assertEqual(
            1, diagnostics["platform_diagnostics"]["invalid_platform_namespace_count"]
        )

    def test_multiple_platform_requirements_are_a_review_signal(self) -> None:
        self._insert_task(
            1,
            requirements=[
                ("platform:linux", 1, 1.0, "fixture", "task-features-v0.3"),
                ("platform:windows", 1, 1.0, "fixture", "task-features-v0.3"),
            ],
        )
        diagnostics, _ = self._build()
        self.assertEqual(
            1, diagnostics["platform_diagnostics"]["multi_platform_requirement_task_count"]
        )

    def test_body_only_platform_candidate_extraction(self) -> None:
        record = {
            "title": "Parser returns wrong result",
            "labels": ["bug"],
            "body_text": "The behavior differs from Linux in our report.",
        }
        detected = detect_body_only_platform_signals(record)
        self.assertEqual("linux", detected[0]["platform"])

    def test_title_platform_prevents_same_platform_from_body_only_queue(self) -> None:
        record = {
            "title": "Bug on Linux",
            "labels": [],
            "body_text": "The Linux version also appears in the logs.",
        }
        self.assertEqual([], detect_body_only_platform_signals(record))

    def test_cli_does_not_trigger_ci_candidate_signal(self) -> None:
        signals = detect_candidate_signals(
            {
                "title": "Generate CLI for the REST API",
                "labels": [],
                "body_text": "",
            }
        )
        names = {item["signal_name"] for item in signals}
        self.assertNotIn("CI", names)
        self.assertIn("REST API", names)

    def test_candidate_tool_title_signal_is_explicit(self) -> None:
        signals = detect_candidate_signals(
            {
                "title": "Migrate CI workflow to GitHub Actions",
                "labels": [],
                "body_text": "",
            }
        )
        github = next(item for item in signals if item["signal_name"] == "GitHub Actions")
        self.assertEqual("title_or_label_explicit", github["strength"])

    def test_candidate_tool_body_only_signal_is_contextual_without_action(self) -> None:
        signals = detect_candidate_signals(
            {
                "title": "Unexpected startup failure",
                "labels": [],
                "body_text": "Environment: Kubernetes 1.30. Logs are attached below.",
            }
        )
        kubernetes = next(item for item in signals if item["signal_name"] == "Kubernetes")
        self.assertEqual("body_only_contextual", kubernetes["strength"])

    def test_candidate_tool_body_signal_can_be_explicit(self) -> None:
        signals = detect_candidate_signals(
            {
                "title": "Update deployment setup",
                "labels": [],
                "body_text": "Please migrate the deployment to Kubernetes and update manifests.",
            }
        )
        kubernetes = next(item for item in signals if item["signal_name"] == "Kubernetes")
        self.assertEqual("body_explicit", kubernetes["strength"])

    def test_candidate_lexicon_is_small_and_unique(self) -> None:
        names = [rule.name.casefold() for rule in CANDIDATE_SIGNAL_RULES]
        self.assertEqual(len(names), len(set(names)))
        self.assertLessEqual(len(names), 25)

    def test_profile_vocabulary_compatibility_flags_unknown_skill(self) -> None:
        self._insert_task(
            1,
            title="Build image with Docker",
            requirements=[
                ("Python", 1, 1.0, "repository_primary_language", "task-features-v0.3")
            ],
        )
        diagnostics, _ = self._build()
        compatibility = {
            item["candidate_skill"]: item
            for item in diagnostics["profile_vocabulary_compatibility"][
                "candidate_signal_compatibility"
            ]
        }
        self.assertTrue(compatibility["Docker"]["potential_unknown_skill_risk"])

    def test_known_profile_skill_is_not_unknown(self) -> None:
        self._insert_task(
            1,
            requirements=[
                ("testing", 1, 0.6, "inferred_task_type", "task-features-v0.3")
            ],
        )
        diagnostics, _ = self._build()
        unknown = diagnostics["profile_vocabulary_compatibility"][
            "eligible_requirement_unknown_to_all_profiles_count"
        ]
        self.assertEqual(0, unknown)

    def test_importance_one_unknown_skill_creates_static_risk_without_ranking(self) -> None:
        self._insert_task(
            1,
            requirements=[
                ("Docker", 1, 1.0, "fixture", "task-features-v0.3")
            ],
        )
        diagnostics, _ = self._build()
        risk = diagnostics["matching_static_risk"]
        self.assertFalse(risk["matching_rankings_computed"])
        self.assertGreater(risk["raw_potential_critical_mismatch_pair_count"], 0)

    def test_requirement_source_distribution(self) -> None:
        self._insert_task(
            1,
            requirements=[
                ("Python", 1, 1.0, "repository_primary_language", "task-features-v0.3"),
                ("testing", 1, 0.6, "inferred_task_type", "task-features-v0.3"),
            ],
        )
        diagnostics, _ = self._build()
        sources = {
            item["requirement_source"]: item["requirement_count"]
            for item in diagnostics["requirement_source_distribution"]
        }
        self.assertEqual(1, sources["repository_primary_language"])
        self.assertEqual(1, sources["inferred_task_type"])

    def test_task_type_skill_composition_uses_public_type(self) -> None:
        self._insert_task(
            1,
            task_types=["testing"],
            requirements=[
                ("Python", 1, 1.0, "repository_primary_language", "task-features-v0.3"),
                ("testing", 1, 0.6, "inferred_task_type", "task-features-v0.3"),
            ],
        )
        diagnostics, _ = self._build()
        self.assertEqual(1, diagnostics["task_type_skill_composition"]["testing"]["task_count"])

    def test_review_candidate_set_is_deterministic(self) -> None:
        for task_id, title in (
            (1, "Migrate CI workflow to GitHub Actions"),
            (2, "Run tests with pytest"),
            (3, "Docker image build cleanup"),
        ):
            self._insert_task(
                task_id,
                title=title,
                task_types=["build_tooling" if task_id != 2 else "testing"],
                requirements=[
                    ("Python", 1, 1.0, "repository_primary_language", "task-features-v0.3")
                ],
            )
        first, first_review = self._build()
        second, second_review = self._build()
        self.assertEqual(first, second)
        self.assertEqual(first_review, second_review)

    def test_body_excerpt_is_bounded_and_removes_code_fence(self) -> None:
        excerpt = _bounded_body_excerpt(
            "prefix ```python\nprint('x')\n``` " + "word " * 100,
            120,
        )
        self.assertLessEqual(len(excerpt), 120)
        self.assertIn("[code block omitted]", excerpt)
        self.assertNotIn("print('x')", excerpt)

    def test_database_hash_is_unchanged_by_build(self) -> None:
        before = file_sha256(self.database)
        diagnostics, _ = self._build()
        after = file_sha256(self.database)
        self.assertEqual(before, after)
        self.assertTrue(diagnostics["database"]["unchanged"])

    def test_archived_and_ineligible_tasks_do_not_enter_eligible_scope(self) -> None:
        self._insert_task(1, eligibility="excluded", requirements=[])
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("UPDATE repository SET is_archived = 1 WHERE repository_id = 2")
            connection.commit()
        finally:
            connection.close()
        self._insert_task(2, repository_id=2, requirements=[])
        diagnostics, _ = self._build()
        self.assertEqual(0, diagnostics["scope"]["eligible_candidate_count"])

    def test_schema_mismatch_fails_loudly(self) -> None:
        broken = self.root / "broken.sqlite3"
        connection = sqlite3.connect(broken)
        try:
            connection.execute("CREATE TABLE repository (repository_id INTEGER PRIMARY KEY)")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(SkillDiagnosticsError):
            build_documents(
                broken,
                self.profiles,
                generated_at="2026-08-12T00:00:00+00:00",
            )

    def test_malformed_candidate_json_is_reported_not_silently_skipped(self) -> None:
        self._insert_task(1, requirements=[])
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "UPDATE task_candidate SET labels_json = '{bad json' WHERE task_candidate_id = 1"
            )
            connection.commit()
        finally:
            connection.close()
        diagnostics, _ = self._build()
        self.assertEqual(1, diagnostics["data_integrity"]["candidate_json_anomaly_count"])
        anomaly = diagnostics["data_integrity"]["candidate_json_anomalies"][0]
        self.assertEqual("invalid_json", anomaly["labels_status"])

    def test_documents_are_json_serializable(self) -> None:
        self._insert_task(
            1,
            requirements=[
                ("Python", 1, 1.0, "repository_primary_language", "task-features-v0.3")
            ],
        )
        diagnostics, review = self._build()
        json.dumps(diagnostics, ensure_ascii=False, sort_keys=True)
        json.dumps(review, ensure_ascii=False, sort_keys=True)


if __name__ == "__main__":
    unittest.main()