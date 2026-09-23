from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from oss_mentor.developer_profiles import (
    GITHUB_PROFILE_IMPORT_VERSION,
    build_github_profile_import,
)


class GitHubProfileImportTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parents[1]

        self.payload = json.loads(
            (
                root
                / "fixtures"
                / "contracts"
                / "v0.5"
                / "github_user.json"
            ).read_text(encoding="utf-8")
        )

    def test_import_builds_expected_public_summary(
        self,
    ) -> None:
        result = build_github_profile_import(
            self.payload
        )

        self.assertEqual(
            GITHUB_PROFILE_IMPORT_VERSION,
            result["schema_version"],
        )
        self.assertEqual(
            "fixture-dev",
            result["github_login"],
        )

        self.assertEqual(
            3,
            result["public_repository_count"],
        )
        self.assertEqual(
            3,
            result[
                "active_public_repository_count"
            ],
        )
        self.assertEqual(
            3,
            result[
                "recent_active_repository_count"
            ],
        )

        self.assertEqual(
            {
                "commits": 19,
                "pull_requests": 6,
                "issues": 3,
                "reviews": 7,
            },
            result["activity_summary"],
        )

        self.assertEqual(
            {
                "first_contribution_at":
                    "2025-11-10T09:00:00Z",
                "last_contribution_at":
                    "2026-07-27T10:00:00Z",
            },
            result["activity_range"],
        )

    def test_language_distribution_is_stable(
        self,
    ) -> None:
        result = build_github_profile_import(
            self.payload
        )

        distribution = {
            row["language"]: row["share"]
            for row
            in result["language_distribution"]
        }

        self.assertEqual(
            0.52,
            distribution["Python"],
        )
        self.assertEqual(
            0.24,
            distribution["JavaScript"],
        )

        self.assertEqual(
            ["Python", "JavaScript"],
            result["suggestions"][
                "preferred_languages"
            ]["value"],
        )

        self.assertEqual(
            "github_weak_inference",
            result["suggestions"][
                "preferred_languages"
            ]["source"],
        )

    def test_skill_evidence_is_extracted(
        self,
    ) -> None:
        result = build_github_profile_import(
            self.payload
        )

        skills = {
            item["skill_name"]: item
            for item
            in result["suggestions"]["skills"]
        }

        self.assertIn("Python", skills)
        self.assertIn("JavaScript", skills)
        self.assertIn("testing", skills)
        self.assertIn(
            "documentation",
            skills,
        )
        self.assertIn(
            "build_tooling",
            skills,
        )

        self.assertEqual(
            "github_weak_inference",
            skills["Python"]["source"],
        )

        self.assertEqual(
            "github_explicit_evidence",
            skills["testing"]["source"],
        )

        self.assertTrue(
            skills["testing"]["evidence"]
        )

    def test_private_repository_never_leaks(
        self,
    ) -> None:
        payload = copy.deepcopy(
            self.payload
        )

        payload["repositories"].append(
            {
                "full_name": "private-labs/secret",
                "private": True,
                "archived": False,
                "languages": {
                    "Rust": 999999,
                },
                "contributions": {
                    "commits": 500,
                    "pull_requests": 50,
                    "issues": 30,
                    "reviews": 80,
                },
                "first_contribution_at":
                    "2025-01-01T00:00:00Z",
                "last_contribution_at":
                    "2026-07-28T00:00:00Z",
                "contributed_paths": [
                    "src/private.rs",
                ],
            }
        )

        result = build_github_profile_import(
            payload
        )

        serialized = json.dumps(
            result,
            ensure_ascii=False,
        )

        self.assertNotIn(
            "private-labs/secret",
            serialized,
        )
        self.assertNotIn(
            '"Rust"',
            serialized,
        )
        self.assertEqual(
            3,
            result["public_repository_count"],
        )

    def test_contribution_volume_does_not_raise_level(
        self,
    ) -> None:
        payload = copy.deepcopy(
            self.payload
        )

        payload["repositories"][0][
            "contributions"
        ]["commits"] = 99999

        result = build_github_profile_import(
            payload
        )

        skills = {
            item["skill_name"]: item
            for item
            in result["suggestions"]["skills"]
        }

        self.assertEqual(
            1,
            skills["Python"][
                "suggested_level"
            ],
        )

    def test_empty_public_data_is_conservative(
        self,
    ) -> None:
        payload = copy.deepcopy(
            self.payload
        )
        payload["repositories"] = []

        result = build_github_profile_import(
            payload
        )

        self.assertEqual(
            [],
            result["suggestions"][
                "preferred_languages"
            ]["value"],
        )

        self.assertEqual(
            [],
            result["suggestions"]["skills"],
        )

        self.assertEqual(
            {
                "commits": 0,
                "pull_requests": 0,
                "issues": 0,
                "reviews": 0,
            },
            result["activity_summary"],
        )

    def test_consent_is_required(
        self,
    ) -> None:
        payload = copy.deepcopy(
            self.payload
        )
        payload["consent_version"] = ""

        with self.assertRaisesRegex(
            ValueError,
            "consent_version",
        ):
            build_github_profile_import(
                payload
            )

    def test_same_input_is_deterministic(
        self,
    ) -> None:
        first = build_github_profile_import(
            self.payload
        )
        second = build_github_profile_import(
            self.payload
        )

        self.assertEqual(
            first,
            second,
        )


if __name__ == "__main__":
    unittest.main()