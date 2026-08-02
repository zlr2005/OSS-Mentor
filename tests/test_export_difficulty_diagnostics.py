from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.export_difficulty_diagnostics import (
    DEFAULT_OUTPUT_PATH,
    DIAGNOSTIC_SCHEMA_VERSION,
    build_difficulty_diagnostics,
    build_parser,
    connect_readonly,
    expected_effort_bucket,
    legacy_sum_effort_bucket,
    load_database_snapshot,
    write_diagnostics_report,
)


FIXED_TIME = "2026-07-28T00:00:00+00:00"


class ExportDifficultyDiagnosticsTests(unittest.TestCase):
    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _create_database(path: Path, records: list[dict[str, object]]) -> None:
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
                    body_text TEXT,
                    labels_json TEXT,
                    task_types_json TEXT,
                    newcomer_label_signal INTEGER,
                    feature_evidence_json TEXT,
                    candidate_eligibility TEXT,
                    comment_count INTEGER,
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

                CREATE TABLE task_skill_requirement (
                    task_candidate_id INTEGER NOT NULL,
                    skill_name TEXT NOT NULL,
                    minimum_level INTEGER NOT NULL
                );
                """
            )
            repositories = sorted(
                {
                    (
                        int(record.get("repository_id", 1)),
                        str(record.get("repository", "example/project")),
                        str(record.get("primary_language", "Python")),
                        int(record.get("is_archived", 0)),
                        int(record.get("is_disabled", 0)),
                    )
                    for record in records
                }
            )
            connection.executemany(
                """
                INSERT INTO repository (
                    repository_id, full_name, primary_language,
                    is_archived, is_disabled
                ) VALUES (?, ?, ?, ?, ?)
                """,
                repositories,
            )
            for record in records:
                connection.execute(
                    """
                    INSERT INTO task_candidate (
                        task_candidate_id, repository_id, issue_number,
                        html_url, title, body_text, labels_json,
                        task_types_json, newcomer_label_signal,
                        feature_evidence_json, candidate_eligibility,
                        comment_count, text_clarity_score,
                        estimated_code_difficulty,
                        estimated_setup_difficulty,
                        estimated_project_context_difficulty,
                        estimated_collaboration_difficulty,
                        estimated_effort_bucket,
                        novice_fit_probability, newcomer_score,
                        growth_value_score, task_feature_version
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        record["task_candidate_id"],
                        record.get("repository_id", 1),
                        record.get("issue_number", record["task_candidate_id"]),
                        record.get("html_url", ""),
                        record.get("title", "Issue"),
                        record.get("body_text", "Body"),
                        record.get("labels_json", "[]"),
                        record.get("task_types_json", '["bug_fix"]'),
                        record.get("newcomer_label_signal", 0),
                        record.get("feature_evidence_json", "{}"),
                        record.get("candidate_eligibility", "eligible"),
                        record.get("comment_count", 0),
                        record.get("text_clarity_score", 50.0),
                        record.get("estimated_code_difficulty", 1),
                        record.get("estimated_setup_difficulty", 1),
                        record.get("estimated_project_context_difficulty", 1),
                        record.get("estimated_collaboration_difficulty", 0),
                        record.get("estimated_effort_bucket", "half_day"),
                        record.get("novice_fit_probability", 0.5),
                        record.get("newcomer_score", 50.0),
                        record.get("growth_value_score", 50.0),
                        record.get("task_feature_version", "task-features-v0.2"),
                    ),
                )
                for skill_name, minimum_level in record.get("skills", []):
                    connection.execute(
                        """
                        INSERT INTO task_skill_requirement (
                            task_candidate_id, skill_name, minimum_level
                        ) VALUES (?, ?, ?)
                        """,
                        (
                            record["task_candidate_id"],
                            skill_name,
                            minimum_level,
                        ),
                    )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _record(task_id: int, **overrides: object) -> dict[str, object]:
        record: dict[str, object] = {
            "task_candidate_id": task_id,
            "repository_id": 1,
            "repository": "example/project",
            "primary_language": "Python",
            "issue_number": task_id,
            "html_url": f"https://github.com/example/project/issues/{task_id}",
            "title": f"Issue {task_id}",
            "body_text": f"Private body secret {task_id}",
            "labels_json": '["bug"]',
            "task_types_json": '["bug_fix"]',
            "newcomer_label_signal": 0,
            "feature_evidence_json": '{"auxiliary_signals":{"performance":[]}}',
            "candidate_eligibility": "eligible",
            "comment_count": 0,
            "text_clarity_score": 50.0,
            "estimated_code_difficulty": 1,
            "estimated_setup_difficulty": 1,
            "estimated_project_context_difficulty": 1,
            "estimated_collaboration_difficulty": 0,
            "estimated_effort_bucket": "half_day",
            "novice_fit_probability": 0.5,
            "newcomer_score": 50.0,
            "growth_value_score": 50.0,
            "skills": [("Python", 1)],
        }
        record.update(overrides)
        return record

    @staticmethod
    def _assessment(
        *,
        code: int = 1,
        setup: int = 1,
        project_context: int = 1,
        collaboration: int = 0,
        actionability: str = "actionable",
        information_confidence: str = "medium",
        body_missing: bool = False,
        information_reasons: list[str] | None = None,
        effort_bucket: str = "half_day",
        effort_scope: str = "local",
        applicable: bool = True,
        provisional: bool = False,
        effort_confidence: str = "medium",
        dimension_evidence: dict[str, list[dict[str, object]]] | None = None,
        dimension_confidence: dict[str, str] | None = None,
        dimension_conflicts: dict[str, list[dict[str, object]]] | None = None,
        formula_version: str = "difficulty-rules-v0.2",
    ) -> dict[str, object]:
        levels = {
            "code": code,
            "setup": setup,
            "project_context": project_context,
            "collaboration": collaboration,
        }
        priors = {
            "code": 1,
            "setup": 1,
            "project_context": 1,
            "collaboration": 0,
        }
        dimension_evidence = dimension_evidence or {}
        dimension_confidence = dimension_confidence or {}
        dimension_conflicts = dimension_conflicts or {}
        return {
            "formula_version": formula_version,
            "information_quality": {
                "body_missing": body_missing,
                "actionability": actionability,
                "confidence": information_confidence,
                "reasons": information_reasons or [],
            },
            "dimensions": {
                name: {
                    "prior": priors[name],
                    "level": level,
                    "confidence": dimension_confidence.get(name, "medium"),
                    "evidence": dimension_evidence.get(name, []),
                    "conflicts": dimension_conflicts.get(name, []),
                }
                for name, level in levels.items()
            },
            "effort": {
                "bucket": effort_bucket,
                "scope": effort_scope,
                "applicable": applicable,
                "provisional": provisional,
                "confidence": effort_confidence,
                "evidence": [
                    {
                        "source": "derived",
                        "rule_id": f"effort.scope.{effort_scope}",
                        "matched_value": effort_scope,
                        "reason": "test_fixture_scope",
                    }
                ],
            },
        }

    @classmethod
    def _record_with_assessment(
        cls,
        task_id: int,
        *,
        assessment: dict[str, object] | None = None,
        task_types: list[str] | None = None,
        performance: bool = False,
        **overrides: object,
    ) -> dict[str, object]:
        task_types = task_types or ["bug_fix"]
        evidence: dict[str, object] = {
            "auxiliary_signals": {
                "performance": [
                    {"rule_id": "performance.test"}
                ]
                if performance
                else []
            },
            "task_type_evidence": {
                task_type: [{"source": "label", "rule_id": "test.task_type"}]
                for task_type in task_types
                if task_type != "other"
            },
            "difficulty_assessment": assessment or cls._assessment(),
        }
        levels = evidence["difficulty_assessment"]["dimensions"]  # type: ignore[index]
        effort = evidence["difficulty_assessment"]["effort"]  # type: ignore[index]
        defaults: dict[str, object] = {
            "task_types_json": json.dumps(task_types),
            "feature_evidence_json": json.dumps(evidence),
            "estimated_code_difficulty": levels["code"]["level"],  # type: ignore[index]
            "estimated_setup_difficulty": levels["setup"]["level"],  # type: ignore[index]
            "estimated_project_context_difficulty": levels["project_context"]["level"],  # type: ignore[index]
            "estimated_collaboration_difficulty": levels["collaboration"]["level"],  # type: ignore[index]
            "estimated_effort_bucket": effort["bucket"],  # type: ignore[index]
            "task_feature_version": "task-features-v0.3",
        }
        defaults.update(overrides)
        return cls._record(task_id, **defaults)

    def test_sqlite_is_opened_read_only_and_hash_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "source.sqlite3"
            self._create_database(database, [self._record(1)])
            before = self._sha256(database)

            connection = connect_readonly(database)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute(
                        "UPDATE task_candidate SET title='changed' "
                        "WHERE task_candidate_id=1"
                    )
            finally:
                connection.close()

            snapshot = load_database_snapshot(database)
            self.assertEqual(1, len(snapshot["records"]))
            self.assertEqual(before, self._sha256(database))

    def test_stable_report_and_json_output(self) -> None:
        records = [self._record(2), self._record(1)]
        first = build_difficulty_diagnostics(
            records,
            generated_at=FIXED_TIME,
            after_database_path="after.sqlite3",
        )
        second = build_difficulty_diagnostics(
            list(reversed(records)),
            generated_at=FIXED_TIME,
            after_database_path="after.sqlite3",
        )
        self.assertEqual(first, second)

        with tempfile.TemporaryDirectory() as temporary:
            first_path = write_diagnostics_report(
                first, Path(temporary) / "first.json"
            )
            second_path = write_diagnostics_report(
                second, Path(temporary) / "second.json"
            )
            self.assertEqual(first_path.read_bytes(), second_path.read_bytes())

    def test_empty_database_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "empty.sqlite3"
            self._create_database(database, [])
            snapshot = load_database_snapshot(database)
            report = build_difficulty_diagnostics(
                snapshot["records"],
                after_skills=snapshot["skills"],
                generated_at=FIXED_TIME,
            )
            self.assertEqual(
                0,
                report["after"]["summaries"]["eligible"]["record_count"],
            )
            self.assertEqual(
                0,
                report["after"]["summaries"]["eligible"][
                    "difficulty_tuples"
                ]["valid_count"],
            )

    def test_malformed_json_is_counted_without_crashing(self) -> None:
        records = [
            self._record(
                1,
                labels_json='["bug"',
                task_types_json='{"bug_fix":true}',
                feature_evidence_json="[]",
            )
        ]
        report = build_difficulty_diagnostics(records, generated_at=FIXED_TIME)
        malformed = report["after"]["summaries"]["eligible"]["malformed_json"]
        self.assertEqual(1, malformed["labels_invalid_count"])
        self.assertEqual(1, malformed["task_types_invalid_count"])
        self.assertEqual(1, malformed["feature_evidence_invalid_count"])

    def test_eligible_scope_and_newcomer_scope_are_separate(self) -> None:
        records = [
            self._record(1, newcomer_label_signal=1),
            self._record(2, candidate_eligibility="excluded"),
        ]
        report = build_difficulty_diagnostics(records, generated_at=FIXED_TIME)
        counts = report["record_counts"]
        self.assertEqual(2, counts["after_all_active_candidates"])
        self.assertEqual(1, counts["after_eligible_candidates"])
        self.assertEqual(1, counts["after_newcomer_eligible_candidates"])

    def test_report_never_leaks_body_text(self) -> None:
        secret = "TOP-SECRET-BODY-CONTENT"
        report = build_difficulty_diagnostics(
            [self._record(1, body_text=secret)], generated_at=FIXED_TIME
        )
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(secret, serialized)
        self.assertNotIn('"body_text"', serialized)

    def test_distribution_totals_are_conserved(self) -> None:
        records = [
            self._record(1, estimated_code_difficulty=0),
            self._record(2, estimated_code_difficulty=1),
            self._record(3, estimated_code_difficulty=3),
        ]
        report = build_difficulty_diagnostics(records, generated_at=FIXED_TIME)
        eligible = report["after"]["summaries"]["eligible"]
        code = eligible["difficulty_dimensions"]["code"]
        self.assertEqual(3, sum(code["level_counts"].values()))
        self.assertEqual(3, code["valid_count"])
        self.assertEqual(
            3,
            eligible["effort_bucket"]["total"],
        )
        self.assertEqual(
            3,
            eligible["difficulty_tuples"]["valid_count"],
        )

    def test_legacy_effort_comparison_is_not_consistency_validation(self) -> None:
        same = self._record(
            1,
            estimated_code_difficulty=0,
            estimated_setup_difficulty=0,
            estimated_project_context_difficulty=1,
            estimated_collaboration_difficulty=0,
            estimated_effort_bucket="under_2h",
        )
        different = self._record(
            2,
            estimated_code_difficulty=3,
            estimated_setup_difficulty=2,
            estimated_project_context_difficulty=3,
            estimated_collaboration_difficulty=2,
            estimated_effort_bucket="half_day",
        )
        self.assertEqual("under_2h", legacy_sum_effort_bucket(same))
        self.assertEqual("multi_day", legacy_sum_effort_bucket(different))
        self.assertEqual("multi_day", expected_effort_bucket(different))

        report = build_difficulty_diagnostics(
            [same, different], generated_at=FIXED_TIME
        )
        comparison = report["after"]["summaries"]["eligible"][
            "legacy_effort_comparison"
        ]
        self.assertEqual(2, comparison["comparable_count"])
        self.assertEqual(1, comparison["different_from_legacy_count"])
        self.assertIn("differences_are_not_errors", comparison["semantic"])
        anomalies = report["after"]["eligible_analysis"]["anomalies"]
        self.assertNotIn("effort_sum_mismatch", anomalies)

    def test_baseline_and_after_align_by_task_candidate_id(self) -> None:
        baseline = [
            self._record(1, estimated_code_difficulty=1),
            self._record(
                2,
                task_types_json='["bug_fix"]',
                estimated_code_difficulty=1,
                estimated_effort_bucket="half_day",
            ),
        ]
        after = [
            self._record(
                2,
                task_types_json='["feature"]',
                estimated_code_difficulty=2,
                estimated_effort_bucket="one_day",
            ),
            self._record(3),
        ]
        report = build_difficulty_diagnostics(
            after,
            baseline_records=baseline,
            baseline_skills={2: {"Python": 1}},
            after_skills={2: {"Python": 2}},
            generated_at=FIXED_TIME,
        )
        comparison = report["baseline_to_after"]
        self.assertEqual(1, comparison["common_eligible_count"])
        self.assertEqual([1], comparison["baseline_only_task_candidate_ids"])
        self.assertEqual([3], comparison["after_only_task_candidate_ids"])
        self.assertEqual(1, comparison["task_type_changes"]["count"])
        self.assertEqual(1, comparison["difficulty_changes"]["count"])
        self.assertEqual(1, comparison["effort_changes"]["count"])
        self.assertEqual(1, comparison["skill_minimum_level_changes"]["count"])
        changed_id = comparison["task_type_changes"]["records"][0][
            "task_candidate_id"
        ]
        self.assertEqual(2, changed_id)

    def test_body_only_setup_and_other_anomalies_have_no_body(self) -> None:
        record = self._record(
            1,
            title="Neutral issue",
            body_text="The behavior differs from Linux in the backend.",
            task_types_json='["other"]',
            estimated_setup_difficulty=2,
            estimated_collaboration_difficulty=2,
            estimated_effort_bucket="multi_day",
            comment_count=12,
        )
        report = build_difficulty_diagnostics([record], generated_at=FIXED_TIME)
        anomalies = report["after"]["eligible_analysis"]["anomalies"]
        self.assertEqual(1, anomalies["setup_two_body_only_keyword"]["count"])
        self.assertEqual(1, anomalies["other_multi_day"]["count"])
        serialized = json.dumps(anomalies, ensure_ascii=False)
        self.assertNotIn("differs from Linux", serialized)


    def test_v02_schema_and_default_output(self) -> None:
        self.assertEqual("difficulty_diagnostics_v0.2", DIAGNOSTIC_SCHEMA_VERSION)
        self.assertEqual(
            Path("data/reports/difficulty_diagnostics_v0.2.json"),
            DEFAULT_OUTPUT_PATH,
        )
        args = build_parser().parse_args([])
        self.assertEqual(str(DEFAULT_OUTPUT_PATH), args.output)
        report = build_difficulty_diagnostics([], generated_at=FIXED_TIME)
        self.assertEqual(DIAGNOSTIC_SCHEMA_VERSION, report["schema_version"])

    def test_readonly_connections_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "readonly.sqlite3"
            self._create_database(database, [self._record(1)])
            before = self._sha256(database)
            connection = connect_readonly(database)
            try:
                query_only = connection.execute("PRAGMA query_only").fetchone()[0]
                self.assertEqual(1, query_only)
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("DELETE FROM task_candidate")
            finally:
                connection.close()
            self.assertEqual(before, self._sha256(database))

    def test_old_records_without_difficulty_assessment_are_supported(self) -> None:
        report = build_difficulty_diagnostics(
            [self._record(1)], generated_at=FIXED_TIME
        )
        status = report["after"]["summaries"]["eligible"][
            "difficulty_assessment"
        ]["status"]["counts"]
        self.assertEqual(1, status["missing"])
        self.assertEqual(0, status["invalid"])

    def test_valid_difficulty_assessment_is_parsed(self) -> None:
        record = self._record_with_assessment(1)
        report = build_difficulty_diagnostics([record], generated_at=FIXED_TIME)
        summary = report["after"]["summaries"]["eligible"][
            "difficulty_assessment"
        ]
        self.assertEqual(1, summary["status"]["counts"]["ok"])
        self.assertEqual(
            1,
            summary["formula_version_distribution"]["counts"][
                "difficulty-rules-v0.2"
            ],
        )

    def test_invalid_difficulty_assessment_is_recorded(self) -> None:
        invalid = self._assessment()
        invalid["dimensions"]["code"]["level"] = 9  # type: ignore[index]
        record = self._record_with_assessment(1, assessment=invalid)
        report = build_difficulty_diagnostics([record], generated_at=FIXED_TIME)
        summary = report["after"]["summaries"]["eligible"][
            "difficulty_assessment"
        ]
        self.assertEqual(1, summary["status"]["counts"]["invalid"])
        self.assertEqual(
            1,
            summary["invalid_reason_counts"]["dimension_code_level_invalid"],
        )

    def test_actionability_distribution(self) -> None:
        records = [
            self._record_with_assessment(
                1, assessment=self._assessment(actionability="actionable")
            ),
            self._record_with_assessment(
                2,
                assessment=self._assessment(
                    actionability="design_pending",
                    information_confidence="medium",
                ),
            ),
        ]
        report = build_difficulty_diagnostics(records, generated_at=FIXED_TIME)
        counts = report["after"]["summaries"]["eligible"][
            "difficulty_assessment"
        ]["information_quality"]["actionability_distribution"]["counts"]
        self.assertEqual(1, counts["actionable"])
        self.assertEqual(1, counts["design_pending"])

    def test_information_confidence_distribution(self) -> None:
        records = [
            self._record_with_assessment(
                1, assessment=self._assessment(information_confidence="high")
            ),
            self._record_with_assessment(
                2, assessment=self._assessment(information_confidence="low")
            ),
        ]
        report = build_difficulty_diagnostics(records, generated_at=FIXED_TIME)
        counts = report["after"]["summaries"]["eligible"][
            "difficulty_assessment"
        ]["information_quality"]["confidence_distribution"]["counts"]
        self.assertEqual(1, counts["high"])
        self.assertEqual(1, counts["low"])

    def test_dimension_confidence_and_evidence_distribution(self) -> None:
        code_evidence = {
            "code": [
                {
                    "dimension": "code",
                    "source": "title",
                    "rule_id": "difficulty.code.nontrivial_logic",
                    "matched_value": "state machine",
                    "strength": "medium",
                    "suggested_level": 2,
                    "reason": "nontrivial_implementation_logic",
                }
            ]
        }
        assessment = self._assessment(
            code=2,
            dimension_evidence=code_evidence,
            dimension_confidence={"code": "high"},
            effort_bucket="one_day",
            effort_scope="module",
        )
        report = build_difficulty_diagnostics(
            [self._record_with_assessment(1, assessment=assessment)],
            generated_at=FIXED_TIME,
        )
        code = report["after"]["summaries"]["eligible"][
            "difficulty_assessment"
        ]["dimensions"]["code"]
        self.assertEqual(1, code["confidence_distribution"]["counts"]["high"])
        self.assertEqual(1, code["evidence_count_distribution"]["counts"]["1"])

    def test_effort_scope_and_applicability_distribution(self) -> None:
        records = [
            self._record_with_assessment(
                1,
                assessment=self._assessment(
                    effort_bucket="one_day", effort_scope="module"
                ),
            ),
            self._record_with_assessment(
                2,
                assessment=self._assessment(
                    actionability="non_actionable",
                    information_confidence="low",
                    effort_bucket="multi_day",
                    effort_scope="non_actionable",
                    applicable=False,
                    provisional=True,
                    effort_confidence="low",
                ),
            ),
        ]
        report = build_difficulty_diagnostics(records, generated_at=FIXED_TIME)
        effort = report["after"]["summaries"]["eligible"][
            "difficulty_assessment"
        ]["effort"]
        self.assertEqual(1, effort["scope_distribution"]["counts"]["module"])
        self.assertEqual(
            1, effort["scope_distribution"]["counts"]["non_actionable"]
        )
        self.assertEqual(1, effort["applicable_distribution"]["counts"]["false"])

    def test_non_actionable_effort_contract(self) -> None:
        valid = self._assessment(
            actionability="non_actionable",
            information_confidence="low",
            effort_bucket="multi_day",
            effort_scope="non_actionable",
            applicable=False,
            provisional=True,
            effort_confidence="low",
        )
        invalid_contract = self._assessment(
            actionability="non_actionable",
            information_confidence="low",
            effort_bucket="multi_day",
            effort_scope="non_actionable",
            applicable=False,
            provisional=False,
            effort_confidence="medium",
        )
        records = [
            self._record_with_assessment(1, assessment=valid, task_types=["other"]),
            self._record_with_assessment(
                2, assessment=invalid_contract, task_types=["other"]
            ),
        ]
        report = build_difficulty_diagnostics(records, generated_at=FIXED_TIME)
        checks = report["after"]["eligible_analysis"]["difficulty_v02_checks"]
        self.assertEqual(2, checks["non_actionable_effort"]["count"])
        self.assertEqual(
            1, checks["effort_not_applicable_contract"]["count"]
        )

    def test_setup_reported_environment_only_queue(self) -> None:
        evidence = {
            "setup": [
                {
                    "dimension": "setup",
                    "source": "body",
                    "rule_id": "difficulty.setup.reported_environment_only",
                    "matched_value": "Environment: Windows",
                    "strength": "weak",
                    "suggested_level": 1,
                    "reason": "reported_environment_is_not_requirement",
                }
            ]
        }
        assessment = self._assessment(
            setup=2,
            dimension_evidence=evidence,
            effort_bucket="one_day",
            effort_scope="module",
        )
        report = build_difficulty_diagnostics(
            [self._record_with_assessment(1, assessment=assessment)],
            generated_at=FIXED_TIME,
        )
        queue = report["after"]["eligible_analysis"]["difficulty_v02_checks"][
            "setup_reported_environment_only"
        ]
        self.assertEqual(1, queue["count"])

    def test_collaboration_comment_only_above_one_queue(self) -> None:
        evidence = {
            "collaboration": [
                {
                    "dimension": "collaboration",
                    "source": "derived",
                    "rule_id": "difficulty.collaboration.comment_volume",
                    "matched_value": "20",
                    "strength": "weak",
                    "suggested_level": 1,
                    "reason": "comment_volume_is_weak_coordination_signal",
                }
            ]
        }
        assessment = self._assessment(
            collaboration=2,
            dimension_evidence=evidence,
            effort_bucket="one_day",
            effort_scope="module",
        )
        report = build_difficulty_diagnostics(
            [self._record_with_assessment(1, assessment=assessment, comment_count=20)],
            generated_at=FIXED_TIME,
        )
        queue = report["after"]["eligible_analysis"]["difficulty_v02_checks"][
            "collaboration_comment_only_above_one"
        ]
        self.assertEqual(1, queue["count"])

    def test_level_three_requires_strong_evidence_queue(self) -> None:
        evidence = {
            "code": [
                {
                    "dimension": "code",
                    "source": "derived",
                    "rule_id": "difficulty.code.performance_auxiliary",
                    "matched_value": "performance",
                    "strength": "weak",
                    "suggested_level": 3,
                    "reason": "performance_signal_requires_supporting_technical_evidence",
                }
            ]
        }
        assessment = self._assessment(
            code=3,
            dimension_evidence=evidence,
            effort_bucket="one_day",
            effort_scope="module",
        )
        report = build_difficulty_diagnostics(
            [self._record_with_assessment(1, assessment=assessment)],
            generated_at=FIXED_TIME,
        )
        queue = report["after"]["eligible_analysis"]["difficulty_v02_checks"][
            "dimension_level_three_without_strong_evidence"
        ]["code"]
        self.assertEqual(1, queue["count"])

    def test_performance_uniform_hard_three_queue(self) -> None:
        strong = {
            "code": [
                {
                    "dimension": "code",
                    "source": "title",
                    "rule_id": "difficulty.code.core_architecture",
                    "matched_value": "core architecture",
                    "strength": "strong",
                    "suggested_level": 3,
                    "reason": "core_architecture_change",
                }
            ],
            "project_context": [
                {
                    "dimension": "project_context",
                    "source": "title",
                    "rule_id": "difficulty.context.core_architecture",
                    "matched_value": "core architecture",
                    "strength": "strong",
                    "suggested_level": 3,
                    "reason": "core_architecture_context",
                }
            ],
        }
        assessment = self._assessment(
            code=3,
            project_context=3,
            dimension_evidence=strong,
            effort_bucket="multi_day",
            effort_scope="system",
        )
        report = build_difficulty_diagnostics(
            [
                self._record_with_assessment(
                    1, assessment=assessment, performance=True
                )
            ],
            generated_at=FIXED_TIME,
        )
        queue = report["after"]["eligible_analysis"]["difficulty_v02_checks"][
            "performance_uniform_hard_three"
        ]
        self.assertEqual(1, queue["count"])

    def test_task_type_internal_stratification(self) -> None:
        records = [
            self._record_with_assessment(
                1,
                task_types=["testing"],
                assessment=self._assessment(code=1),
            ),
            self._record_with_assessment(
                2,
                task_types=["testing"],
                assessment=self._assessment(
                    code=2, effort_bucket="one_day", effort_scope="module"
                ),
            ),
        ]
        report = build_difficulty_diagnostics(records, generated_at=FIXED_TIME)
        group = report["after"]["eligible_analysis"]["difficulty_v02_checks"][
            "task_type_internal_stratification"
        ]["testing_only"]
        self.assertEqual(2, group["record_count"])
        self.assertEqual(1, group["difficulty"]["code"]["level_counts"]["1"])
        self.assertEqual(1, group["difficulty"]["code"]["level_counts"]["2"])

    def test_body_missing_high_confidence_queue(self) -> None:
        assessment = self._assessment(
            body_missing=True,
            information_confidence="high",
            actionability="unclear",
        )
        report = build_difficulty_diagnostics(
            [self._record_with_assessment(1, assessment=assessment, body_text="")],
            generated_at=FIXED_TIME,
        )
        queue = report["after"]["eligible_analysis"]["difficulty_v02_checks"][
            "body_missing_high_confidence"
        ]
        self.assertEqual(1, queue["count"])

    def test_unclear_high_confidence_queue(self) -> None:
        assessment = self._assessment(
            actionability="unclear", information_confidence="high"
        )
        report = build_difficulty_diagnostics(
            [self._record_with_assessment(1, assessment=assessment)],
            generated_at=FIXED_TIME,
        )
        queue = report["after"]["eligible_analysis"]["difficulty_v02_checks"][
            "unclear_high_confidence"
        ]
        self.assertEqual(1, queue["count"])

    def test_external_and_evidence_effort_bucket_match(self) -> None:
        record = self._record_with_assessment(
            1,
            assessment=self._assessment(
                effort_bucket="one_day", effort_scope="module"
            ),
            estimated_effort_bucket="half_day",
        )
        report = build_difficulty_diagnostics([record], generated_at=FIXED_TIME)
        comparison = report["after"]["summaries"]["eligible"][
            "difficulty_assessment"
        ]["effort"]["evidence_bucket_external_comparison"]
        self.assertEqual(1, comparison["mismatch_count"])

    def test_task_type_evidence_regression_queue(self) -> None:
        evidence = {
            "auxiliary_signals": {"performance": []},
            "difficulty_assessment": self._assessment(),
        }
        record = self._record(
            1,
            task_types_json='["bug_fix"]',
            feature_evidence_json=json.dumps(evidence),
            task_feature_version="task-features-v0.3",
        )
        report = build_difficulty_diagnostics([record], generated_at=FIXED_TIME)
        queue = report["after"]["eligible_analysis"]["difficulty_v02_checks"][
            "task_type_evidence_regression"
        ]["missing_public_task_type_evidence"]
        self.assertEqual(1, queue["count"])

    def test_comparison_includes_direction_and_static_matching_risk(self) -> None:
        baseline = [
            self._record(
                1,
                estimated_code_difficulty=1,
                estimated_setup_difficulty=1,
                estimated_effort_bucket="half_day",
            )
        ]
        after = [
            self._record_with_assessment(
                1,
                assessment=self._assessment(
                    code=3,
                    setup=2,
                    effort_bucket="multi_day",
                    effort_scope="system",
                ),
            )
        ]
        report = build_difficulty_diagnostics(
            after, baseline_records=baseline, generated_at=FIXED_TIME
        )
        comparison = report["baseline_to_after"]
        self.assertEqual(
            1,
            comparison["difficulty_changes"]["by_dimension_direction"]["code"][
                "upgraded"
            ],
        )
        self.assertEqual(
            1, comparison["matching_static_risk"]["code_increase_count"]
        )
        self.assertEqual(
            1, comparison["matching_static_risk"]["setup_increase_count"]
        )
        self.assertEqual(
            1, comparison["difficulty_information_quality_added"]["count"]
        )

    def test_report_is_deterministic_and_json_serializable(self) -> None:
        records = [
            self._record_with_assessment(2),
            self._record_with_assessment(1),
        ]
        first = build_difficulty_diagnostics(records, generated_at=FIXED_TIME)
        second = build_difficulty_diagnostics(
            list(reversed(records)), generated_at=FIXED_TIME
        )
        self.assertEqual(first, second)
        json.dumps(first, ensure_ascii=False, sort_keys=True)


if __name__ == "__main__":
    unittest.main()