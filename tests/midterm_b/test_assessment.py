from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from oss_mentor.profiling.assessment import (
    build_assessment_preview,
    export_enhanced_features,
    load_questionnaire,
)


ROOT = Path(__file__).resolve().parents[2]
QUESTIONNAIRE_PATH = (
    ROOT / "config" / "midterm_profile" / "questionnaire_v0.1.json"
)
SCENARIOS_PATH = (
    ROOT / "fixtures" / "midterm" / "b" / "assessment_scenarios_v0.1.json"
)


class AssessmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.questionnaire = load_questionnaire(QUESTIONNAIRE_PATH)
        payload = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
        cls.scenarios = {row["id"]: row for row in payload["scenarios"]}

    def preview(self, scenario_id: str) -> dict[str, object]:
        scenario = self.scenarios[scenario_id]
        return build_assessment_preview(
            self.questionnaire,
            scenario["submission"],
            external_evidence=scenario["external_evidence"],
        )

    @staticmethod
    def feature(preview: dict[str, object], feature_name: str) -> dict[str, object]:
        return next(
            row
            for row in preview["features"]  # type: ignore[index]
            if row["feature_name"] == feature_name
        )

    def test_all_six_fixed_scenarios_build(self) -> None:
        self.assertEqual(6, len(self.scenarios))
        for scenario_id in self.scenarios:
            with self.subTest(scenario=scenario_id):
                preview = self.preview(scenario_id)
                self.assertFalse(preview["saved"])
                self.assertEqual(
                    "self_report_and_evidence_estimate_not_verified_ability",
                    preview["interpretation"],
                )

    def test_zero_history_still_produces_preview_and_unknown_mask(self) -> None:
        preview = self.preview("zero_history")
        self.assertIn(
            "current_interest.task.documentation", preview["current_interests"]
        )
        debugging = self.feature(preview, "capability.debugging")
        self.assertFalse(debugging["observed"])
        self.assertEqual(0.0, debugging["proposed_value"])

    def test_answered_categories_distinguish_unselected_from_unknown(self) -> None:
        preview = self.preview("zero_history")
        javascript = self.feature(preview, "preference.language.javascript")
        debugging = self.feature(preview, "capability.debugging")
        self.assertTrue(javascript["observed"])
        self.assertEqual(0.0, javascript["proposed_value"])
        self.assertFalse(debugging["observed"])

    def test_direct_evidence_outranks_self_report(self) -> None:
        preview = self.preview("experienced_contributor")
        code_change = self.feature(preview, "capability.code_change")
        self.assertEqual(0.75, code_change["proposed_value"])
        self.assertEqual("github_direct", code_change["source_kind"])

    def test_current_interest_is_separate_from_historical_evidence(self) -> None:
        preview = self.preview("direction_change")
        self.assertIn("current_interest.task.quality", preview["current_interests"])
        documentation = self.feature(preview, "capability.documentation")
        self.assertEqual("github_direct", documentation["source_kind"])
        self.assertEqual(0.75, documentation["proposed_value"])

    def test_conflict_is_visible_instead_of_silently_averaged(self) -> None:
        preview = self.preview("evidence_conflict")
        code_change = self.feature(preview, "capability.code_change")
        self.assertTrue(code_change["conflict"])
        self.assertEqual(0.25, code_change["proposed_value"])
        self.assertEqual("github_direct", code_change["source_kind"])

    def test_inapplicable_branch_features_remain_unknown(self) -> None:
        preview = self.preview("partial_evidence")
        change_scope = self.feature(preview, "capability.change_scope")
        self.assertFalse(change_scope["observed"])

    def test_manual_lock_blocks_automatic_change(self) -> None:
        preview = self.preview("manual_lock")
        code_change = self.feature(preview, "capability.code_change")
        self.assertEqual(0.25, code_change["proposed_value"])
        self.assertEqual("user_locked", code_change["source_kind"])
        self.assertEqual("field_locked", code_change["blocked_reason"])

    def test_preview_is_deterministic_and_does_not_mutate_inputs(self) -> None:
        scenario = self.scenarios["evidence_conflict"]
        submission = copy.deepcopy(scenario["submission"])
        evidence = copy.deepcopy(scenario["external_evidence"])
        before_submission = copy.deepcopy(submission)
        before_evidence = copy.deepcopy(evidence)
        first = build_assessment_preview(
            self.questionnaire, submission, external_evidence=evidence
        )
        second = build_assessment_preview(
            self.questionnaire, submission, external_evidence=evidence
        )
        self.assertEqual(first, second)
        self.assertEqual(before_submission, submission)
        self.assertEqual(before_evidence, evidence)

    def test_inactive_branch_answer_is_rejected(self) -> None:
        submission = copy.deepcopy(self.scenarios["zero_history"]["submission"])
        submission["answers"]["implementation_scope"] = "one_file"
        with self.assertRaisesRegex(ValueError, "inactive question"):
            build_assessment_preview(self.questionnaire, submission)

    def test_questionnaire_version_mismatch_is_rejected(self) -> None:
        submission = copy.deepcopy(self.scenarios["zero_history"]["submission"])
        submission["questionnaire_version"] = "outdated"
        with self.assertRaisesRegex(ValueError, "does not match"):
            build_assessment_preview(self.questionnaire, submission)

    def test_missing_required_branch_answer_is_rejected(self) -> None:
        submission = copy.deepcopy(self.scenarios["zero_history"]["submission"])
        del submission["answers"]["documentation_validation"]
        with self.assertRaisesRegex(
            ValueError, "missing required answer: documentation_validation"
        ):
            build_assessment_preview(self.questionnaire, submission)

    def test_unknown_external_evidence_feature_is_rejected(self) -> None:
        submission = self.scenarios["zero_history"]["submission"]
        evidence = [
            {
                "feature_name": "capability.invented",
                "value": 1.0,
                "confidence": 1.0,
                "source_kind": "github_direct",
                "evidence_ref": "fixture:invalid",
                "observed_at": "2026-10-18T09:00:00Z",
            }
        ]
        with self.assertRaisesRegex(ValueError, "unknown feature_name"):
            build_assessment_preview(
                self.questionnaire,
                submission,
                external_evidence=evidence,
            )

    def test_conflicting_direct_evidence_is_visible(self) -> None:
        submission = self.scenarios["zero_history"]["submission"]
        evidence = [
            {
                "feature_name": "capability.testing",
                "value": value,
                "confidence": 0.8,
                "source_kind": "github_direct",
                "evidence_ref": f"fixture:direct:{value}",
                "observed_at": "2026-10-18T09:00:00Z",
            }
            for value in (0.25, 0.75)
        ]
        preview = build_assessment_preview(
            self.questionnaire,
            submission,
            external_evidence=evidence,
        )
        self.assertTrue(self.feature(preview, "capability.testing")["conflict"])

    def test_export_preserves_order_and_explicit_missing_mask(self) -> None:
        preview = self.preview("zero_history")
        exported = export_enhanced_features(
            preview,
            profile_key="fixture-zero-history",
            profile_version="profile-v1",
            as_of="2026-10-18T09:00:00Z",
        )
        self.assertEqual(
            len(exported["feature_names"]), len(exported["values"])
        )
        self.assertEqual(
            len(exported["feature_names"]), len(exported["observed_mask"])
        )
        debugging_index = exported["feature_names"].index("capability.debugging")
        self.assertEqual(0.0, exported["values"][debugging_index])
        self.assertFalse(exported["observed_mask"][debugging_index])


if __name__ == "__main__":
    unittest.main()
