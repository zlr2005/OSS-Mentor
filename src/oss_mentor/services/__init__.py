"""Service layer for OSS-Mentor v0.5."""

from oss_mentor.services.auth_service import (
    AuthService,
    AuthSettings,
    GitHubAuthError,
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
)
from oss_mentor.services.profile_service import (
    ProfileService,
    as_developer_profile_v2,
)
from oss_mentor.services.midterm_profile_service import (
    MidtermProfileNotConfirmedError,
    MidtermProfileRequiredError,
    MidtermProfileService,
    verified_developer_key,
)

__all__ = [
    "AuthService",
    "AuthSettings",
    "GitHubAuthError",
    "MidtermProfileNotConfirmedError",
    "MidtermProfileRequiredError",
    "MidtermProfileService",
    "ProfileService",
    "SESSION_COOKIE_NAME",
    "SESSION_TTL_SECONDS",
    "as_developer_profile_v2",
    "verified_developer_key",
]
