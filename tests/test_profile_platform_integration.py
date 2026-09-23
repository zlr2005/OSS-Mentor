from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from oss_mentor.services.profile_service import ProfileService, as_developer_profile_v2
from oss_mentor.sqlite_store import SQLiteCandidateStore
from oss_mentor.storage.base import ProfileStore
from oss_mentor.storage.identity import IdentityStore
from oss_mentor.storage.profiles import ProfileStorage, SQLiteProfileStorage

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "db" / "sqlite"


class ProfilePlatformIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = SQLiteProfileStorage(
            Path(self.temp.name) / "profiles.sqlite3", MIGRATIONS / "001_mvp.sql"
        )
        self.storage.initialize()
        self.service = ProfileService(self.storage)
        identities = IdentityStore(self.storage)
        self.owner = identities.create_user(github_user_id=101, github_login="owner")
        self.other = identities.create_user(github_user_id=102, github_login="other")
        fixtures = ROOT / "fixtures" / "contracts" / "v0.5"
        self.profile = json.loads((fixtures / "profiles.json").read_text())["profiles"][0]
        self.github = json.loads((fixtures / "github_user.json").read_text())
        self.api_contract = json.loads((fixtures / "profile_api.json").read_text())

    def import_target(self):
        self.service.save_manual_profile(self.profile, user_id=self.owner)
        result = self.service.import_github_profile(
            profile_key=self.profile["profile_key"], github_payload=self.github
        )
        self.assertEqual(set(self.api_contract["service_response_fields"]["import"]), set(result))
        return next(s for s in result["suggestions"] if s["field_name"] == "skills.build_tooling")

    def test_shared_protocol_and_dataclass_round_trip(self):
        self.assertIs(ProfileStorage, ProfileStore)
        self.assertIsInstance(self.storage, ProfileStore)
        model = as_developer_profile_v2(self.profile)
        self.storage.upsert_profile(model)
        self.assertEqual(model, self.service.profile_contract(model.profile_key))
        matching = self.storage.profile_for_matching(model.profile_key)
        self.assertEqual(2, matching["skills"]["python"])
        self.assertEqual("growth", matching["service_track"])

    def test_ownership_cannot_be_rebound_or_guessed(self):
        before = self.service.save_manual_profile(self.profile, user_id=self.owner)
        changed = {**self.profile, "display_name": "Wrong owner"}
        with self.assertRaisesRegex(ValueError, "ownership conflict"):
            self.service.save_manual_profile(changed, user_id=self.other)
        with self.assertRaisesRegex(ValueError, "ownership conflict"):
            self.service.save_manual_profile(
                {**self.profile, "profile_key": "second-profile"}, user_id=self.owner
            )
        self.assertEqual(before, self.service.profile_for_user(self.owner))
        self.assertIsNone(self.service.profile_for_user(self.other))

    def test_unknown_owner_and_invalid_id_leave_no_profile(self):
        for user_id in (True, 0, -1, "1", 999):
            with self.subTest(user_id=user_id), self.assertRaises(ValueError):
                self.service.save_manual_profile(self.profile, user_id=user_id)
            self.assertIsNone(self.service.profile(self.profile["profile_key"]))

    def test_legacy_key_cannot_modify_authenticated_binding(self):
        before = self.service.save_manual_profile(self.profile, user_id=self.owner)
        with self.assertRaisesRegex(ValueError, "authenticated ownership"):
            self.service.save_manual_profile(
                {**self.profile, "display_name": "Legacy overwrite"}, user_key="legacy"
            )
        self.assertEqual(before, self.service.profile_for_user(self.owner))

    def test_dataclass_update_keeps_owner_and_provenance(self):
        before = self.service.save_manual_profile(self.profile, user_id=self.owner)
        model = self.service.profile_contract(self.profile["profile_key"])
        self.storage.upsert_profile(model)
        saved = self.service.profile_for_user(self.owner)
        self.assertEqual(before["field_metadata"], saved["field_metadata"])
        self.assertEqual(self.owner, saved["user_id"])
        self.assertEqual(model, self.service.profile_contract(model.profile_key))

    def test_deleted_identity_cascades_profile_and_evidence(self):
        self.import_target()
        with self.storage.connect() as connection:
            connection.execute("DELETE FROM oss_user WHERE user_id = ?", (self.owner,))
            for table in ("developer_profile", "developer_skill", "profile_user_binding",
                          "github_profile_import", "profile_field_suggestion",
                          "profile_field_state", "developer_skill_evidence"):
                self.assertEqual(0, connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            self.assertEqual([], connection.execute("PRAGMA foreign_key_check").fetchall())

    def test_soft_deleted_user_is_not_returned_or_updated(self):
        self.service.save_manual_profile(self.profile, user_id=self.owner)
        with self.storage.connect() as connection:
            connection.execute("UPDATE oss_user SET deleted_at = '2026-09-21T00:00:00Z' WHERE user_id = ?",
                               (self.owner,))
        self.assertIsNone(self.service.profile_for_user(self.owner))
        with self.assertRaisesRegex(ValueError, "deleted"):
            self.service.save_manual_profile(self.profile, user_id=self.owner)

    def test_suggestion_contract_and_resolution_reload(self):
        target = self.import_target()
        required = set(self.api_contract["suggestion_required_fields"])
        self.assertTrue(required.issubset(target))
        self.assertEqual(target["field"], target["field_name"])
        self.assertEqual(target["source"], target["suggestion_source"])
        result = self.service.accept_suggestion(
            profile_key=self.profile["profile_key"],
            suggestion_id=target["profile_field_suggestion_id"],
        )
        self.assertEqual(set(self.api_contract["service_response_fields"]["resolve"]), set(result))
        self.assertIsNotNone(result["suggestion"]["resolved_at"])
        fresh = ProfileService(SQLiteProfileStorage(self.storage.database_path, MIGRATIONS / "001_mvp.sql"))
        self.assertEqual(result["profile"], fresh.profile_for_user(self.owner))
        self.assertIn(result["suggestion"], result["suggestions"])
        with self.assertRaisesRegex(ValueError, "state_conflict"):
            fresh.reject_suggestion(profile_key=self.profile["profile_key"],
                                    suggestion_id=target["profile_field_suggestion_id"])

    def test_suggestion_from_other_profile_is_not_resolved(self):
        target = self.import_target()
        other_profile = {**self.profile, "profile_key": "other"}
        self.service.save_manual_profile(other_profile, user_id=self.other)
        with self.assertRaises(KeyError):
            self.service.accept_suggestion(profile_key="other",
                                           suggestion_id=target["profile_field_suggestion_id"])

    def test_manual_edit_after_import_still_takes_priority(self):
        target = self.import_target()
        current = self.service.profile_for_user(self.owner)
        current["skills"]["build_tooling"] = 3
        current["field_metadata"]["skills.build_tooling"] = {
            "source": "user_input", "locked": False, "observed_at": "2026-09-21T00:00:00Z"
        }
        self.service.save_manual_profile(current, user_id=self.owner)
        with self.assertRaisesRegex(ValueError, "higher-priority"):
            self.service.accept_suggestion(profile_key=current["profile_key"],
                                           suggestion_id=target["profile_field_suggestion_id"])
        self.assertEqual(3, self.service.profile_for_user(self.owner)["skills"]["build_tooling"])

    def test_legacy_009_upgrades_without_inventing_identity(self):
        old_migrations = Path(self.temp.name) / "old-migrations"
        old_migrations.mkdir()
        for migration in MIGRATIONS.glob("*.sql"):
            if migration.name < "009a":
                shutil.copy2(migration, old_migrations / migration.name)
        database = Path(self.temp.name) / "old009.sqlite3"
        old = SQLiteCandidateStore(database, old_migrations / "001_mvp.sql")
        old.initialize()
        old.upsert_profile(as_legacy_profile(self.profile))
        with old.connect() as connection:
            connection.execute(
                """INSERT INTO profile_user_binding
                   (user_key, developer_profile_id, linked_at, updated_at)
                   SELECT '1', developer_profile_id, created_at, updated_at FROM developer_profile"""
            )
        upgraded = SQLiteProfileStorage(database, MIGRATIONS / "001_mvp.sql")
        upgraded.initialize()
        upgraded.initialize()
        saved = upgraded.load_profile(self.profile["profile_key"])
        self.assertEqual("1", saved["user_key"])
        self.assertNotIn("user_id", saved)
        self.assertEqual(self.profile["skills"], saved["skills"])
        with upgraded.connect() as connection:
            self.assertEqual([], connection.execute("PRAGMA foreign_key_check").fetchall())


def as_legacy_profile(profile):
    from oss_mentor.developer_profiles import DeveloperProfile
    values = copy.deepcopy(profile)
    values.pop("field_metadata")
    values["profile_source"] = "user_input"
    return DeveloperProfile(**values)
