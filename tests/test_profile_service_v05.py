from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from oss_mentor.services.profile_service import (
    ProfileService,
)
from oss_mentor.storage.profiles import (
    SQLiteProfileStorage,
)


ROOT = Path(__file__).resolve().parents[1]

MIGRATION = (
    ROOT
    / "db"
    / "sqlite"
    / "001_mvp.sql"
)


class ProfileServiceV05Tests(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.temporary = (
            tempfile.TemporaryDirectory()
        )
        self.addCleanup(
            self.temporary.cleanup
        )

        database = (
            Path(self.temporary.name)
            / "profile-service.sqlite3"
        )

        self.storage = (
            SQLiteProfileStorage(
                database,
                MIGRATION,
            )
        )
        self.storage.initialize()

        self.service = ProfileService(
            self.storage
        )

        profiles_payload = json.loads(
            (
                ROOT
                / "fixtures"
                / "contracts"
                / "v0.5"
                / "profiles.json"
            ).read_text(
                encoding="utf-8"
            )
        )

        self.profile = copy.deepcopy(
            profiles_payload[
                "profiles"
            ][0]
        )

        self.github_payload = json.loads(
            (
                ROOT
                / "fixtures"
                / "contracts"
                / "v0.5"
                / "github_user.json"
            ).read_text(
                encoding="utf-8"
            )
        )

    def test_manual_profile_works_without_github(
        self,
    ) -> None:
        saved = (
            self.service.save_manual_profile(
                self.profile,
                user_key="fixture-user-key",
            )
        )

        self.assertEqual(
            "fixture-user",
            saved["profile_key"],
        )

        self.assertEqual(
            ["Python"],
            saved[
                "preferred_languages"
            ],
        )

        self.assertEqual(
            2,
            saved["skills"]["Python"],
        )

    def test_import_twice_is_idempotent(
        self,
    ) -> None:
        self.service.save_manual_profile(
            self.profile
        )

        first = (
            self.service.import_github_profile(
                profile_key="fixture-user",
                github_payload=
                    self.github_payload,
            )
        )

        second = (
            self.service.import_github_profile(
                profile_key="fixture-user",
                github_payload=
                    self.github_payload,
            )
        )

        self.assertTrue(
            first[
                "persistence"
            ]["created"]
        )

        self.assertFalse(
            second[
                "persistence"
            ]["created"]
        )

        self.assertEqual(
            first[
                "persistence"
            ][
                "github_profile_import_id"
            ],
            second[
                "persistence"
            ][
                "github_profile_import_id"
            ],
        )

    def test_import_does_not_overwrite_manual_fields(
        self,
    ) -> None:
        self.service.save_manual_profile(
            self.profile
        )

        self.service.import_github_profile(
            profile_key="fixture-user",
            github_payload=
                self.github_payload,
        )

        saved = self.service.profile(
            "fixture-user"
        )

        self.assertIsNotNone(
            saved
        )

        assert saved is not None

        self.assertEqual(
            ["Python"],
            saved[
                "preferred_languages"
            ],
        )

        self.assertEqual(
            2,
            saved["skills"]["Python"],
        )

        self.assertTrue(
            saved[
                "field_metadata"
            ][
                "skills.Python"
            ][
                "locked"
            ]
        )

    def test_accept_explicit_skill_suggestion_updates_profile(
        self,
    ) -> None:
        self.service.save_manual_profile(
            self.profile
        )

        imported = (
            self.service.import_github_profile(
                profile_key="fixture-user",
                github_payload=
                    self.github_payload,
            )
        )

        target = next(
            item
            for item
            in imported["suggestions"]
            if item["field"]
            == "skills.build_tooling"
        )

        result = (
            self.service.accept_suggestion(
                profile_key="fixture-user",
                suggestion_id=int(
                    target[
                        "profile_field_suggestion_id"
                    ]
                ),
            )
        )

        self.assertEqual(
            1,
            result[
                "profile"
            ][
                "skills"
            ][
                "build_tooling"
            ],
        )

        metadata = (
            result[
                "profile"
            ][
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

    def test_reject_suggestion_keeps_profile_unchanged(
        self,
    ) -> None:
        self.service.save_manual_profile(
            self.profile
        )

        imported = (
            self.service.import_github_profile(
                profile_key="fixture-user",
                github_payload=
                    self.github_payload,
            )
        )

        target = next(
            item
            for item
            in imported["suggestions"]
            if item["field"]
            == "skills.build_tooling"
        )

        before = copy.deepcopy(
            self.service.profile(
                "fixture-user"
            )
        )

        self.service.reject_suggestion(
            profile_key="fixture-user",
            suggestion_id=int(
                target[
                    "profile_field_suggestion_id"
                ]
            ),
        )

        after = self.service.profile(
            "fixture-user"
        )

        self.assertEqual(
            before,
            after,
        )

        rejected = (
            self.storage.list_suggestions(
                profile_key="fixture-user",
                status="rejected",
            )
        )

        self.assertEqual(
            1,
            len(rejected),
        )

    def test_locked_manual_skill_cannot_be_accepted(
        self,
    ) -> None:
        self.service.save_manual_profile(
            self.profile
        )

        imported = (
            self.service.import_github_profile(
                profile_key="fixture-user",
                github_payload=
                    self.github_payload,
            )
        )

        target = next(
            item
            for item
            in imported["suggestions"]
            if item["field"]
            == "skills.Python"
        )

        with self.assertRaisesRegex(
            ValueError,
            "locked",
        ):
            self.service.accept_suggestion(
                profile_key="fixture-user",
                suggestion_id=int(
                    target[
                        "profile_field_suggestion_id"
                    ]
                ),
            )

        saved = self.service.profile(
            "fixture-user"
        )

        assert saved is not None

        self.assertEqual(
            2,
            saved["skills"]["Python"],
        )

    def test_delete_profile_removes_user_profile(
        self,
    ) -> None:
        self.service.save_manual_profile(
            self.profile,
            user_key="fixture-user-key",
        )

        deleted = (
            self.service.delete_profile(
                "fixture-user"
            )
        )

        self.assertTrue(
            deleted
        )

        self.assertIsNone(
            self.service.profile(
                "fixture-user"
            )
        )


if __name__ == "__main__":
    unittest.main()