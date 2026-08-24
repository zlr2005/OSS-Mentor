"""Shared contracts for OSS-Mentor v0.5.

This module is the single source of truth for cross-module enums and
data structures. Changes must be approved by all four members and the
OpenAPI document must be updated together with this file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CONTRACT_VERSION = "contracts-v0.5"

SERVICE_TRACK_NEWCOMER = "newcomer"
SERVICE_TRACK_GROWTH = "growth"
SERVICE_TRACKS = (SERVICE_TRACK_NEWCOMER, SERVICE_TRACK_GROWTH)

FEEDBACK_STATE_INTERESTED = "interested"
FEEDBACK_STATE_NOT_SUITABLE = "not_suitable"
FEEDBACK_STATE_STARTED = "started"
FEEDBACK_STATE_COMPLETED = "completed"
FEEDBACK_STATES = (
    FEEDBACK_STATE_INTERESTED,
    FEEDBACK_STATE_NOT_SUITABLE,
    FEEDBACK_STATE_STARTED,
    FEEDBACK_STATE_COMPLETED,
)

TASK_TYPE_BUG_FIX = "bug_fix"
TASK_TYPE_TESTING = "testing"
TASK_TYPE_DOCUMENTATION = "documentation"
TASK_TYPE_FEATURE = "feature"
TASK_TYPE_REFACTOR = "refactor"
TASK_TYPE_BUILD_TOOLING = "build_tooling"
TASK_TYPES = (
    TASK_TYPE_BUG_FIX,
    TASK_TYPE_TESTING,
    TASK_TYPE_DOCUMENTATION,
    TASK_TYPE_FEATURE,
    TASK_TYPE_REFACTOR,
    TASK_TYPE_BUILD_TOOLING,
)

AVAILABILITY_AVAILABLE = "available"
AVAILABILITY_CLOSED = "closed"
AVAILABILITY_ASSIGNED = "assigned"
AVAILABILITY_LINKED_OPEN_PR = "linked_open_pr"
AVAILABILITY_LOCKED = "locked"
AVAILABILITY_REPOSITORY_INACTIVE = "repository_inactive"
AVAILABILITY_TEMPORARILY_UNVERIFIED = "temporarily_unverified"
CANDIDATE_AVAILABILITY_STATES = (
    AVAILABILITY_AVAILABLE,
    AVAILABILITY_CLOSED,
    AVAILABILITY_ASSIGNED,
    AVAILABILITY_LINKED_OPEN_PR,
    AVAILABILITY_LOCKED,
    AVAILABILITY_REPOSITORY_INACTIVE,
    AVAILABILITY_TEMPORARILY_UNVERIFIED,
)

SYNC_RUN_PENDING = "pending"
SYNC_RUN_RUNNING = "running"
SYNC_RUN_SUCCEEDED = "succeeded"
SYNC_RUN_PARTIALLY_SUCCEEDED = "partially_succeeded"
SYNC_RUN_FAILED = "failed"
SYNC_RUN_STATUSES = (
    SYNC_RUN_PENDING,
    SYNC_RUN_RUNNING,
    SYNC_RUN_SUCCEEDED,
    SYNC_RUN_PARTIALLY_SUCCEEDED,
    SYNC_RUN_FAILED,
)

REASON_CODE_LANGUAGE_MATCH = "language_match"
REASON_CODE_TASK_TYPE_MATCH = "task_type_match"
REASON_CODE_SKILL_MATCH = "skill_match"
REASON_CODE_SKILL_STRETCH = "skill_stretch"
REASON_CODE_NEWCOMER_SIGNAL = "newcomer_signal"
REASON_CODE_ACTIVE_REPOSITORY = "active_repository"
REASON_CODE_FRESH_ISSUE = "fresh_issue"
REASON_CODE_CONTRIBUTING_GUIDE = "contributing_guide_available"
REASON_CODE_NEGATIVE_FEEDBACK = "negative_feedback_penalty"
REASON_CODE_DIVERSITY_RERANK = "diversity_rerank"
REASON_CODES = (
    REASON_CODE_LANGUAGE_MATCH,
    REASON_CODE_TASK_TYPE_MATCH,
    REASON_CODE_SKILL_MATCH,
    REASON_CODE_SKILL_STRETCH,
    REASON_CODE_NEWCOMER_SIGNAL,
    REASON_CODE_ACTIVE_REPOSITORY,
    REASON_CODE_FRESH_ISSUE,
    REASON_CODE_CONTRIBUTING_GUIDE,
    REASON_CODE_NEGATIVE_FEEDBACK,
    REASON_CODE_DIVERSITY_RERANK,
)


@dataclass(frozen=True, slots=True)
class Difficulty:
    code: int
    setup: int

    def __post_init__(self) -> None:
        if not 0 <= self.code <= 3 or not 0 <= self.setup <= 3:
            raise ValueError("difficulty values must be between 0 and 3")


@dataclass(frozen=True, slots=True)
class Reason:
    code: str
    label: str
    evidence: str
    score_delta: float

    def __post_init__(self) -> None:
        if self.code not in REASON_CODES:
            raise ValueError(f"unknown reason code: {self.code}")


@dataclass(frozen=True, slots=True)
class RecommendationItemV3:
    task_candidate_id: int
    repository_full_name: str
    issue_number: int
    title: str
    html_url: str
    service_track: str
    score: float
    difficulty: Difficulty
    matched_skills: tuple[str, ...]
    missing_skills: tuple[str, ...]
    reasons: tuple[Reason, ...]
    warnings: tuple[str, ...] = ()
    availability: str = AVAILABILITY_AVAILABLE
    verified_at: str | None = None
    feedback_state: str | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("score must be between 0.0 and 1.0")
        if self.service_track not in SERVICE_TRACKS:
            raise ValueError(f"invalid service_track: {self.service_track}")
        if not self.reasons:
            raise ValueError("reasons must contain at least one entry")
        if self.availability != AVAILABILITY_AVAILABLE:
            raise ValueError("unavailable tasks cannot appear in recommendations")
        if self.feedback_state is not None and self.feedback_state not in FEEDBACK_STATES:
            raise ValueError(f"invalid feedback_state: {self.feedback_state}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_candidate_id": self.task_candidate_id,
            "repository_full_name": self.repository_full_name,
            "issue_number": self.issue_number,
            "title": self.title,
            "html_url": self.html_url,
            "service_track": self.service_track,
            "score": self.score,
            "difficulty": {"code": self.difficulty.code, "setup": self.difficulty.setup},
            "matched_skills": list(self.matched_skills),
            "missing_skills": list(self.missing_skills),
            "reasons": [
                {
                    "code": reason.code,
                    "label": reason.label,
                    "evidence": reason.evidence,
                    "score_delta": reason.score_delta,
                }
                for reason in self.reasons
            ],
            "warnings": list(self.warnings),
            "availability": self.availability,
            "verified_at": self.verified_at,
            "feedback_state": self.feedback_state,
        }


@dataclass(frozen=True, slots=True)
class DeveloperProfileV2:
    profile_key: str
    display_name: str
    service_track: str
    preferred_languages: tuple[str, ...]
    operating_systems: tuple[str, ...]
    preferred_task_types: tuple[str, ...]
    max_code_difficulty: int
    max_setup_difficulty: int
    desired_skill_stretch: int
    skills: dict[str, int] = field(default_factory=dict)
    profile_source: str = "user"
    profile_version: str = "developer-profile-v0.5"

    def __post_init__(self) -> None:
        if self.service_track not in SERVICE_TRACKS:
            raise ValueError(f"invalid service_track: {self.service_track}")


@dataclass(frozen=True, slots=True)
class ErrorDetail:
    code: str
    message: str
    details: dict[str, Any] | None = None
