from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from oss_mentor.profiling.assessment import build_assessment_preview, load_questionnaire
from oss_mentor.profiling.confirmation import (
    build_confirmation_plan,
    build_feature_state,
    confirm_assessment_plan,
    set_feature_lock,
)


ROOT = Path(__file__).resolve().parents[2]
QUESTIONNAIRE = ROOT / "config" / "midterm_profile" / "questionnaire_v0.1.json"
ASSESSMENT_SCENARIOS = (
    ROOT / "fixtures" / "midterm" / "b" / "assessment_scenarios_v0.1.json"
)
CONFIRMATION_SCENARIOS = (
    ROOT / "fixtures" / "midterm" / "b" / "confirmation_scenarios_v0.1.json"
)


class ConfirmationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.questionnaire = load_questionnaire(QUESTIONNAIRE)
        assessment_payload = json.loads(ASSESSMENT_SCENARIOS.read_text(encoding="utf-8"))
        cls.assessments = {row["id"]: row for row in assessment_payload["scenarios"]}
        confirmation_payload = json.loads(
            CONFIRMATION_SCENARIOS.read_text(encoding="utf-8")
        )
        cls.confirmed_at = confirmation_payload["confirmed_at"]
        cls.scenarios = {row["id"]: row for row in confirmation_payload["scenarios"]}

    def preview_for(self, scenario: dict) -> dict:
        source = self.assessments[scenario["assessment_scenario"]]
        return build_assessment_preview(
            self.questionnaire,
            source["submission"],
            external_evidence=source["external_evidence"],
        )

    def state_for(self, scenario: dict, preview: dict) -> dict:
        return build_feature_state(
            preview,
            profile_key=f"fixture:{scenario['id']}",
            profile_version="profile:v1",
            updated_at="2026-10-18T07:00:00Z",
            feature_overrides=scenario["feature_overrides"],
        )

    @staticmethod
    def suggestion_for(plan: dict, feature_name: str) -> dict:
        return next(
            row for row in plan["suggestions"] if row["feature_name"] == feature_name
        )

    def decisions_for(self, scenario: dict, plan: dict) -> dict[str, str]:
        if scenario.get("decision_mode") in {"accept_all", "reject_all"}:
            decision = "accept" if scenario["decision_mode"] == "accept_all" else "reject"
            return {
                row["suggestion_id"]: decision
                for row in plan["suggestions"]
                if row["status"] == "pending"
            }
        decisions: dict[str, str] = {}
        for row in scenario.get("decisions", []):
            if "suggestion_id" in row:
                decisions[row["suggestion_id"]] = row["decision"]
            else:
                suggestion = self.suggestion_for(plan, row["feature_name"])
                decisions[suggestion["suggestion_id"]] = row["decision"]
        return decisions

    @staticmethod
    def state_feature(state: dict, feature_name: str) -> dict:
        return next(row for row in state["features"] if row["feature_name"] == feature_name)

    def test_fixture_covers_required_confirmation_scenarios(self) -> None:
        self.assertEqual(
            {
                "accept_all",
                "reject_all",
                "partial_decision",
                "explicit_conflict_acceptance",
                "current_field_locked",
                "preview_field_locked",
                "stale_profile_version",
                "duplicate_confirmation",
                "unknown_suggestion",
                "unknown_feature_preserved",
                "unlock_then_accept",
            },
            set(self.scenarios),
        )

    def test_accept_all_applies_pending_suggestions_and_preserves_unknown(self) -> None:
        scenario = self.scenarios["accept_all"]
        preview = self.preview_for(scenario)
        state = self.state_for(scenario, preview)
        plan = build_confirmation_plan(preview, state)
        decisions = self.decisions_for(scenario, plan)
        result = confirm_assessment_plan(
            plan, state, decisions, confirmed_at=self.confirmed_at
        )
        self.assertEqual(set(decisions), set(result["applied_suggestion_ids"]))
        self.assertEqual([], result["remaining_suggestion_ids"])
        self.assertNotEqual("profile:v1", result["new_profile_version"])
        debugging = self.state_feature(result["profile_state"], "capability.debugging")
        self.assertFalse(debugging["observed"])
        self.assertEqual(0.0, debugging["value"])
        self.assertFalse(result["persisted"])

    def test_reject_all_keeps_original_feature_values(self) -> None:
        scenario = self.scenarios["reject_all"]
        preview = self.preview_for(scenario)
        state = self.state_for(scenario, preview)
        plan = build_confirmation_plan(preview, state)
        result = confirm_assessment_plan(
            plan,
            state,
            self.decisions_for(scenario, plan),
            confirmed_at=self.confirmed_at,
        )
        self.assertEqual([], result["applied_suggestion_ids"])
        self.assertEqual([], result["remaining_suggestion_ids"])
        for before, after in zip(state["features"], result["profile_state"]["features"]):
            self.assertEqual(before, after)

    def test_partial_decision_keeps_unhandled_suggestions_pending(self) -> None:
        scenario = self.scenarios["partial_decision"]
        preview = self.preview_for(scenario)
        state = self.state_for(scenario, preview)
        plan = build_confirmation_plan(preview, state)
        decisions = self.decisions_for(scenario, plan)
        result = confirm_assessment_plan(
            plan, state, decisions, confirmed_at=self.confirmed_at
        )
        quality = self.state_feature(
            result["profile_state"], "current_interest.task.quality"
        )
        documentation = self.state_feature(
            result["profile_state"], "capability.documentation"
        )
        self.assertTrue(quality["observed"])
        self.assertFalse(documentation["observed"])
        self.assertGreater(len(result["remaining_suggestion_ids"]), 0)

    def test_conflict_requires_and_records_an_explicit_decision(self) -> None:
        scenario = self.scenarios["explicit_conflict_acceptance"]
        preview = self.preview_for(scenario)
        state = self.state_for(scenario, preview)
        plan = build_confirmation_plan(preview, state)
        suggestion = self.suggestion_for(plan, "capability.code_change")
        self.assertTrue(suggestion["conflict"])
        result = confirm_assessment_plan(
            plan,
            state,
            self.decisions_for(scenario, plan),
            confirmed_at=self.confirmed_at,
        )
        code_change = self.state_feature(result["profile_state"], "capability.code_change")
        self.assertEqual(0.25, code_change["value"])
        record = next(
            row for row in result["decisions"] if row["feature_name"] == "capability.code_change"
        )
        self.assertTrue(record["conflict_was_visible"])

    def test_current_lock_blocks_automatic_overwrite(self) -> None:
        scenario = self.scenarios["current_field_locked"]
        preview = self.preview_for(scenario)
        state = self.state_for(scenario, preview)
        plan = build_confirmation_plan(preview, state)
        suggestion = self.suggestion_for(plan, "capability.code_change")
        self.assertEqual("blocked", suggestion["status"])
        self.assertEqual("field_locked", suggestion["blocked_reason"])
        result = confirm_assessment_plan(
            plan,
            state,
            self.decisions_for(scenario, plan),
            confirmed_at=self.confirmed_at,
        )
        code_change = self.state_feature(result["profile_state"], "capability.code_change")
        self.assertEqual(0.25, code_change["value"])
        self.assertTrue(code_change["locked"])
        self.assertIn(suggestion["suggestion_id"], result["blocked_suggestion_ids"])
        with self.assertRaisesRegex(ValueError, "blocked suggestions cannot be decided"):
            confirm_assessment_plan(
                plan,
                state,
                {suggestion["suggestion_id"]: "accept"},
                confirmed_at=self.confirmed_at,
            )

    def test_preview_lock_also_blocks_a_suggestion(self) -> None:
        scenario = self.scenarios["preview_field_locked"]
        preview = self.preview_for(scenario)
        state = self.state_for(scenario, preview)
        plan = build_confirmation_plan(preview, state)
        suggestion = self.suggestion_for(plan, "capability.code_change")
        self.assertEqual("blocked", suggestion["status"])
        self.assertEqual("field_locked", suggestion["blocked_reason"])

    def test_stale_profile_version_is_rejected(self) -> None:
        scenario = self.scenarios["stale_profile_version"]
        preview = self.preview_for(scenario)
        state = self.state_for(scenario, preview)
        plan = build_confirmation_plan(preview, state)
        changed_state = copy.deepcopy(state)
        changed_state["profile_version"] = "profile:v2"
        with self.assertRaisesRegex(ValueError, scenario["expected_error"]):
            confirm_assessment_plan(
                plan,
                changed_state,
                self.decisions_for(scenario, plan),
                confirmed_at=self.confirmed_at,
            )

    def test_duplicate_confirmation_is_deterministic_and_non_mutating(self) -> None:
        scenario = self.scenarios["duplicate_confirmation"]
        preview = self.preview_for(scenario)
        state = self.state_for(scenario, preview)
        plan = build_confirmation_plan(preview, state)
        decisions = self.decisions_for(scenario, plan)
        before_plan = copy.deepcopy(plan)
        before_state = copy.deepcopy(state)
        first = confirm_assessment_plan(
            plan, state, decisions, confirmed_at=self.confirmed_at
        )
        second = confirm_assessment_plan(
            plan, state, decisions, confirmed_at=self.confirmed_at
        )
        self.assertEqual(first, second)
        self.assertEqual(before_plan, plan)
        self.assertEqual(before_state, state)

    def test_unknown_suggestion_id_is_rejected(self) -> None:
        scenario = self.scenarios["unknown_suggestion"]
        preview = self.preview_for(scenario)
        state = self.state_for(scenario, preview)
        plan = build_confirmation_plan(preview, state)
        with self.assertRaisesRegex(ValueError, scenario["expected_error"]):
            confirm_assessment_plan(
                plan,
                state,
                self.decisions_for(scenario, plan),
                confirmed_at=self.confirmed_at,
            )

    def test_unknown_non_applicable_feature_remains_unknown(self) -> None:
        scenario = self.scenarios["unknown_feature_preserved"]
        preview = self.preview_for(scenario)
        state = self.state_for(scenario, preview)
        plan = build_confirmation_plan(preview, state)
        result = confirm_assessment_plan(
            plan,
            state,
            self.decisions_for(scenario, plan),
            confirmed_at=self.confirmed_at,
        )
        feature = self.state_feature(result["profile_state"], "capability.change_scope")
        self.assertFalse(feature["observed"])
        self.assertEqual(0.0, feature["value"])

    def test_unlock_then_rebuild_plan_allows_acceptance(self) -> None:
        scenario = self.scenarios["unlock_then_accept"]
        preview = self.preview_for(scenario)
        state = self.state_for(scenario, preview)
        initial_plan = build_confirmation_plan(preview, state)
        initial = self.suggestion_for(initial_plan, scenario["feature_name"])
        self.assertEqual("blocked", initial["status"])
        unlocked = set_feature_lock(
            state,
            feature_name=scenario["feature_name"],
            locked=False,
            expected_profile_version=state["profile_version"],
            updated_at="2026-10-18T09:30:00Z",
        )
        plan = build_confirmation_plan(preview, unlocked)
        suggestion = self.suggestion_for(plan, scenario["feature_name"])
        self.assertEqual("pending", suggestion["status"])
        result = confirm_assessment_plan(
            plan,
            unlocked,
            {suggestion["suggestion_id"]: "accept"},
            confirmed_at=self.confirmed_at,
        )
        feature = self.state_feature(result["profile_state"], scenario["feature_name"])
        self.assertFalse(feature["locked"])
        self.assertEqual(0.75, feature["value"])


if __name__ == "__main__":
    unittest.main()
