from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from oss_mentor.contracts_midterm import (
    AssessmentConfirmation,
    AssessmentSubmission,
    ConfirmationPlan,
    IdempotencyConflictError,
    ProfileFeatureState,
    ProfilePreview,
    StalePlanError,
)
from oss_mentor.profiling.assessment import build_assessment_preview, load_questionnaire
from oss_mentor.profiling.confirmation import (
    build_confirmation_plan,
    build_feature_state,
    confirm_assessment_plan,
)
from oss_mentor.storage.base import MidtermProfileStore
from oss_mentor.storage.profiles import SQLiteMidtermProfileStore


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "db/sqlite"
QUESTIONNAIRE_PATH = ROOT / "config/midterm_profile/questionnaire_v0.1.json"
SCENARIOS_PATH = ROOT / "fixtures/midterm/b/assessment_scenarios_v0.1.json"


class MidtermProfileStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "profiles.sqlite3"
        self.store = SQLiteMidtermProfileStore(
            self.database,
            MIGRATIONS / "001_mvp.sql",
        )
        self.store.initialize()
        self._create_owned_profile(1, "profile:store-test")
        self._create_owned_profile(2, "profile:other-user")
        self.questionnaire_definition = json.loads(
            QUESTIONNAIRE_PATH.read_text(encoding="utf-8")
        )
        self.questionnaire = load_questionnaire(QUESTIONNAIRE_PATH)
        scenarios = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
        self.scenario = next(
            item for item in scenarios["scenarios"] if item["id"] == "zero_history"
        )
        self.store.put_questionnaire(
            questionnaire_id=self.questionnaire_definition["questionnaire_id"],
            questionnaire_version=self.questionnaire_definition["questionnaire_version"],
            schema_version=self.questionnaire_definition["schema_version"],
            feature_schema_version=self.questionnaire_definition["feature_schema_version"],
            definition=self.questionnaire_definition,
            definition_sha256=hashlib.sha256(QUESTIONNAIRE_PATH.read_bytes()).hexdigest(),
        )

    def _create_owned_profile(self, user_id: int, profile_key: str) -> None:
        now = "2026-10-26T00:00:00Z"
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO oss_user (
                    user_id, github_user_id, github_login,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, 1000 + user_id, f"user-{user_id}", now, now),
            )
            cursor = connection.execute(
                """
                INSERT INTO developer_profile (
                    profile_key, display_name, service_track,
                    preferred_languages_json, operating_systems_json,
                    preferred_task_types_json, max_code_difficulty,
                    max_setup_difficulty, desired_skill_stretch,
                    profile_source, created_at, updated_at
                ) VALUES (?, ?, 'newcomer', '[]', '[]', '[]', 1, 1, 1,
                          'user_input', ?, ?)
                """,
                (profile_key, f"User {user_id}", now, now),
            )
            connection.execute(
                """
                INSERT INTO profile_user_binding (
                    user_key, user_id, developer_profile_id,
                    linked_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (f"identity:test-{user_id}", user_id, cursor.lastrowid, now, now),
            )

    def _workflow(self, *, suffix: str = "main"):
        submission = AssessmentSubmission.from_dict(
            {
                **self.scenario["submission"],
                "questionnaire_id": self.questionnaire_definition["questionnaire_id"],
                "questionnaire_version": self.questionnaire_definition[
                    "questionnaire_version"
                ],
            }
        )
        submission_id = self.store.create_assessment_submission(
            user_id=1,
            submission=submission,
            idempotency_key=f"submission:{suffix}",
        )
        preview_payload = build_assessment_preview(
            self.questionnaire,
            self.scenario["submission"],
            external_evidence=self.scenario["external_evidence"],
        )
        preview = ProfilePreview.from_private(
            preview_payload,
            preview_id=f"preview:{suffix}",
            profile_key="profile:store-test",
            base_profile_version="profile:v0",
        )
        self.store.create_profile_preview(
            user_id=1,
            preview=preview,
            input_sha256=hashlib.sha256(
                json.dumps(preview_payload, sort_keys=True).encode("utf-8")
            ).hexdigest(),
        )
        state_payload = build_feature_state(
            preview_payload,
            profile_key=preview.profile_key,
            profile_version=preview.base_profile_version,
            updated_at="2026-10-25T00:00:00Z",
        )
        plan_payload = build_confirmation_plan(preview_payload, state_payload)
        plan = ConfirmationPlan.from_private(
            plan_payload,
            preview_id=preview.preview_id,
            created_at="2026-10-26T00:00:00Z",
        )
        if suffix != "main":
            plan = replace(plan, plan_id=f"plan:{suffix}")
        self.store.create_confirmation_plan(user_id=1, plan=plan)
        requested = {
            item.suggestion_id: "accept"
            for item in plan.suggestions
            if item.status == "pending"
        }
        result_payload = confirm_assessment_plan(
            {**plan_payload, "plan_id": plan.plan_id},
            state_payload,
            requested,
            confirmed_at="2026-10-26T00:01:00Z",
        )
        confirmation = AssessmentConfirmation.from_private(result_payload)
        if suffix != "main":
            confirmation = replace(
                confirmation,
                confirmation_id=f"confirmation:{suffix}",
                plan_id=plan.plan_id,
            )
        return submission, submission_id, preview, plan, confirmation

    def _commit(self, confirmation: AssessmentConfirmation, key: str = "confirm:main"):
        with self.store.transaction() as transaction:
            return transaction.commit_assessment_confirmation(
                user_id=1,
                confirmation=confirmation,
                decisions=confirmation.decisions,
                feature_state=confirmation.profile_state,
                idempotency_key=key,
            )

    def test_012_migration_applies_once_and_has_frozen_tables(self) -> None:
        self.store.initialize()
        expected = {
            "profile_questionnaire",
            "profile_assessment_submission",
            "profile_assessment_preview",
            "profile_assessment_preview_feature",
            "profile_assessment_plan",
            "profile_assessment_suggestion",
            "profile_assessment_confirmation",
            "profile_assessment_decision",
            "developer_profile_feature_state",
            "developer_profile_feature",
            "developer_profile_current_feature",
        }
        with self.store.connect() as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            applied = connection.execute(
                """SELECT COUNT(*) FROM schema_migration
                   WHERE migration_name = '012_midterm_profile_assessment.sql'"""
            ).fetchone()[0]
            foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        self.assertTrue(expected.issubset(tables))
        self.assertEqual(applied, 1)
        self.assertEqual(foreign_key_errors, [])
        self.assertIsInstance(self.store, MidtermProfileStore)

    def test_existing_011_database_upgrades_to_012(self) -> None:
        old_migrations = Path(self.temporary.name) / "migrations-through-011"
        old_migrations.mkdir()
        for migration in MIGRATIONS.glob("*.sql"):
            if migration.name < "012":
                shutil.copy2(migration, old_migrations / migration.name)
        upgrade_database = Path(self.temporary.name) / "upgrade.sqlite3"
        old_store = SQLiteMidtermProfileStore(
            upgrade_database,
            old_migrations / "001_mvp.sql",
        )
        old_store.initialize()
        current = SQLiteMidtermProfileStore(
            upgrade_database,
            MIGRATIONS / "001_mvp.sql",
        )
        current.initialize()
        with current.connect() as connection:
            applied = connection.execute(
                """SELECT COUNT(*) FROM schema_migration
                   WHERE migration_name = '012_midterm_profile_assessment.sql'"""
            ).fetchone()[0]
            questionnaire = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'profile_questionnaire'"
            ).fetchone()
        self.assertEqual(applied, 1)
        self.assertIsNotNone(questionnaire)

    def test_questionnaire_and_submission_are_idempotent(self) -> None:
        submission = AssessmentSubmission.from_dict(
            {
                **self.scenario["submission"],
                "questionnaire_id": self.questionnaire_definition["questionnaire_id"],
                "questionnaire_version": self.questionnaire_definition[
                    "questionnaire_version"
                ],
            }
        )
        first = self.store.create_assessment_submission(
            user_id=1,
            submission=submission,
            idempotency_key="submission:repeat",
        )
        second = self.store.create_assessment_submission(
            user_id=1,
            submission=submission,
            idempotency_key="submission:repeat",
        )
        self.assertEqual(first, second)
        self.assertEqual(
            self.store.get_assessment_submission(user_id=1, submission_id=first)["answers"],
            submission.answers,
        )
        changed = replace(submission, submitted_at="2026-10-26T02:00:00Z")
        with self.assertRaises(IdempotencyConflictError):
            self.store.create_assessment_submission(
                user_id=1,
                submission=changed,
                idempotency_key="submission:repeat",
            )

    def test_preview_and_plan_round_trip_without_cross_user_access(self) -> None:
        _, _, preview, plan, _ = self._workflow()
        self.assertEqual(
            self.store.get_profile_preview(user_id=1, preview_id=preview.preview_id),
            preview,
        )
        self.assertEqual(
            self.store.get_confirmation_plan(user_id=1, plan_id=plan.plan_id),
            plan,
        )
        self.assertIsNone(
            self.store.get_profile_preview(user_id=2, preview_id=preview.preview_id)
        )
        self.assertIsNone(
            self.store.get_confirmation_plan(user_id=2, plan_id=plan.plan_id)
        )

    def test_confirmation_requires_transaction_and_publishes_current_snapshot(self) -> None:
        _, _, _, plan, confirmation = self._workflow()
        with self.assertRaisesRegex(RuntimeError, "transaction"):
            self.store.commit_assessment_confirmation(
                user_id=1,
                confirmation=confirmation,
                decisions=confirmation.decisions,
                feature_state=confirmation.profile_state,
                idempotency_key="confirm:main",
            )
        stored = self._commit(confirmation)
        self.assertEqual(stored, confirmation)
        self.assertEqual(
            self.store.get_current_feature_state(user_id=1),
            confirmation.profile_state,
        )
        self.assertEqual(
            self.store.find_confirmation_by_idempotency_key(
                user_id=1, idempotency_key="confirm:main"
            ),
            confirmation,
        )
        enhanced = self.store.get_enhanced_profile_features(user_id=1)
        self.assertEqual(enhanced.profile_version, confirmation.new_profile_version)
        self.assertIsNone(enhanced.developer_key)
        self.assertEqual(enhanced.profile_key, "profile:store-test")
        self.assertEqual(len(enhanced.feature_names), 23)
        self.assertEqual(
            self.store.get_confirmation_plan(user_id=1, plan_id=plan.plan_id).status,
            "completed",
        )

    def test_confirmation_retry_is_idempotent_but_changed_payload_conflicts(self) -> None:
        _, _, _, _, confirmation = self._workflow()
        self._commit(confirmation, "confirm:retry")
        self.assertEqual(self._commit(confirmation, "confirm:retry"), confirmation)
        changed = replace(confirmation, confirmation_id="confirmation:different")
        with self.assertRaises(IdempotencyConflictError):
            self._commit(changed, "confirm:retry")

    def test_stale_plan_is_rejected_after_current_version_changes(self) -> None:
        _, _, _, _, confirmation = self._workflow()
        self._commit(confirmation)
        _, _, _, _, stale = self._workflow(suffix="stale")
        with self.assertRaises(StalePlanError):
            self._commit(stale, "confirm:stale")
        self.assertIsNone(
            self.store.find_confirmation_by_idempotency_key(
                user_id=1, idempotency_key="confirm:stale"
            )
        )

    def test_failure_rolls_back_decisions_state_and_current_pointer(self) -> None:
        _, _, _, plan, confirmation = self._workflow()
        with self.store.connect() as connection:
            connection.executescript(
                """
                CREATE TRIGGER fail_midterm_feature
                BEFORE INSERT ON developer_profile_feature
                BEGIN SELECT RAISE(ABORT, 'injected feature failure'); END;
                """
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self._commit(confirmation, "confirm:rollback")
        with self.store.connect() as connection:
            counts = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "profile_assessment_confirmation",
                    "profile_assessment_decision",
                    "developer_profile_feature_state",
                    "developer_profile_feature",
                    "developer_profile_current_feature",
                )
            }
        self.assertTrue(all(value == 0 for value in counts.values()))
        self.assertEqual(
            self.store.get_confirmation_plan(user_id=1, plan_id=plan.plan_id).status,
            "open",
        )

    def test_user_deletion_cascades_midterm_profile_history(self) -> None:
        _, _, _, _, confirmation = self._workflow()
        self._commit(confirmation)
        with self.store.connect() as connection:
            connection.execute("DELETE FROM oss_user WHERE user_id = 1")
            counts = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "profile_assessment_submission",
                    "profile_assessment_preview",
                    "profile_assessment_plan",
                    "profile_assessment_confirmation",
                    "developer_profile_feature_state",
                    "developer_profile_current_feature",
                )
            }
            profile = connection.execute(
                "SELECT 1 FROM developer_profile WHERE profile_key = 'profile:store-test'"
            ).fetchone()
        self.assertTrue(all(value == 0 for value in counts.values()))
        self.assertIsNone(profile)

    def test_confirmed_feature_history_rejects_in_place_updates(self) -> None:
        _, _, _, _, confirmation = self._workflow()
        self._commit(confirmation)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            with self.store.connect() as connection:
                connection.execute(
                    "UPDATE developer_profile_feature SET value = 0.0"
                )
        self.assertEqual(
            self.store.get_current_feature_state(user_id=1),
            confirmation.profile_state,
        )


if __name__ == "__main__":
    unittest.main()
