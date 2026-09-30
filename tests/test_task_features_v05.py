from __future__ import annotations

import unittest

from oss_mentor.task_features import extract_task_features


class TaskFeaturesV05Tests(unittest.TestCase):
    def test_missing_fields_are_explicit_without_changing_feature_generation(
        self,
    ) -> None:
        features = extract_task_features(
            {
                "title": "Fix parser crash",
                "body_text": "",
                "labels": ["bug"],
                "primary_language": "",
                "comment_count": 0,
                "candidate_eligibility": "eligible",
            }
        )

        missing = {
            item["field"]: item
            for item in features.feature_evidence["missing_fields"]
        }

        self.assertIn("body_text", missing)
        self.assertIn("primary_language", missing)
        self.assertNotIn("title", missing)
        self.assertNotIn("labels", missing)

        self.assertIn("bug_fix", features.task_types)

    def test_empty_labels_are_not_reported_as_missing(
        self,
    ) -> None:
        features = extract_task_features(
            {
                "title": "Improve parser behavior",
                "body_text": "Implement a small parser improvement.",
                "labels": [],
                "primary_language": "Python",
                "comment_count": 0,
                "candidate_eligibility": "eligible",
            }
        )

        fields = {
            item["field"]
            for item in features.feature_evidence["missing_fields"]
        }

        self.assertNotIn("labels", fields)

    def test_unavailable_labels_are_reported_as_missing(
        self,
    ) -> None:
        features = extract_task_features(
            {
                "title": "Improve parser behavior",
                "body_text": "Implement a small parser improvement.",
                "primary_language": "Python",
                "comment_count": 0,
                "candidate_eligibility": "eligible",
            }
        )

        fields = {
            item["field"]
            for item in features.feature_evidence["missing_fields"]
        }

        self.assertIn("labels", fields)

    def test_skill_requirement_evidence_has_confidence(
        self,
    ) -> None:
        features = extract_task_features(
            {
                "title": "Fix pytest fixture handling",
                "body_text": (
                    "Update conftest.py and pytest fixtures "
                    "to fix the failing tests."
                ),
                "labels": ["bug", "testing"],
                "primary_language": "Python",
                "comment_count": 1,
                "candidate_eligibility": "eligible",
            }
        )

        skills = features.feature_evidence[
            "skill_requirement_evidence"
        ]["skills"]

        self.assertTrue(skills)

        for evidence in skills.values():
            self.assertIn(
                evidence["confidence"],
                {"low", "medium", "high"},
            )


if __name__ == "__main__":
    unittest.main()