from __future__ import annotations

import unittest
from pathlib import Path

from oss_mentor.developer_profiles import custom_profile_for_matching, load_profiles


class DeveloperProfileTests(unittest.TestCase):
    def test_demo_profiles_cover_both_tracks_without_identity_data(self) -> None:
        root = Path(__file__).resolve().parents[1]
        profiles = load_profiles(root / "config" / "demo_profiles_v0.1.json")

        self.assertEqual(2, len(profiles))
        self.assertEqual({"newcomer", "growth"}, {p.service_track for p in profiles})
        self.assertTrue(all(p.profile_source == "demo" for p in profiles))
        self.assertTrue(all("Python" in p.skills for p in profiles))

    def test_custom_profile_is_normalized_for_matching(self) -> None:
        profile = custom_profile_for_matching(
            {
                "display_name": "  本地用户  ",
                "service_track": "growth",
                "preferred_languages": ["Python", "python"],
                "operating_systems": ["Windows"],
                "preferred_task_types": ["testing"],
                "max_code_difficulty": 3,
                "max_setup_difficulty": 2,
                "desired_skill_stretch": 1,
                "skills": {"Python": 2, "testing": 1},
            }
        )
        self.assertEqual("本地用户", profile["display_name"])
        self.assertEqual(["Python"], profile["preferred_languages"])
        self.assertEqual(["windows"], profile["operating_systems"])
        self.assertEqual(2, profile["skills"]["python"])

    def test_custom_profile_rejects_platform_skill_spoofing(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid skill name"):
            custom_profile_for_matching(
                {
                    "service_track": "newcomer",
                    "preferred_languages": ["Python"],
                    "operating_systems": ["linux"],
                    "preferred_task_types": ["bug_fix"],
                    "max_code_difficulty": 1,
                    "max_setup_difficulty": 1,
                    "desired_skill_stretch": 0,
                    "skills": {"platform:macos": 4},
                }
            )


if __name__ == "__main__":
    unittest.main()
