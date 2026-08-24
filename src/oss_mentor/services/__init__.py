"""Service layer for OSS-Mentor v0.5."""

from oss_mentor.services.auth_service import (
    AuthService,
    AuthSettings,
    GitHubAuthError,
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
)

__all__ = [
    "AuthService",
    "AuthSettings",
    "GitHubAuthError",
    "SESSION_COOKIE_NAME",
    "SESSION_TTL_SECONDS",
]
