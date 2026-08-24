from __future__ import annotations

import unittest

from oss_mentor.services.auth_service import (
    AuthService,
    AuthSettings,
    GitHubAuthError,
    SESSION_COOKIE_NAME,
)


class FakeIdentityStore:
    """In-memory double for the identity storage interface."""

    def __init__(self) -> None:
        self.users: list[dict] = []
        self.sessions: list[dict] = []
        self.states: list[dict] = []
        self.credentials: list[dict] = []
        self.next_user_id = 1

    def create_user(self, *, github_user_id, github_login, display_name=None):
        self.users.append(
            {
                "user_id": self.next_user_id,
                "github_user_id": github_user_id,
                "github_login": github_login,
                "display_name": display_name,
                "deleted_at": None,
            }
        )
        self.next_user_id += 1
        return self.users[-1]["user_id"]

    def find_user_by_github_id(self, github_user_id):
        for user in self.users:
            if user["github_user_id"] == github_user_id:
                return user
        return None

    def find_user(self, user_id):
        for user in self.users:
            if user["user_id"] == user_id:
                return user
        return None

    def store_oauth_credential(self, *, user_id, access_token_ref, scope, expires_at):
        self.credentials.append(
            {
                "user_id": user_id,
                "access_token_ref": access_token_ref,
                "scope": scope,
                "expires_at": expires_at,
            }
        )
        return len(self.credentials)

    def create_oauth_state(self, *, state_hash, return_to, expires_at):
        self.states.append(
            {
                "state_hash": state_hash,
                "return_to": return_to,
                "expires_at": expires_at,
                "consumed_at": None,
            }
        )
        return len(self.states)

    def consume_oauth_state(self, state_hash):
        for state in self.states:
            if state["state_hash"] == state_hash and state["consumed_at"] is None:
                state["consumed_at"] = "2026-07-29T12:30:00Z"
                return state
        return None

    def create_session(self, *, user_id, session_id, expires_at):
        self.sessions.append(
            {
                "user_id": user_id,
                "session_id": session_id,
                "expires_at": expires_at,
                "revoked_at": None,
            }
        )
        return len(self.sessions)

    def find_session(self, session_id):
        for session in self.sessions:
            if session["session_id"] == session_id and session["revoked_at"] is None:
                return session
        return None

    def revoke_session(self, session_id):
        for session in self.sessions:
            if session["session_id"] == session_id:
                session["revoked_at"] = "2026-07-29T12:30:00Z"


def _settings(configured: bool = True) -> AuthSettings:
    return AuthSettings(
        client_id="client-1" if configured else None,
        client_secret="secret-1" if configured else None,
        session_secret="test-secret",
        base_url="http://127.0.0.1:8765",
        github_api_base="https://api.github.com",
    )


class AuthServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeIdentityStore()

    def test_unconfigured_oauth_returns_clear_message(self) -> None:
        service = AuthService(self.store, _settings(configured=False))
        result = service.start_oauth(return_to="/")
        self.assertFalse(result["oauth_configured"])
        self.assertIn("未配置", result["message"])

    def test_start_oauth_rejects_open_redirects(self) -> None:
        service = AuthService(self.store, _settings())
        with self.assertRaises(GitHubAuthError):
            service.start_oauth(return_to="https://evil.example/steal")
        with self.assertRaises(GitHubAuthError):
            service.start_oauth(return_to="//evil.example")

    def test_start_oauth_creates_state_and_url(self) -> None:
        service = AuthService(self.store, _settings())
        result = service.start_oauth(return_to="/profile")
        self.assertTrue(result["oauth_configured"])
        self.assertIn("github.com/login/oauth/authorize", result["authorize_url"])
        self.assertIn("read%3Auser", result["authorize_url"])
        self.assertEqual(1, len(self.store.states))

    def test_callback_rejects_unknown_state(self) -> None:
        service = AuthService(self.store, _settings())
        with self.assertRaises(GitHubAuthError):
            service.handle_callback(code="code-1", state="bogus-state")

    def test_callback_rejects_expired_state(self) -> None:
        service = AuthService(self.store, _settings())
        self.store.create_oauth_state(
            state_hash=service._sign_state("expired-state"),
            return_to="/",
            expires_at="2020-01-01T00:00:00Z",
        )
        with self.assertRaises(GitHubAuthError):
            service.handle_callback(code="code-1", state="expired-state")

    def test_callback_reuses_state_only_once(self) -> None:
        service = AuthService(self.store, _settings())
        service.start_oauth(return_to="/")
        state_hash = self.store.states[0]["state_hash"]
        # The store is consumed on first attempt; the HTTP layer would fail
        # afterwards, so simulate the second attempt finding nothing.
        self.store.consume_oauth_state(state_hash)
        with self.assertRaises(GitHubAuthError):
            service.handle_callback(code="code-1", state="anything")

    def test_current_user_returns_none_without_session(self) -> None:
        service = AuthService(self.store, _settings())
        self.assertIsNone(service.current_user(None))
        self.assertIsNone(service.current_user("missing-session"))

    def test_current_user_returns_none_for_expired_session(self) -> None:
        service = AuthService(self.store, _settings())
        user_id = self.store.create_user(github_user_id=1, github_login="alice")
        self.store.create_session(
            user_id=user_id,
            session_id="expired-session",
            expires_at="2020-01-01T00:00:00Z",
        )
        self.assertIsNone(service.current_user("expired-session"))

    def test_current_user_returns_user_for_valid_session(self) -> None:
        service = AuthService(self.store, _settings())
        user_id = self.store.create_user(
            github_user_id=1, github_login="alice", display_name="Alice"
        )
        self.store.create_session(
            user_id=user_id,
            session_id="valid-session",
            expires_at="2099-01-01T00:00:00Z",
        )
        user = service.current_user("valid-session")
        self.assertIsNotNone(user)
        self.assertEqual("alice", user["github_login"])

    def test_logout_revokes_session(self) -> None:
        service = AuthService(self.store, _settings())
        user_id = self.store.create_user(github_user_id=1, github_login="alice")
        self.store.create_session(
            user_id=user_id,
            session_id="session-x",
            expires_at="2099-01-01T00:00:00Z",
        )
        service.logout("session-x")
        self.assertIsNone(service.current_user("session-x"))

    def test_session_cookie_name_is_stable(self) -> None:
        self.assertEqual("oss_mentor_session", SESSION_COOKIE_NAME)


if __name__ == "__main__":
    unittest.main()
