from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from oss_mentor.sqlite_store import SQLiteCandidateStore


ROOT = Path(__file__).resolve().parents[1]
SQLITE_DIR = ROOT / "db" / "sqlite"
MIGRATION = SQLITE_DIR / "001_mvp.sql"

NOW = "2026-07-29T12:30:00+00:00"
OBSERVED_AT = "2026-07-29T12:30:00Z"


class ProfileMigrationV05Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

        self.database = (
            Path(self.temporary.name)
            / "profile-v05.sqlite3"
        )

        self.store = SQLiteCandidateStore(
            self.database,
            MIGRATION,
        )

    @staticmethod
    def _insert_profile(
        connection: sqlite3.Connection,
        *,
        profile_key: str = "fixture-user",
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO developer_profile (
                profile_key,
                display_name,
                service_track,
                preferred_languages_json,
                operating_systems_json,
                preferred_task_types_json,
                max_code_difficulty,
                max_setup_difficulty,
                desired_skill_stretch,
                profile_source,
                consent_version,
                created_at,
                updated_at
            ) VALUES (
                ?,
                'Fixture Developer',
                'growth',
                '["Python"]',
                '["windows"]',
                '["testing"]',
                2,
                2,
                1,
                'user_input',
                'profile-import-consent-v0.1',
                ?,
                ?
            )
            """,
            (
                profile_key,
                NOW,
                NOW,
            ),
        )

        if cursor.lastrowid is None:
            raise RuntimeError(
                "failed to create developer profile"
            )

        return int(cursor.lastrowid)

    @staticmethod
    def _insert_import(
        connection: sqlite3.Connection,
        *,
        developer_profile_id: int,
        import_key: str = "fixture-import-key",
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO github_profile_import (
                developer_profile_id,
                import_key,
                github_login,
                import_version,
                consent_version,
                observed_at,
                imported_at,
                public_repository_count,
                recent_repository_count,
                summary_json
            ) VALUES (
                ?,
                ?,
                'fixture-dev',
                'github-profile-import-v0.1',
                'profile-import-consent-v0.1',
                ?,
                ?,
                3,
                3,
                '{}'
            )
            """,
            (
                developer_profile_id,
                import_key,
                OBSERVED_AT,
                NOW,
            ),
        )

        if cursor.lastrowid is None:
            raise RuntimeError(
                "failed to create GitHub profile import"
            )

        return int(cursor.lastrowid)

    def test_009_is_applied_to_empty_database(
        self,
    ) -> None:
        self.store.initialize()

        with self.store.connect() as connection:
            tables = {
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table'
                    """
                )
            }

            migrations = {
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT migration_name
                    FROM schema_migration
                    """
                )
            }

        self.assertIn(
            "profile_user_binding",
            tables,
        )
        self.assertIn(
            "github_profile_import",
            tables,
        )
        self.assertIn(
            "profile_field_suggestion",
            tables,
        )
        self.assertIn(
            "developer_skill_evidence",
            tables,
        )

        self.assertIn(
            "009_github_profile_evidence.sql",
            migrations,
        )

    def test_009_upgrades_v04_database_without_losing_profile(
        self,
    ) -> None:
        old_migrations = (
            Path(self.temporary.name)
            / "v04-migrations"
        )
        old_migrations.mkdir()

        for number in range(1, 7):
            source = next(
                SQLITE_DIR.glob(
                    f"{number:03d}_*.sql"
                )
            )
            shutil.copy2(
                source,
                old_migrations / source.name,
            )

        old_database = (
            Path(self.temporary.name)
            / "upgrade.sqlite3"
        )

        old_store = SQLiteCandidateStore(
            old_database,
            old_migrations / "001_mvp.sql",
        )

        old_store.initialize()

        with old_store.connect() as connection:
            self._insert_profile(
                connection,
                profile_key="existing-v04-user",
            )

        upgraded_store = SQLiteCandidateStore(
            old_database,
            MIGRATION,
        )

        upgraded_store.initialize()

        with upgraded_store.connect() as connection:
            profile = connection.execute(
                """
                SELECT profile_key
                FROM developer_profile
                WHERE profile_key = ?
                """,
                ("existing-v04-user",),
            ).fetchone()

            migrations = {
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT migration_name
                    FROM schema_migration
                    """
                )
            }

            tables = {
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table'
                    """
                )
            }

        self.assertIsNotNone(profile)

        self.assertIn(
            "009_github_profile_evidence.sql",
            migrations,
        )

        self.assertIn(
            "github_profile_import",
            tables,
        )

    def test_profile_delete_cascades_profile_evidence(
        self,
    ) -> None:
        self.store.initialize()

        with self.store.connect() as connection:
            profile_id = self._insert_profile(
                connection
            )

            connection.execute(
                """
                INSERT INTO profile_user_binding (
                    user_key,
                    developer_profile_id,
                    linked_at,
                    updated_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    "fixture-user-key",
                    profile_id,
                    NOW,
                    NOW,
                ),
            )

            connection.execute(
                """
                INSERT INTO developer_skill (
                    developer_profile_id,
                    skill_name,
                    skill_level,
                    evidence_source,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    profile_id,
                    "Python",
                    2,
                    "user_input",
                    NOW,
                ),
            )

            import_id = self._insert_import(
                connection,
                developer_profile_id=profile_id,
            )

            connection.execute(
                """
                INSERT INTO profile_field_suggestion (
                    github_profile_import_id,
                    developer_profile_id,
                    field_name,
                    current_value_json,
                    proposed_value_json,
                    suggestion_source,
                    confidence,
                    evidence_json,
                    observed_at,
                    status,
                    blocked_reason,
                    resolved_at,
                    created_at,
                    updated_at
                ) VALUES (
                    ?,
                    ?,
                    'preferred_languages',
                    '["Python"]',
                    '["Python", "JavaScript"]',
                    'github_weak_inference',
                    0.70,
                    '[]',
                    ?,
                    'pending',
                    'higher_priority_current_source',
                    NULL,
                    ?,
                    ?
                )
                """,
                (
                    import_id,
                    profile_id,
                    OBSERVED_AT,
                    NOW,
                    NOW,
                ),
            )

            connection.execute(
                """
                INSERT INTO developer_skill_evidence (
                    developer_profile_id,
                    github_profile_import_id,
                    skill_name,
                    evidence_source,
                    confidence,
                    evidence_json,
                    observed_at,
                    created_at
                ) VALUES (
                    ?,
                    ?,
                    'testing',
                    'github_explicit_evidence',
                    0.90,
                    '[]',
                    ?,
                    ?
                )
                """,
                (
                    profile_id,
                    import_id,
                    OBSERVED_AT,
                    NOW,
                ),
            )

            connection.execute(
                """
                DELETE FROM developer_profile
                WHERE developer_profile_id = ?
                """,
                (profile_id,),
            )

            counts = {
                table: int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    ).fetchone()[0]
                )
                for table in (
                    "profile_user_binding",
                    "github_profile_import",
                    "profile_field_suggestion",
                    "developer_skill_evidence",
                    "developer_skill",
                )
            }

        self.assertEqual(
            {
                "profile_user_binding": 0,
                "github_profile_import": 0,
                "profile_field_suggestion": 0,
                "developer_skill_evidence": 0,
                "developer_skill": 0,
            },
            counts,
        )

    def test_import_key_is_unique_for_idempotency(
        self,
    ) -> None:
        self.store.initialize()

        with self.store.connect() as connection:
            profile_id = self._insert_profile(
                connection
            )

            self._insert_import(
                connection,
                developer_profile_id=profile_id,
                import_key="same-import",
            )

            with self.assertRaises(
                sqlite3.IntegrityError
            ):
                self._insert_import(
                    connection,
                    developer_profile_id=profile_id,
                    import_key="same-import",
                )

    def test_suggestion_constraints_are_enforced(
        self,
    ) -> None:
        self.store.initialize()

        with self.store.connect() as connection:
            profile_id = self._insert_profile(
                connection
            )

            import_id = self._insert_import(
                connection,
                developer_profile_id=profile_id,
            )

            with self.assertRaises(
                sqlite3.IntegrityError
            ):
                connection.execute(
                    """
                    INSERT INTO profile_field_suggestion (
                        github_profile_import_id,
                        developer_profile_id,
                        field_name,
                        proposed_value_json,
                        suggestion_source,
                        confidence,
                        evidence_json,
                        observed_at,
                        status,
                        created_at,
                        updated_at
                    ) VALUES (
                        ?,
                        ?,
                        'skills.testing',
                        '1',
                        'github_explicit_evidence',
                        1.50,
                        '[]',
                        ?,
                        'pending',
                        ?,
                        ?
                    )
                    """,
                    (
                        import_id,
                        profile_id,
                        OBSERVED_AT,
                        NOW,
                        NOW,
                    ),
                )

    def test_profile_tables_do_not_store_auth_secrets(
        self,
    ) -> None:
        self.store.initialize()

        tables = (
            "profile_user_binding",
            "github_profile_import",
            "profile_field_suggestion",
            "developer_skill_evidence",
        )

        forbidden_columns = {
            "token",
            "access_token",
            "refresh_token",
            "cookie",
            "authorization_code",
            "oauth_state",
            "state",
        }

        with self.store.connect() as connection:
            for table in tables:
                columns = {
                    str(row[1]).casefold()
                    for row in connection.execute(
                        f"PRAGMA table_info({table})"
                    )
                }

                self.assertTrue(
                    forbidden_columns.isdisjoint(
                        columns
                    ),
                    (
                        f"{table} contains forbidden "
                        f"authentication columns: "
                        f"{forbidden_columns & columns}"
                    ),
                )


if __name__ == "__main__":
    unittest.main()