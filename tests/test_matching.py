from __future__ import annotations

import unittest

from oss_mentor.matching import match_candidate


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


if __name__ == "__main__":
    unittest.main()
