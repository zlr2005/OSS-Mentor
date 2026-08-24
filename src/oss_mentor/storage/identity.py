"""SQLite implementation of the identity/session storage interface."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from oss_mentor.sqlite_store import SQLiteCandidateStore


class IdentityStore:
    """Identity, OAuth credential and session persistence backed by SQLite."""

    def __init__(self, base_store: SQLiteCandidateStore) -> None:
        self.base_store = base_store

    @property
    def database_path(self) -> Path:
        return self.base_store.database_path

    def initialize(self) -> None:
        self.base_store.initialize()

    def connect(self) -> sqlite3.Connection:
        return self.base_store.connect()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _ensure_tables(self, connection: sqlite3.Connection) -> None:
        """Create identity tables inline if migration 007 has not run yet."""
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS oss_user (
                user_id INTEGER PRIMARY KEY,
                github_user_id INTEGER NOT NULL UNIQUE,
                github_login TEXT NOT NULL,
                display_name TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                deleted_at TEXT
            );
            CREATE TABLE IF NOT EXISTS oauth_identity (
                oauth_identity_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES oss_user(user_id) ON DELETE CASCADE,
                provider TEXT NOT NULL,
                provider_user_id TEXT NOT NULL,
                access_token_ref TEXT NOT NULL,
                scope TEXT NOT NULL,
                token_expires_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (provider, provider_user_id)
            );
            CREATE TABLE IF NOT EXISTS oauth_state (
                oauth_state_id INTEGER PRIMARY KEY,
                state_hash TEXT NOT NULL UNIQUE,
                return_to TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                consumed_at TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_session (
                user_session_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES oss_user(user_id) ON DELETE CASCADE,
                session_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT
            );
            """
        )

    def create_user(
        self, *, github_user_id: int, github_login: str, display_name: str | None = None
    ) -> int:
        self.initialize()
        now = self._now()
        with self.connect() as connection:
            self._ensure_tables(connection)
            cursor = connection.execute(
                """
                INSERT INTO oss_user (
                    github_user_id, github_login, display_name, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (github_user_id, github_login, display_name, now, now),
            )
            return int(cursor.lastrowid)

    def find_user_by_github_id(self, github_user_id: int) -> dict[str, Any] | None:
        self.initialize()
        with self.connect() as connection:
            self._ensure_tables(connection)
            row = connection.execute(
                "SELECT * FROM oss_user WHERE github_user_id = ?",
                (github_user_id,),
            ).fetchone()
        return dict(row) if row else None

    def find_user(self, user_id: int) -> dict[str, Any] | None:
        self.initialize()
        with self.connect() as connection:
            self._ensure_tables(connection)
            row = connection.execute(
                "SELECT * FROM oss_user WHERE user_id = ?", (user_id,)
            ).fetchone()
        return dict(row) if row else None

    def store_oauth_credential(
        self,
        *,
        user_id: int,
        access_token_ref: str,
        scope: str,
        expires_at: str | None,
    ) -> int:
        self.initialize()
        now = self._now()
        with self.connect() as connection:
            self._ensure_tables(connection)
            connection.execute(
                """
                INSERT INTO oauth_identity (
                    user_id, provider, provider_user_id, access_token_ref,
                    scope, token_expires_at, created_at, updated_at
                ) VALUES (?, 'github', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, provider_user_id) DO UPDATE SET
                    access_token_ref = excluded.access_token_ref,
                    scope = excluded.scope,
                    token_expires_at = excluded.token_expires_at,
                    updated_at = excluded.updated_at
                """,
                (user_id, str(user_id), access_token_ref, scope, expires_at, now, now),
            )
            row = connection.execute(
                """
                SELECT oauth_identity_id FROM oauth_identity
                WHERE provider = 'github' AND provider_user_id = ?
                """,
                (str(user_id),),
            ).fetchone()
            return int(row["oauth_identity_id"])

    def create_oauth_state(self, *, state_hash: str, return_to: str, expires_at: str) -> int:
        self.initialize()
        now = self._now()
        with self.connect() as connection:
            self._ensure_tables(connection)
            cursor = connection.execute(
                """
                INSERT INTO oauth_state (state_hash, return_to, expires_at, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (state_hash, return_to, expires_at, now),
            )
            return int(cursor.lastrowid)

    def consume_oauth_state(self, state_hash: str) -> dict[str, Any] | None:
        """Atomically consume a one-time OAuth state, returning it or None."""
        self.initialize()
        now = self._now()
        with self.connect() as connection:
            self._ensure_tables(connection)
            row = connection.execute(
                """
                SELECT oauth_state_id, state_hash, return_to, expires_at, consumed_at
                FROM oauth_state WHERE state_hash = ? AND consumed_at IS NULL
                """,
                (state_hash,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE oauth_state SET consumed_at = ? WHERE oauth_state_id = ?",
                (now, row["oauth_state_id"]),
            )
        return dict(row)

    def create_session(
        self,
        *,
        user_id: int,
        session_id: str,
        expires_at: str,
    ) -> int:
        self.initialize()
        now = self._now()
        with self.connect() as connection:
            self._ensure_tables(connection)
            cursor = connection.execute(
                """
                INSERT INTO user_session (user_id, session_id, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, session_id, now, expires_at),
            )
            return int(cursor.lastrowid)

    def find_session(self, session_id: str) -> dict[str, Any] | None:
        self.initialize()
        with self.connect() as connection:
            self._ensure_tables(connection)
            row = connection.execute(
                """
                SELECT * FROM user_session
                WHERE session_id = ? AND revoked_at IS NULL
                """,
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def revoke_session(self, session_id: str) -> None:
        self.initialize()
        now = self._now()
        with self.connect() as connection:
            self._ensure_tables(connection)
            connection.execute(
                "UPDATE user_session SET revoked_at = ? WHERE session_id = ?",
                (now, session_id),
            )
