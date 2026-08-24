"""GitHub OAuth and local session management for OSS-Mentor v0.5."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

OAUTH_STATE_TTL_SECONDS = 600
SESSION_TTL_SECONDS = 7 * 24 * 3600
SESSION_COOKIE_NAME = "oss_mentor_session"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _hmac_sha256(key: str, message: str) -> str:
    return hmac.new(key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


class AuthSettings:
    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        session_secret: str | None = None,
        base_url: str = "http://127.0.0.1:8765",
        github_api_base: str = "https://api.github.com",
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.session_secret = session_secret or secrets.token_hex(32)
        self.base_url = base_url.rstrip("/")
        self.github_api_base = github_api_base.rstrip("/")

    @property
    def oauth_configured(self) -> bool:
        return bool(self.client_id and self.client_secret)


class GitHubAuthError(Exception):
    pass


class AuthService:
    """Transport-independent OAuth and session logic, unit-testable without HTTP."""

    def __init__(self, store: Any, settings: AuthSettings) -> None:
        self.store = store
        self.settings = settings

    def _sign_state(self, state: str) -> str:
        return _hmac_sha256(self.settings.session_secret, state)

    def _build_authorize_url(self, state: str) -> str:
        params = {
            "client_id": self.settings.client_id or "",
            "redirect_uri": f"{self.settings.base_url}/api/v1/auth/github/callback",
            "scope": "read:user",
            "state": state,
        }
        return "https://github.com/login/oauth/authorize?" + urllib.parse.urlencode(params)

    def start_oauth(self, *, return_to: str) -> dict[str, Any]:
        """Create a one-time OAuth state and return the authorization URL."""
        if not self.settings.oauth_configured:
            return {
                "oauth_configured": False,
                "message": "GitHub OAuth 未配置。请设置 GITHUB_OAUTH_CLIENT_ID 与 GITHUB_OAUTH_CLIENT_SECRET 后重启。",
            }
        if not return_to.startswith("/") or "://" in return_to or return_to.startswith("//"):
            raise GitHubAuthError("return_to must be a local absolute path")
        state = secrets.token_urlsafe(32)
        expires_at = _utc_now() + timedelta(seconds=OAUTH_STATE_TTL_SECONDS)
        self.store.create_oauth_state(
            state_hash=self._sign_state(state),
            return_to=return_to,
            expires_at=_iso(expires_at),
        )
        return {
            "oauth_configured": True,
            "authorize_url": self._build_authorize_url(state),
            "state": state,
        }

    def _exchange_code(self, code: str) -> dict[str, Any]:
        payload = {
            "client_id": self.settings.client_id,
            "client_secret": self.settings.client_secret,
            "code": code,
            "redirect_uri": f"{self.settings.base_url}/api/v1/auth/github/callback",
        }
        request = urllib.request.Request(
            "https://github.com/login/oauth/access_token",
            data=urllib.parse.urlencode(payload).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "oss-mentor",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
            raise GitHubAuthError(f"github token exchange failed: {exc}") from exc

    def _fetch_github_user(self, access_token: str) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.settings.github_api_base}/user",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {access_token}",
                "User-Agent": "oss-mentor",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
            raise GitHubAuthError(f"github user fetch failed: {exc}") from exc

    def handle_callback(self, *, code: str, state: str) -> dict[str, Any]:
        """Validate state, exchange the code, and create a user session."""
        state_record = self.store.consume_oauth_state(state_hash=self._sign_state(state))
        if state_record is None:
            raise GitHubAuthError("invalid or expired oauth state")
        expires_at = datetime.strptime(state_record["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        if expires_at < _utc_now():
            raise GitHubAuthError("oauth state has expired")
        if not self.settings.oauth_configured:
            raise GitHubAuthError("github oauth is not configured")

        token_response = self._exchange_code(code)
        access_token = token_response.get("access_token")
        if not access_token:
            raise GitHubAuthError(
                "github token exchange did not return an access token: "
                f"{token_response.get('error', 'unknown error')}"
            )
        github_user = self._fetch_github_user(access_token)
        github_user_id = github_user.get("id")
        github_login = github_user.get("login")
        if not github_user_id or not github_login:
            raise GitHubAuthError("github user response is missing id or login")

        user = self.store.find_user_by_github_id(int(github_user_id))
        if user is None:
            user_id = self.store.create_user(
                github_user_id=int(github_user_id),
                github_login=str(github_login),
                display_name=github_user.get("name") or github_user.get("login"),
            )
        else:
            user_id = int(user["user_id"])
            if user.get("deleted_at"):
                raise GitHubAuthError("user account is disabled")

        self.store.store_oauth_credential(
            user_id=user_id,
            access_token_ref=_hmac_sha256(self.settings.session_secret, str(access_token)),
            scope=str(token_response.get("scope", "read:user")),
            expires_at=None,
        )

        session_id = secrets.token_urlsafe(32)
        session_expires = _utc_now() + timedelta(seconds=SESSION_TTL_SECONDS)
        self.store.create_session(
            user_id=user_id,
            session_id=session_id,
            expires_at=_iso(session_expires),
        )
        return {
            "session_id": session_id,
            "expires_at": _iso(session_expires),
            "return_to": state_record["return_to"],
            "user": {"github_login": github_login, "github_user_id": int(github_user_id)},
        }

    def current_user(self, session_id: str | None) -> dict[str, Any] | None:
        """Return the authenticated user for a session ID, or None."""
        if not session_id:
            return None
        session = self.store.find_session(session_id)
        if session is None:
            return None
        expires_at = datetime.strptime(session["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        if expires_at < _utc_now() or session.get("revoked_at"):
            return None
        user = self.store.find_user(session["user_id"])
        if user is None or user.get("deleted_at"):
            return None
        return {
            "user_id": int(user["user_id"]),
            "github_login": str(user["github_login"]),
            "display_name": user.get("display_name"),
        }

    def logout(self, session_id: str | None) -> None:
        if session_id:
            self.store.revoke_session(session_id)
