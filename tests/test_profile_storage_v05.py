from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from oss_mentor.developer_profiles import (
    build_github_profile_import,
    build_profile_merge_preview,
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


class ProfileStorageV05Tests(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.temporary = (
            tempfile.TemporaryDirectory()
        )
        self.addCleanup(
            self.temporary.cleanup
        )

        self.database = (
            Path(self.temporary.name)
            / "profiles.sqlite3"
        )

        self.storage = (
            SQLiteProfileStorage(
                self.database,
                MIGRATION,
            )
        )

        self.storage.initialize()

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

        github_payload = json.loads(
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

        self.github_import = (
            build_github_profile_import(
                github_payload
            )
        )

    def test_manual_profile_round_trip_preserves_field_state(
        self,
    ) -> None:
        first_id = (
            self.storage.save_profile(
                self.profile,
                user_key="fixture-user-key",
            )
        )

        second_id = (
            self.storage.save_profile(
                self.profile,
                user_key="fixture-user-key",
            )
        )

        self.assertEqual(
            first_id,
            second_id,
        )

        loaded = (
            self.storage.load_profile(
                "fixture-user"
            )
        )

        self.assertIsNotNone(
            loaded
        )

        assert loaded is not None

        self.assertEqual(
            ["Python"],
            loaded[
                "preferred_languages"
            ],
        )

        self.assertEqual(
            2,
            loaded["skills"]["Python"],
        )

        self.assertEqual(
            "fixture-user-key",
            loaded["user_key"],
        )

        metadata = loaded[
            "field_metadata"
        ][
            "skills.Python"
        ]

        self.assertEqual(
            "user_confirmed",
            metadata["source"],
        )

        self.assertTrue(
            metadata["locked"]
        )

    def test_github_import_is_idempotent_and_does_not_overwrite_manual_profile(
        self,
    ) -> None:
        self.storage.save_profile(
            self.profile
        )

        preview = (
            build_profile_merge_preview(
                self.profile,
                self.github_import,
            )
        )

        first = (
            self.storage.save_github_import(
                profile_key="fixture-user",
                github_import=
                    self.github_import,
                merge_preview=preview,
            )
        )

        second = (
            self.storage.save_github_import(
                profile_key="fixture-user",
                github_import=
                    self.github_import,
                merge_preview=preview,
            )
        )

        self.assertTrue(
            first["created"]
        )

        self.assertFalse(
            second["created"]
        )

        self.assertEqual(
            first[
                "github_profile_import_id"
            ],
            second[
                "github_profile_import_id"
            ],
        )

        loaded = (
            self.storage.load_profile(
                "fixture-user"
            )
        )

        self.assertIsNotNone(
            loaded
        )

        assert loaded is not None

        self.assertEqual(
            ["Python"],
            loaded[
                "preferred_languages"
            ],
        )

        self.assertEqual(
            2,
            loaded["skills"]["Python"],
        )

        self.assertTrue(
            loaded[
                "field_metadata"
            ][
                "skills.Python"
            ][
                "locked"
            ]
        )

        with self.storage.connect() as connection:
            import_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM github_profile_import
                    """
                ).fetchone()[0]
            )

        self.assertEqual(
            1,
            import_count,
        )

    def test_profile_import_persists_suggestions_and_skill_evidence(
        self,
    ) -> None:
        self.storage.save_profile(
            self.profile
        )

        preview = (
            build_profile_merge_preview(
                self.profile,
                self.github_import,
            )
        )

        self.storage.save_github_import(
            profile_key="fixture-user",
            github_import=
                self.github_import,
            merge_preview=preview,
        )

        suggestions = (
            self.storage.list_suggestions(
                profile_key="fixture-user",
                status="pending",
            )
        )

        self.assertTrue(
            suggestions
        )

        fields = {
            item["field"]
            for item in suggestions
        }

        self.assertIn(
            "preferred_languages",
            fields,
        )

        self.assertIn(
            "skills.testing",
            fields,
        )

        with self.storage.connect() as connection:
            evidence_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM developer_skill_evidence
                    """
                ).fetchone()[0]
            )

            sources = {
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT DISTINCT evidence_source
                    FROM developer_skill_evidence
                    """
                )
            }

        self.assertGreater(
            evidence_count,
            0,
        )

        self.assertIn(
            "github_explicit_evidence",
            sources,
        )

        self.assertIn(
            "github_weak_inference",
            sources,
        )

    def test_suggestion_status_is_resolved_once(
        self,
    ) -> None:
        self.storage.save_profile(
            self.profile
        )

        preview = (
            build_profile_merge_preview(
                self.profile,
                self.github_import,
            )
        )

        self.storage.save_github_import(
            profile_key="fixture-user",
            github_import=
                self.github_import,
            merge_preview=preview,
        )

        suggestions = (
            self.storage.list_suggestions(
                profile_key="fixture-user",
                status="pending",
            )
        )

        target = next(
            item
            for item in suggestions
            if item["field"]
            == "skills.build_tooling"
        )

        suggestion_id = int(
            target[
                "profile_field_suggestion_id"
            ]
        )

        self.storage.mark_suggestion_status(
            suggestion_id,
            status="accepted",
        )

        accepted = (
            self.storage.list_suggestions(
                profile_key="fixture-user",
                status="accepted",
            )
        )

        self.assertEqual(
            1,
            len(accepted),
        )

        self.assertEqual(
            "skills.build_tooling",
            accepted[0]["field"],
        )

        self.storage.mark_suggestion_status(
            suggestion_id,
            status="accepted",
        )

        with self.assertRaisesRegex(
            ValueError,
            "resolved suggestion",
        ):
            self.storage.mark_suggestion_status(
                suggestion_id,
                status="rejected",
            )

    def test_delete_profile_removes_profile_owned_data(
        self,
    ) -> None:
        self.storage.save_profile(
            self.profile,
            user_key="fixture-user-key",
        )

        preview = (
            build_profile_merge_preview(
                self.profile,
                self.github_import,
            )
        )

        self.storage.save_github_import(
            profile_key="fixture-user",
            github_import=
                self.github_import,
            merge_preview=preview,
        )

        deleted = (
            self.storage.delete_profile(
                "fixture-user"
            )
        )

        self.assertTrue(
            deleted
        )

        self.assertIsNone(
            self.storage.load_profile(
                "fixture-user"
            )
        )

        with self.storage.connect() as connection:
            counts = {
                table: int(
                    connection.execute(
                        f"""
                        SELECT COUNT(*)
                        FROM {table}
                        """
                    ).fetchone()[0]
                )
                for table in (
                    "developer_profile",
                    "developer_skill",
                    "profile_user_binding",
                    "profile_field_state",
                    "github_profile_import",
                    "profile_field_suggestion",
                    "developer_skill_evidence",
                )
            }

        self.assertTrue(
            all(
                count == 0
                for count
                in counts.values()
            ),
            counts,
        )


if __name__ == "__main__":
    unittest.main()