"""Storage interface Protocols for OSS-Mentor v0.5.

Business modules must depend on these interfaces, never on concrete
SQLite SQL. SQLite and PostgreSQL implementations must satisfy the
same Protocols so contract tests can run against both.
"""

from __future__ import annotations

from typing import ContextManager, Protocol, runtime_checkable

from oss_mentor.contracts import (
    DeveloperProfileV2,
    RecommendationBatchV3,
    RecommendationItemV3,
)
from oss_mentor.contracts_midterm import (
    AssessmentConfirmation,
    AssessmentSubmission,
    ConfirmationDecision,
    ConfirmationPlan,
    EnhancedProfileFeatures,
    ProfileFeatureState,
    ProfilePreview,
)


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
    """Developer profile persistence shared by B's service and D's API.

    ``user_id`` is the authenticated ``oss_user.user_id`` from migration
    007. ``profile_key`` remains the public profile identifier, but protected
    HTTP routes must resolve it from the current session rather than accept an
    arbitrary profile key from the client.
    """

    def upsert_profile(self, profile: DeveloperProfileV2) -> int: ...

    def initialize(self) -> None: ...

    def transaction(self) -> ContextManager[ProfileStore]: ...

    def save_profile(
        self,
        profile: dict,
        *,
        user_id: int | None = None,
        user_key: str | None = None,
    ) -> int: ...

    def load_profile(self, profile_key: str) -> dict | None: ...

    def profile_for_user(self, user_id: int) -> dict | None: ...

    def save_github_import(
        self,
        *,
        profile_key: str,
        github_import: dict,
        merge_preview: dict,
    ) -> dict: ...

    def list_suggestions(
        self,
        *,
        profile_key: str,
        status: str | None = None,
    ) -> list[dict]: ...

    def mark_suggestion_status(
        self,
        suggestion_id: int,
        *,
        status: str,
    ) -> None: ...

    def delete_profile(self, profile_key: str) -> bool: ...

    def list_profiles_public(self) -> list[dict]: ...


@runtime_checkable
class MidtermProfileStore(Protocol):
    """Persistence boundary for the B-owned midterm profile workflow.

    ``user_id`` always comes from ``oss_user.user_id`` resolved by the current
    session.  Implementations must resolve the owned ``developer_profile`` /
    ``profile_key`` from that user and must never trust a browser-supplied
    profile key, developer key or owner ID.

    ``transaction()`` must use SQLite ``BEGIN IMMEDIATE`` (or an equivalent
    serializable boundary) and yield a store bound to that transaction.
    Confirmation commits must happen inside this boundary.
    """

    def initialize(self) -> None: ...

    def transaction(self) -> ContextManager[MidtermProfileStore]: ...

    def put_questionnaire(
        self,
        *,
        questionnaire_id: str,
        questionnaire_version: str,
        schema_version: str,
        feature_schema_version: str,
        definition: dict,
        definition_sha256: str,
    ) -> None: ...

    def get_questionnaire(
        self,
        *,
        questionnaire_id: str,
        questionnaire_version: str,
    ) -> dict | None: ...

    def create_assessment_submission(
        self,
        *,
        user_id: int,
        submission: AssessmentSubmission,
        idempotency_key: str,
    ) -> str: ...

    def get_assessment_submission(
        self,
        *,
        user_id: int,
        submission_id: str,
    ) -> dict | None: ...

    def create_profile_preview(
        self,
        *,
        user_id: int,
        preview: ProfilePreview,
        input_sha256: str,
    ) -> str: ...

    def get_profile_preview(
        self,
        *,
        user_id: int,
        preview_id: str,
    ) -> ProfilePreview | None: ...

    def create_confirmation_plan(
        self,
        *,
        user_id: int,
        plan: ConfirmationPlan,
    ) -> str: ...

    def get_confirmation_plan(
        self,
        *,
        user_id: int,
        plan_id: str,
    ) -> ConfirmationPlan | None: ...

    def get_current_feature_state(
        self,
        *,
        user_id: int,
    ) -> ProfileFeatureState | None: ...

    def get_feature_state(
        self,
        *,
        user_id: int,
        profile_version: str,
    ) -> ProfileFeatureState | None: ...

    def find_confirmation_by_idempotency_key(
        self,
        *,
        user_id: int,
        idempotency_key: str,
    ) -> AssessmentConfirmation | None: ...

    def commit_assessment_confirmation(
        self,
        *,
        user_id: int,
        confirmation: AssessmentConfirmation,
        decisions: tuple[ConfirmationDecision, ...],
        feature_state: ProfileFeatureState,
        idempotency_key: str,
    ) -> AssessmentConfirmation: ...

    def get_enhanced_profile_features(
        self,
        *,
        user_id: int,
        profile_version: str | None = None,
        developer_key: str | None = None,
    ) -> EnhancedProfileFeatures | None: ...


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

    def save_recommendation_batch(self, batch: RecommendationBatchV3) -> None: ...

    def find_recommendation_batch(self, run_id: str) -> dict | None: ...
