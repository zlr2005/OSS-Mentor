from __future__ import annotations

import unittest

from oss_mentor.matching import match_candidate, recommendation_availability


def task(**overrides):
    value = {
        "task_candidate_id": 1,
        "repository": "example/demo",
        "issue_number": 7,
        "title": "Example",
        "html_url": "https://github.com/example/demo/issues/7",
        "newcomer_label_signal": 1,
        "estimated_code_difficulty": 1,
        "estimated_setup_difficulty": 2,
        "newcomer_score": 75.0,
        "growth_value_score": 55.0,
        "primary_language": "Python",
        "task_types": ["bug_fix", "testing"],
        "requirements": [
            {"skill_name": "Python", "minimum_level": 1, "importance": 1.0},
            {"skill_name": "platform:macos", "minimum_level": 1, "importance": 1.0},
            {"skill_name": "testing", "minimum_level": 1, "importance": 0.6},
        ],
    }
    value.update(overrides)
    return value


def profile(**overrides):
    value = {
        "service_track": "newcomer",
        "preferred_languages": ["Python"],
        "operating_systems": ["macos"],
        "preferred_task_types": ["bug_fix"],
        "max_code_difficulty": 1,
        "max_setup_difficulty": 2,
        "desired_skill_stretch": 0,
        "skills": {"python": 1, "testing": 1},
    }
    value.update(overrides)
    return value


class MatchingTests(unittest.TestCase):
    def test_newcomer_match_is_explainable(self) -> None:
        result = match_candidate(profile(), task())
        self.assertIsNotNone(result)
        self.assertEqual("newcomer", result.track)
        self.assertEqual(1.0, result.skill_coverage)
        self.assertGreater(result.match_score, 80)

    def test_platform_mismatch_is_hard_filter(self) -> None:
        result = match_candidate(profile(operating_systems=["linux"]), task())
        self.assertIsNone(result)

    def test_unknown_auxiliary_tool_does_not_become_critical_mismatch(self) -> None:
        result = match_candidate(
            profile(),
            task(
                requirements=[
                    {"skill_name": "Python", "minimum_level": 1, "importance": 1.0},
                    {"skill_name": "testing", "minimum_level": 1, "importance": 0.6},
                    {"skill_name": "Docker", "minimum_level": 1, "importance": 0.7},
                ]
            ),
        )
        self.assertIsNotNone(result)
        docker_gap = next(item for item in result.skill_gaps if item["skill"] == "Docker")
        self.assertEqual(0, docker_gap["developer_level"])
        self.assertEqual(1, docker_gap["gap"])
        self.assertEqual(0.7, docker_gap["importance"])

    def test_growth_track_accepts_one_level_stretch(self) -> None:
        result = match_candidate(
            profile(
                service_track="growth",
                max_code_difficulty=3,
                max_setup_difficulty=3,
                desired_skill_stretch=1,
                skills={"python": 1, "testing": 0},
            ),
            task(
                newcomer_label_signal=0,
                estimated_code_difficulty=2,
                requirements=[
                    {"skill_name": "Python", "minimum_level": 2, "importance": 1.0},
                    {"skill_name": "testing", "minimum_level": 1, "importance": 0.6},
                ],
            ),
        )
        self.assertIsNotNone(result)
        self.assertEqual("growth", result.track)
        self.assertEqual(1, result.maximum_skill_gap)

    def test_language_and_task_type_preferences_are_hard_constraints(self) -> None:
        self.assertIsNone(
            match_candidate(profile(preferred_languages=["Go"]), task())
        )
        self.assertIsNone(
            match_candidate(profile(preferred_task_types=["documentation"]), task())
        )

    def test_availability_counts_current_and_alternative_options(self) -> None:
        tasks = [
            task(),
            task(
                task_candidate_id=2,
                primary_language="Go",
                requirements=[
                    {"skill_name": "Go", "minimum_level": 1, "importance": 1.0}
                ],
            ),
            task(
                task_candidate_id=3,
                primary_language="Go",
                task_types=["documentation"],
                requirements=[
                    {"skill_name": "Go", "minimum_level": 1, "importance": 1.0}
                ],
            ),
        ]
        current = profile(skills={"python": 1, "testing": 1, "go": 0})
        availability = recommendation_availability(
            current,
            tasks,
            languages=("python", "go"),
            task_types=("bug_fix", "documentation"),
            operating_systems=("macos", "linux"),
        )
        self.assertEqual(1, availability["current_selection_count"])
        self.assertEqual(1, availability["language_counts"]["python"])
        self.assertEqual(1, availability["language_counts"]["go"])
        self.assertEqual(1, availability["language_task_type_counts"]["go"]["bug_fix"])
        self.assertEqual(1, availability["language_task_type_counts"]["go"]["documentation"])
        self.assertEqual(0, current["skills"]["go"])


if __name__ == "__main__":
    unittest.main()