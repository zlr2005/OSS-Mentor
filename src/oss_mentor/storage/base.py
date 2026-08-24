"""Storage interface Protocols for OSS-Mentor v0.5.

Business modules must depend on these interfaces, never on concrete
SQLite SQL. SQLite and PostgreSQL implementations must satisfy the
same Protocols so contract tests can run against both.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from oss_mentor.contracts import DeveloperProfileV2, RecommendationItemV3


@runtime_checkable
class IdentitySessionStore(Protocol):
    """User identity, OAuth credentials and session persistence."""

    def create_user(self, *, github_user_id: int, github_login: str) -> int: ...

    def find_user_by_github_id(self, github_user_id: int) -> dict | None: ...

    def store_oauth_credential(
        self, *, user_id: int, access_token_ref: str, scope: str, expires_at: str | None
    ) -> int: ...

    def create_session(
        self,
        *,
        user_id: int,
        session_id: str,
        expires_at: str,
        oauth_state_hash: str | None = None,
    ) -> int: ...

    def find_session(self, session_id: str) -> dict | None: ...

    def revoke_session(self, session_id: str) -> None: ...

    def consume_oauth_state(self, state: str) -> dict | None: ...


@runtime_checkable
class ProfileStore(Protocol):
    """Developer profile persistence (owned by member B)."""

    def upsert_profile(self, profile: DeveloperProfileV2) -> int: ...

    def profile_for_user(self, user_id: int) -> dict | None: ...

    def list_profiles_public(self) -> list[dict]: ...


@runtime_checkable
class CandidateStore(Protocol):
    """Candidate task persistence (owned by member A)."""

    def matchable_candidates(self) -> list[dict]: ...

    def candidate_detail(self, task_candidate_id: int) -> dict | None: ...

    def feedback_states(
        self, feedback_context: str, task_candidate_ids: list[int]
    ) -> dict[int, str]: ...

    def record_feedback(
        self,
        *,
        task_candidate_id: int,
        feedback_context: str,
        service_track: str,
        feedback_state: str,
    ) -> dict: ...

    def feedback_summary(self) -> dict: ...


@runtime_checkable
class RecommendationStore(Protocol):
    """Recommendation snapshot persistence (owned by member C)."""

    def save_recommendation_batch(
        self,
        *,
        run_id: str,
        profile_hash: str,
        candidate_hash: str,
        match_version: str,
        items: list[RecommendationItemV3],
    ) -> None: ...

    def find_recommendation_batch(self, run_id: str) -> dict | None: ...
