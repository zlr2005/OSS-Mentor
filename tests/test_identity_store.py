from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from oss_mentor.sqlite_store import SQLiteCandidateStore
from oss_mentor.storage.identity import IdentityStore


class IdentityStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = SQLiteCandidateStore(
            Path(self.temporary.name) / "identity_test.sqlite3",
            root / "db" / "sqlite" / "001_mvp.sql",
        )
        base.initialize()
        self.store = IdentityStore(base)

    def test_user_lifecycle(self) -> None:
        user_id = self.store.create_user(
            github_user_id=1001, github_login="alice", display_name="Alice"
        )
        found = self.store.find_user_by_github_id(1001)
        self.assertIsNotNone(found)
        self.assertEqual("alice", found["github_login"])
        self.assertEqual(user_id, found["user_id"])

    def test_oauth_state_is_single_use(self) -> None:
        self.store.create_oauth_state(
            state_hash="hash-a",
            return_to="/",
            expires_at="2099-01-01T00:00:00Z",
        )
        first = self.store.consume_oauth_state("hash-a")
        second = self.store.consume_oauth_state("hash-a")
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_session_lifecycle(self) -> None:
        user_id = self.store.create_user(github_user_id=2002, github_login="bob")
        self.store.create_session(
            user_id=user_id,
            session_id="session-1",
            expires_at="2099-01-01T00:00:00Z",
        )
        session = self.store.find_session("session-1")
        self.assertIsNotNone(session)
        self.store.revoke_session("session-1")
        self.assertIsNone(self.store.find_session("session-1"))

    def test_credential_upsert_updates_same_identity(self) -> None:
        user_id = self.store.create_user(github_user_id=3003, github_login="carol")
        first = self.store.store_oauth_credential(
            user_id=user_id,
            access_token_ref="ref-a",
            scope="read:user",
            expires_at=None,
        )
        second = self.store.store_oauth_credential(
            user_id=user_id,
            access_token_ref="ref-b",
            scope="read:user",
            expires_at=None,
        )
        with self.store.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM oauth_identity WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
            ref = connection.execute(
                "SELECT access_token_ref FROM oauth_identity WHERE user_id = ?",
                (user_id,),
            ).fetchone()[0]
        self.assertEqual(1, count)
        self.assertEqual("ref-b", ref)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
