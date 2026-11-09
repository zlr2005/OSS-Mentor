from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "docs/artifacts/d_midterm_final_20261109"


def _load(name: str) -> dict:
    return json.loads((ARTIFACTS / name).read_text(encoding="utf-8"))


class OnlineAcceptanceArtifactTests(unittest.TestCase):
    def test_online_refresh_explains_extra_preexisting_candidate(self) -> None:
        report = _load("online_refresh.json")

        self.assertEqual(502, report["strict_request_available"])
        self.assertEqual(502, report["cache_compatible_available"])
        self.assertEqual(501, report["availability_counts"]["available"])
        self.assertEqual(501, report["reverified_available_count"])
        self.assertEqual(1, report["preexisting_valid_not_rechecked_count"])
        self.assertEqual(
            7,
            report["preexisting_valid_not_rechecked_candidates"][0][
                "task_candidate_id"
            ],
        )

    def test_distribution_marks_task_types_as_multilabel(self) -> None:
        distribution = _load("distribution.json")

        self.assertTrue(distribution["task_type_counts_are_multilabel"])
        self.assertEqual(
            sum(distribution["task_types"].values()),
            distribution["task_type_assignment_count"],
        )
        self.assertGreater(
            distribution["task_type_assignment_count"],
            distribution["count"],
        )

    def test_explanation_and_profile_state_cover_all_502_candidates(self) -> None:
        summary = _load("explanation_profile_summary.json")
        profile_state = summary["profile_state"]

        self.assertEqual(502, summary["candidate_count"])
        self.assertEqual(502, summary["explanation_complete_count"])
        self.assertEqual(0, summary["explanation_missing_count"])
        self.assertTrue(summary["explanation_complete_all"])
        self.assertEqual(502, profile_state["history_incomplete_count"])
        self.assertEqual(0, profile_state["cold_start_count"])
        self.assertFalse(profile_state["history_complete"])
        self.assertFalse(profile_state["cold_start"])

    def test_expired_cache_prepares_then_recovers_with_new_generation(self) -> None:
        recovery = _load("cache_expiry_recovery.json")

        self.assertEqual("passed", recovery["status"])
        self.assertNotEqual(
            recovery["expired_generation"],
            recovery["worker"]["generation"],
        )
        self.assertEqual(
            ["profile_bootstrap_rules"],
            recovery["preparing"]["scoring_engines"],
        )
        self.assertIn(
            "model_features_preparing",
            recovery["preparing"]["warnings"],
        )
        self.assertEqual(501, recovery["read_back"]["scores"])
        self.assertEqual(501, recovery["read_back"]["unique_task_ids"])
        self.assertEqual(100, recovery["recovered"]["items"])
        self.assertEqual(100, recovery["recovered"]["rankings"])
        self.assertEqual(["coder_gated"], recovery["recovered"]["scoring_engines"])
        self.assertEqual(
            ["prepared_profile_gated"],
            recovery["recovered"]["profile_modes"],
        )


if __name__ == "__main__":
    unittest.main()
