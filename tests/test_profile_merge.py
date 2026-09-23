from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from oss_mentor.developer_profiles import (
    build_github_profile_import,
    build_profile_merge_preview,
    apply_profile_suggestion,
)


class ProfileMergeTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parents[1]

        github_payload = json.loads(
            (
                root
                / "fixtures"
                / "contracts"
                / "v0.5"
                / "github_user.json"
            ).read_text(encoding="utf-8")
        )

        profiles_payload = json.loads(
            (
                root
                / "fixtures"
                / "contracts"
                / "v0.5"
                / "profiles.json"
            ).read_text(encoding="utf-8")
        )

        self.github_import = (
            build_github_profile_import(
                github_payload
            )
        )

        self.profile = (
            profiles_payload[
                "profiles"
            ][0]
        )

    def test_preview_does_not_mutate_profile(
        self,
    ) -> None:
        before = copy.deepcopy(
            self.profile
        )

        build_profile_merge_preview(
            self.profile,
            self.github_import,
        )

        self.assertEqual(
            before,
            self.profile,
        )

    def test_manual_language_is_not_silently_overwritten(
        self,
    ) -> None:
        preview = (
            build_profile_merge_preview(
                self.profile,
                self.github_import,
            )
        )

        language = next(
            item
            for item in preview[
                "suggestions"
            ]
            if item["field"]
            == "preferred_languages"
        )

        self.assertEqual(
            ["Python"],
            language["current_value"],
        )

        self.assertEqual(
            ["Python", "JavaScript"],
            language["proposed_value"],
        )

        self.assertEqual(
            "user_input",
            language["current_source"],
        )

        self.assertEqual(
            "higher_priority_current_source",
            language["blocked_reason"],
        )

        self.assertEqual(
            ["Python"],
            self.profile[
                "preferred_languages"
            ],
        )

    def test_locked_confirmed_skill_is_protected(
        self,
    ) -> None:
        preview = (
            build_profile_merge_preview(
                self.profile,
                self.github_import,
            )
        )

        python_skill = next(
            item
            for item in preview[
                "suggestions"
            ]
            if item["field"]
            == "skills.Python"
        )

        self.assertTrue(
            python_skill[
                "current_locked"
            ]
        )

        self.assertEqual(
            "field_locked",
            python_skill[
                "blocked_reason"
            ],
        )

        self.assertEqual(
            2,
            self.profile[
                "skills"
            ]["Python"],
        )

    def test_reject_does_not_change_profile(
        self,
    ) -> None:
        preview = (
            build_profile_merge_preview(
                self.profile,
                self.github_import,
            )
        )

        suggestion = next(
            item
            for item in preview[
                "suggestions"
            ]
            if item["field"]
            == "skills.build_tooling"
        )

        before = copy.deepcopy(
            self.profile
        )

        result = (
            apply_profile_suggestion(
                self.profile,
                suggestion,
                decision="reject",
            )
        )

        self.assertEqual(
            before,
            result["profile"],
        )

        self.assertEqual(
            "rejected",
            result[
                "suggestion"
            ]["status"],
        )

    def test_accept_adds_new_skill_with_provenance(
        self,
    ) -> None:
        preview = (
            build_profile_merge_preview(
                self.profile,
                self.github_import,
            )
        )

        suggestion = next(
            item
            for item in preview[
                "suggestions"
            ]
            if item["field"]
            == "skills.build_tooling"
        )

        result = (
            apply_profile_suggestion(
                self.profile,
                suggestion,
                decision="accept",
            )
        )

        updated = result["profile"]

        self.assertEqual(
            1,
            updated["skills"][
                "build_tooling"
            ],
        )

        metadata = (
            updated[
                "field_metadata"
            ][
                "skills.build_tooling"
            ]
        )

        self.assertEqual(
            "user_confirmed",
            metadata["source"],
        )

        self.assertEqual(
            "github_explicit_evidence",
            metadata[
                "accepted_source"
            ],
        )

        self.assertTrue(
            metadata["evidence"]
        )

    def test_locked_field_cannot_be_accepted(
        self,
    ) -> None:
        preview = (
            build_profile_merge_preview(
                self.profile,
                self.github_import,
            )
        )

        suggestion = next(
            item
            for item in preview[
                "suggestions"
            ]
            if item["field"]
            == "skills.Python"
        )

        with self.assertRaisesRegex(
            ValueError,
            "locked",
        ):
            apply_profile_suggestion(
                self.profile,
                suggestion,
                decision="accept",
            )

    def test_higher_priority_manual_field_cannot_be_accepted_over(
        self,
    ) -> None:
        preview = (
            build_profile_merge_preview(
                self.profile,
                self.github_import,
            )
        )

        suggestion = next(
            item
            for item in preview[
                "suggestions"
            ]
            if item["field"]
            == "preferred_languages"
        )

        with self.assertRaisesRegex(
            ValueError,
            "higher-priority",
        ):
            apply_profile_suggestion(
                self.profile,
                suggestion,
                decision="accept",
            )

    def test_preview_is_deterministic(
        self,
    ) -> None:
        first = (
            build_profile_merge_preview(
                self.profile,
                self.github_import,
            )
        )

        second = (
            build_profile_merge_preview(
                self.profile,
                self.github_import,
            )
        )

        self.assertEqual(
            first,
            second,
        )


if __name__ == "__main__":
    unittest.main()