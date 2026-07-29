from __future__ import annotations

import unittest

from oss_mentor.task_features import extract_task_features, infer_skill_requirements


class TaskFeatureTests(unittest.TestCase):
    def test_clear_first_contribution_bug_scores_for_newcomer(self) -> None:
        features = extract_task_features(
            {
                "title": "Bug: wrong color returned by parser",
                "body_text": (
                    "## Steps to reproduce\n```python\nparse('red')\n```\n"
                    "## Expected behavior\nThe parser should return red.\n"
                    "## Acceptance criteria\n- [ ] Add a regression test in `tests/test_color.py`."
                ),
                "labels": ["bug", "first-contribution"],
                "comment_count": 2,
                "candidate_eligibility": "eligible",
            }
        )

        self.assertTrue(features.has_reproduction_steps)
        self.assertTrue(features.has_expected_behavior)
        self.assertTrue(features.has_acceptance_criteria)
        self.assertTrue(features.has_affected_module_hint)
        self.assertIn("bug_fix", features.task_types)
        self.assertEqual(
            "label",
            features.feature_evidence["task_type_evidence"]["bug_fix"][0]["source"],
        )
        self.assertGreaterEqual(features.text_clarity_score, 80)
        self.assertGreater(features.newcomer_score, 70)

    def test_real_world_feature_and_bug_phrases_are_classified_with_evidence(
        self,
    ) -> None:
        feature = extract_task_features(
            {
                "title": "Allow batch based metrics calculation",
                "body_text": "",
                "labels": ["kind/feature", "help wanted"],
                "comment_count": 0,
                "candidate_eligibility": "eligible",
            }
        )
        bug = extract_task_features(
            {
                "title": "Export dialog doesn't respect iOS safe area",
                "body_text": "",
                "labels": ["good first issue"],
                "comment_count": 0,
                "candidate_eligibility": "eligible",
            }
        )

        self.assertIn("feature", feature.task_types)
        self.assertIn("bug_fix", bug.task_types)
        self.assertEqual(
            "label",
            feature.feature_evidence["task_type_evidence"]["feature"][0]["source"],
        )
        self.assertEqual(
            "title",
            bug.feature_evidence["task_type_evidence"]["bug_fix"][0]["source"],
        )

    def test_ineligible_candidate_gets_zero_track_scores(self) -> None:
        features = extract_task_features(
            {
                "title": "Core architecture refactor",
                "body_text": "Refactor the core architecture and optimize performance.",
                "labels": ["core", "refactor"],
                "comment_count": 20,
                "candidate_eligibility": "temporarily_ineligible",
            }
        )
        self.assertEqual(0.0, features.newcomer_score)
        self.assertEqual(0.0, features.growth_value_score)
        self.assertEqual(3, features.estimated_code_difficulty)

    def test_title_platform_takes_priority_over_body_comparison(self) -> None:
        record = {
            "title": "Bug in the macOS backend",
            "body_text": "The behavior differs from Linux.",
            "labels": ["GUI: MacOSX", "first-contribution"],
            "comment_count": 1,
            "candidate_eligibility": "eligible",
            "primary_language": "Python",
        }
        features = extract_task_features(record)
        requirements = infer_skill_requirements(record, features)
        platforms = {
            item.skill_name for item in requirements if item.skill_name.startswith("platform:")
        }
        self.assertEqual({"platform:macos"}, platforms)


if __name__ == "__main__":
    unittest.main()
