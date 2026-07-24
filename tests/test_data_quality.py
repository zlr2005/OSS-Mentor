from __future__ import annotations

import json
import unittest

from oss_mentor.data_quality import build_data_quality_report, render_data_quality_markdown


class DataQualityTests(unittest.TestCase):
    def _record(self, **overrides: object) -> dict[str, object]:
        record: dict[str, object] = {
            "task_candidate_id": 1,
            "repository": "example/project",
            "primary_language": "Python",
            "is_archived": 0,
            "is_disabled": 0,
            "issue_number": 10,
            "html_url": "https://github.com/example/project/issues/10",
            "title": "Fix incorrect parser result",
            "body_text": "Steps to reproduce the bug and expected result.",
            "labels_json": json.dumps(["bug"]),
            "state": "open",
            "assignment_state": "unassigned",
            "has_linked_open_pr": 0,
            "last_activity_at": "2026-07-20T00:00:00+00:00",
            "github_verified_at": "2026-07-20T00:00:00+00:00",
            "candidate_eligibility": "eligible",
            "newcomer_label_signal": 0,
            "task_types_json": json.dumps(["bug_fix"]),
            "text_clarity_score": 80.0,
            "estimated_code_difficulty": 1,
            "estimated_setup_difficulty": 1,
            "estimated_project_context_difficulty": 1,
            "estimated_collaboration_difficulty": 0,
            "estimated_effort_bucket": "half_day",
            "novice_fit_probability": 0.7,
            "newcomer_score": 70.0,
            "growth_value_score": 45.0,
            "feature_evidence_json": json.dumps({"formula_version": "task-features-v0.1"}),
            "feature_extracted_at": "2026-07-20T01:00:00+00:00",
            "task_feature_version": "task-features-v0.1",
            "requirements": [
                {
                    "skill_name": "Python",
                    "minimum_level": 1,
                    "importance": 1.0,
                    "requirement_source": "repository_primary_language",
                    "feature_version": "task-features-v0.1",
                },
                {
                    "skill_name": "testing",
                    "minimum_level": 1,
                    "importance": 0.6,
                    "requirement_source": "inferred_task_type",
                    "feature_version": "task-features-v0.1",
                },
            ],
        }
        record.update(overrides)
        return record

    def test_complete_record_passes_core_quality_checks(self) -> None:
        report = build_data_quality_report(
            [self._record()], generated_at="2026-07-22T00:00:00+00:00"
        )
        quality = report["quality_by_scope"]["eligible_candidates"]

        self.assertEqual(1, report["scope_summary"]["eligible_candidates"]["total_count"])
        self.assertEqual(0.0, quality["input_completeness"]["body_text"]["missing_rate"])
        self.assertEqual(1.0, quality["task_type_quality"]["coverage_rate"])
        self.assertEqual(1.0, quality["skill_requirement_quality"]["coverage_rate"])
        self.assertEqual(1.0, quality["difficulty_quality"]["valid_rate"])
        self.assertTrue(report["acceptance_summary"]["overall_passed"])

    def test_empty_input_uses_null_rates_and_markdown_na(self) -> None:
        report = build_data_quality_report(
            [], generated_at="2026-07-22T00:00:00+00:00"
        )
        quality = report["quality_by_scope"]["eligible_candidates"]

        self.assertIsNone(quality["task_type_quality"]["coverage_rate"])
        self.assertIsNone(quality["skill_requirement_quality"]["coverage_rate"])
        self.assertIsNone(quality["difficulty_quality"]["valid_rate"])
        self.assertIn("N/A", render_data_quality_markdown(report))

    def test_missing_body_language_and_github_verification_are_counted(self) -> None:
        report = build_data_quality_report(
            [
                self._record(
                    body_text="   ",
                    primary_language="unknown",
                    github_verified_at=None,
                )
            ]
        )
        completeness = report["quality_by_scope"]["eligible_candidates"][
            "input_completeness"
        ]

        self.assertEqual(1, completeness["body_text"]["missing_count"])
        self.assertEqual(1, completeness["primary_language"]["missing_count"])
        self.assertEqual(1, completeness["github_verification"]["missing_count"])

    def test_task_type_boundaries_follow_public_contract(self) -> None:
        raw_values = [
            ["bug_fix"],
            ["other"],
            ["performance"],
            ["other", "performance"],
            ["bug_fix", "other"],
            ["refactor", "performance"],
            [],
        ]
        records = [
            self._record(task_candidate_id=index, issue_number=index, task_types_json=json.dumps(value))
            for index, value in enumerate(raw_values, start=1)
        ]
        records.append(
            self._record(task_candidate_id=20, issue_number=20, task_types_json="{bad json")
        )
        records.append(
            self._record(task_candidate_id=21, issue_number=21, task_types_json=json.dumps({"bug_fix": True}))
        )

        report = build_data_quality_report(records)
        quality = report["quality_by_scope"]["eligible_candidates"]["task_type_quality"]

        self.assertEqual(3, quality["recognized_count"])
        self.assertEqual(1, quality["other_only_count"])
        self.assertEqual(3, quality["unsupported_only_count"])
        self.assertEqual(2, quality["mixed_supported_unsupported_count"])
        self.assertEqual(3, quality["field_missing_count"])
        self.assertEqual(2, quality["invalid_field_count"])

    def test_other_is_not_counted_as_recognized_task_type(self) -> None:
        report = build_data_quality_report(
            [self._record(task_types_json=json.dumps(["other"]))]
        )
        quality = report["quality_by_scope"]["eligible_candidates"]["task_type_quality"]

        self.assertEqual(0, quality["recognized_count"])
        self.assertEqual(1, quality["other_only_count"])
        self.assertEqual(0.0, quality["coverage_rate"])

    def test_missing_and_invalid_requirements_do_not_count_as_coverage(self) -> None:
        records = [
            self._record(task_candidate_id=1, requirements=[]),
            self._record(
                task_candidate_id=2,
                issue_number=11,
                requirements=[
                    {
                        "skill_name": "",
                        "minimum_level": 5,
                        "importance": 0,
                        "requirement_source": "",
                        "feature_version": "",
                    }
                ],
            ),
            self._record(task_candidate_id=3, issue_number=12),
        ]

        report = build_data_quality_report(records)
        quality = report["quality_by_scope"]["eligible_candidates"][
            "skill_requirement_quality"
        ]

        self.assertEqual(1, quality["covered_count"])
        self.assertEqual(2, quality["missing_count"])
        self.assertEqual(1, quality["invalid_requirement_task_count"])

    def test_platform_and_feature_version_problems_are_reported(self) -> None:
        requirements = [
            {
                "skill_name": "platform:windows",
                "minimum_level": 1,
                "importance": 1.0,
                "requirement_source": "explicit_platform_signal",
                "feature_version": "task-features-v0.1",
            },
            {
                "skill_name": "platform:android",
                "minimum_level": 1,
                "importance": 1.0,
                "requirement_source": "explicit_platform_signal",
                "feature_version": "task-features-v0.1",
            },
            {
                "skill_name": "linux",
                "minimum_level": 1,
                "importance": 1.0,
                "requirement_source": "inferred_task_type",
                "feature_version": "task-features-v0.1",
            },
        ]
        report = build_data_quality_report(
            [
                self._record(
                    requirements=requirements,
                    task_feature_version="task-features-v0.2",
                )
            ]
        )
        quality = report["quality_by_scope"]["eligible_candidates"][
            "skill_requirement_quality"
        ]

        self.assertEqual(1, quality["platform_requirement_task_count"])
        self.assertEqual(1, quality["invalid_platform_requirement_count"])
        self.assertEqual(1, quality["plain_platform_skill_count"])
        self.assertEqual(1, quality["feature_version_mismatch_count"])

    def test_difficulty_missing_and_invalid_values_are_separate(self) -> None:
        records = [
            self._record(task_candidate_id=1),
            self._record(
                task_candidate_id=2,
                issue_number=11,
                estimated_code_difficulty=None,
            ),
            self._record(
                task_candidate_id=3,
                issue_number=12,
                estimated_setup_difficulty=4,
                estimated_effort_bucket="two_days",
            ),
            self._record(
                task_candidate_id=4,
                issue_number=13,
                estimated_project_context_difficulty=1.5,
            ),
            self._record(
                task_candidate_id=5,
                issue_number=14,
                estimated_collaboration_difficulty=True,
            ),
        ]

        report = build_data_quality_report(records)
        quality = report["quality_by_scope"]["eligible_candidates"]["difficulty_quality"]

        self.assertEqual(4, quality["complete_count"])
        self.assertEqual(1, quality["valid_count"])
        self.assertEqual(1, quality["missing_count"])
        self.assertEqual(3, quality["invalid_count"])
        self.assertEqual(1, quality["by_field"]["estimated_code_difficulty"]["missing_count"])
        self.assertEqual(1, quality["by_field"]["estimated_setup_difficulty"]["invalid_count"])
        self.assertEqual(1, quality["by_field"]["estimated_effort_bucket"]["invalid_count"])

    def test_scope_filters_exclude_archived_disabled_and_ineligible_records(self) -> None:
        records = [
            self._record(task_candidate_id=1, newcomer_label_signal=1),
            self._record(task_candidate_id=2, issue_number=11, is_archived=1),
            self._record(task_candidate_id=3, issue_number=12, is_disabled=1),
            self._record(
                task_candidate_id=4,
                issue_number=13,
                candidate_eligibility="excluded",
            ),
            self._record(task_candidate_id=5, issue_number=14),
        ]

        report = build_data_quality_report(records)
        scopes = report["scope_summary"]

        self.assertEqual(5, scopes["all_candidates"]["total_count"])
        self.assertEqual(3, scopes["active_candidates"]["total_count"])
        self.assertEqual(2, scopes["eligible_candidates"]["total_count"])
        self.assertEqual(1, scopes["newcomer_eligible_candidates"]["total_count"])

    def test_invalid_evidence_json_and_scores_are_reported(self) -> None:
        report = build_data_quality_report(
            [
                self._record(
                    feature_evidence_json="[]",
                    text_clarity_score=101,
                    novice_fit_probability=True,
                )
            ]
        )
        quality = report["quality_by_scope"]["eligible_candidates"]["feature_quality"]

        self.assertEqual(1, quality["feature_evidence_missing_count"])
        self.assertEqual(1, quality["feature_score_invalid_count"])
        self.assertEqual(1, quality["score_fields"]["text_clarity_score"]["invalid_count"])
        self.assertEqual(1, quality["score_fields"]["novice_fit_probability"]["invalid_count"])

    def test_anomaly_samples_are_limited_stable_and_do_not_include_body(self) -> None:
        records = [
            self._record(
                task_candidate_id=index,
                issue_number=index,
                repository=f"project/{12 - index:02d}",
                body_text="",
                task_types_json=json.dumps(["other"]),
            )
            for index in range(1, 13)
        ]

        report = build_data_quality_report(records, sample_limit=3)
        samples = report["anomalies"]["body_text_missing_samples"]

        self.assertEqual(3, len(samples))
        self.assertTrue(all("body_text" not in sample for sample in samples))
        self.assertEqual(
            sorted(sample["repository"] for sample in samples),
            [sample["repository"] for sample in samples],
        )

    def test_markdown_contains_main_sections_and_escapes_table_cells(self) -> None:
        report = build_data_quality_report(
            [self._record(title="Parser | incorrect result")],
            generated_at="2026-07-22T00:00:00+00:00",
        )
        markdown = render_data_quality_markdown(report)

        self.assertIn("# OSS-Mentor 数据质量报告 v0.2", markdown)
        self.assertIn("## 4. 任务类型质量", markdown)
        self.assertIn("## 5. 技能要求质量", markdown)
        self.assertIn("## 6. 难度质量", markdown)

    def test_rejects_invalid_input_and_sample_limit(self) -> None:
        with self.assertRaises(TypeError):
            build_data_quality_report(["not a mapping"])  # type: ignore[list-item]
        with self.assertRaises(ValueError):
            build_data_quality_report([], sample_limit=-1)


if __name__ == "__main__":
    unittest.main()