from __future__ import annotations

import json
import unittest
from dataclasses import fields
from pathlib import Path

from oss_mentor.contracts_midterm import (
    AssessmentConfirmation,
    AssessmentSubmission,
    ConfirmationPlan,
    EnhancedProfileFeatures,
    IdempotencyConflictError,
    ProfileEvidence,
    ProfileFeatureState,
    ProfilePreview,
    StalePlanError,
)
from oss_mentor.profiling.assessment import (
    build_assessment_preview,
    load_questionnaire,
)
from oss_mentor.profiling.confirmation import (
    build_confirmation_plan,
    build_feature_state,
    confirm_assessment_plan,
)
from oss_mentor.profiling.delivery import build_confirmed_feature_snapshot
from oss_mentor.storage.base import MidtermProfileStore


ROOT = Path(__file__).resolve().parents[2]
QUESTIONNAIRE = ROOT / "config/midterm_profile/questionnaire_v0.1.json"
SCENARIOS = ROOT / "fixtures/midterm/b/assessment_scenarios_v0.1.json"


class MidtermContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.questionnaire = load_questionnaire(QUESTIONNAIRE)
        payload = json.loads(SCENARIOS.read_text(encoding="utf-8"))
        cls.scenario = next(
            row for row in payload["scenarios"] if row["id"] == "zero_history"
        )
        cls.preview_payload = build_assessment_preview(
            cls.questionnaire,
            cls.scenario["submission"],
            external_evidence=cls.scenario["external_evidence"],
        )

    def preview(self) -> ProfilePreview:
        return ProfilePreview.from_private(
            self.preview_payload,
            preview_id="preview:contract-test",
            profile_key="profile:contract-test",
            base_profile_version="profile:v1",
        )

    def test_profile_evidence_round_trip_preserves_audit_details(self) -> None:
        evidence = ProfileEvidence.from_dict(
            {
                "feature_name": "capability.testing",
                "value": 0.5,
                "confidence": 0.7,
                "source_kind": "github_direct",
                "evidence_ref": "github:commit:123",
                "observed_at": "2026-10-26T01:02:03+08:00",
                "commit_sha": "1" * 40,
                "file_version_key": "file-version:test-1",
            }
        )
        restored = ProfileEvidence.from_dict(evidence.to_dict())
        self.assertEqual(evidence, restored)
        self.assertEqual("2026-10-25T17:02:03Z", restored.observed_at)
        self.assertEqual("1" * 40, restored.details["commit_sha"])
        self.assertEqual("file-version:test-1", restored.details["file_version_key"])

    def test_submission_has_no_owner_or_developer_key_fields(self) -> None:
        names = {item.name for item in fields(AssessmentSubmission)}
        self.assertNotIn("user_id", names)
        self.assertNotIn("developer_key", names)
        self.assertNotIn("profile_key", names)
        submission = AssessmentSubmission.from_dict(
            {
                "questionnaire_id": "oss-mentor-midterm-profile",
                "questionnaire_version": "2026-10-18.v1",
                "submitted_at": "2026-10-26T00:00:00Z",
                "answers": {"current_focus": "quality"},
            }
        )
        self.assertEqual("quality", submission.to_dict()["answers"]["current_focus"])

    def test_private_preview_maps_to_public_preview(self) -> None:
        preview = self.preview()
        self.assertEqual("preview:contract-test", preview.preview_id)
        self.assertEqual("profile:contract-test", preview.profile_key)
        self.assertEqual("profile:v1", preview.base_profile_version)
        self.assertTrue(preview.persisted)
        self.assertEqual(
            sorted(item.feature_name for item in preview.features),
            [item.feature_name for item in preview.features],
        )

    def test_plan_and_confirmation_round_trip_through_contracts(self) -> None:
        preview = self.preview()
        state_payload = build_feature_state(
            self.preview_payload,
            profile_key=preview.profile_key,
            profile_version=preview.base_profile_version,
            updated_at="2026-10-25T00:00:00Z",
        )
        state = ProfileFeatureState.from_private(state_payload)
        plan_payload = build_confirmation_plan(self.preview_payload, state_payload)
        plan = ConfirmationPlan.from_private(
            plan_payload,
            preview_id=preview.preview_id,
            created_at="2026-10-26T00:00:00Z",
        )
        decisions = {
            item.suggestion_id: "accept"
            for item in plan.suggestions
            if item.status == "pending"
        }
        result_payload = confirm_assessment_plan(
            plan_payload,
            state_payload,
            decisions,
            confirmed_at="2026-10-26T00:01:00Z",
        )
        result = AssessmentConfirmation.from_private(result_payload)
        self.assertEqual(plan.plan_id, result.plan_id)
        self.assertEqual("profile:v1", result.base_profile_version)
        self.assertEqual(result.new_profile_version, result.profile_state.profile_version)
        self.assertEqual(
            set(decisions),
            {item.suggestion_id for item in result.decisions},
        )

    def test_confirmed_features_map_to_c_contract(self) -> None:
        preview = self.preview()
        state_payload = build_feature_state(
            self.preview_payload,
            profile_key=preview.profile_key,
            profile_version=preview.base_profile_version,
            updated_at="2026-10-25T00:00:00Z",
        )
        plan_payload = build_confirmation_plan(self.preview_payload, state_payload)
        decisions = {
            item["suggestion_id"]: "accept"
            for item in plan_payload["suggestions"]
            if item["status"] == "pending"
        }
        result = confirm_assessment_plan(
            plan_payload,
            state_payload,
            decisions,
            confirmed_at="2026-10-26T00:02:00Z",
        )
        snapshot = build_confirmed_feature_snapshot(
            result["profile_state"],
            as_of="2026-10-26T00:02:00Z",
        )
        features = EnhancedProfileFeatures.from_private(
            snapshot,
            developer_key=preview.profile_key,
        )
        self.assertEqual(preview.profile_key, features.developer_key)
        self.assertEqual(len(features.feature_names), len(features.values))
        self.assertTrue(all(
            observed or value == 0.0
            for observed, value in zip(features.observed_mask, features.values)
        ))

    def test_storage_protocol_exposes_frozen_operations(self) -> None:
        for name in (
            "transaction",
            "put_questionnaire",
            "create_assessment_submission",
            "create_profile_preview",
            "create_confirmation_plan",
            "get_current_feature_state",
            "find_confirmation_by_idempotency_key",
            "commit_assessment_confirmation",
            "get_enhanced_profile_features",
        ):
            self.assertTrue(hasattr(MidtermProfileStore, name), name)
        self.assertEqual("state_conflict", StalePlanError.code)
        self.assertEqual("state_conflict", IdempotencyConflictError.code)

    def test_migration_freeze_reserves_012_for_b(self) -> None:
        document = (ROOT / "docs/midterm_s0_contract_freeze.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("012_midterm_profile_assessment.sql", document)
        self.assertIn("must not merge", document)
        self.assertIn("011_midterm_datasets.sql", document)


if __name__ == "__main__":
    unittest.main()
