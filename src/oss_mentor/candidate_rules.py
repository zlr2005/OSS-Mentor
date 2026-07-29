"""Deterministic MVP candidate eligibility rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable


FEATURE_VERSION = "candidate-eligibility-v0.1"
AVAILABILITY_VERSION = "candidate-availability-v0.5"
CANDIDATE_AVAILABILITY_VALUES = frozenset(
    {
        "available",
        "closed",
        "assigned",
        "linked_open_pr",
        "locked",
        "repository_inactive",
        "temporarily_unverified",
    }
)
NEWCOMER_LABELS = frozenset(
    {
        "good first issue",
        "good-first-issue",
        "first timers only",
        "first-timers-only",
        "beginner",
        "beginner friendly",
        "beginner-friendly",
        "easy",
        "difficulty: easy",
        "help wanted",
        "help-wanted",
        "contribution welcome",
        "first contribution",
        "first-contribution",
    }
)
BLOCKING_LABELS = frozenset(
    {
        "blocked",
        "needs clarification",
        "needs info",
        "needs-info",
        "status: blocked",
        "status: needs clarification",
        "status: needs information",
        "waiting for response",
        "awaiting response",
    }
)


@dataclass(frozen=True, slots=True)
class EligibilityResult:
    eligibility: str
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    newcomer_label_signal: bool
    feature_definition_version: str = FEATURE_VERSION


@dataclass(frozen=True, slots=True)
class AvailabilityResult:
    availability: str
    reasons: tuple[str, ...]
    verified_at: str | None
    version: str = AVAILABILITY_VERSION


def has_newcomer_label(labels: Iterable[str]) -> bool:
    normalized = {label.strip().casefold() for label in labels}
    return bool(normalized.intersection(NEWCOMER_LABELS))


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def evaluate_availability(
    record: dict[str, Any],
    *,
    repository: dict[str, Any] | None = None,
    now: datetime | None = None,
    maximum_age_hours: int = 24,
) -> AvailabilityResult:
    """Map current GitHub evidence to the fixed v0.5 availability contract."""

    repository = repository or {}
    verified_at = record.get("github_verified_at")
    if (
        repository.get("is_archived")
        or repository.get("is_disabled")
        or repository.get("maintenance_status") == "inactive"
    ):
        return AvailabilityResult(
            "repository_inactive",
            ("repository_inactive",),
            verified_at,
        )
    if record.get("is_pull_request") or record.get("state") != "open":
        return AvailabilityResult("closed", ("issue_closed",), verified_at)
    if record.get("assignment_state") == "assigned":
        return AvailabilityResult("assigned", ("issue_assigned",), verified_at)
    if record.get("has_linked_open_pr") is True:
        return AvailabilityResult(
            "linked_open_pr",
            ("linked_open_pr",),
            verified_at,
        )
    if record.get("is_locked"):
        return AvailabilityResult("locked", ("issue_locked",), verified_at)

    parsed = _parse_time(verified_at)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if (
        record.get("source_system") != "github_rest"
        or not record.get("github_issue_id")
        or parsed is None
    ):
        return AvailabilityResult(
            "temporarily_unverified",
            ("requires_github_verification",),
            verified_at,
        )
    if current - parsed > timedelta(hours=maximum_age_hours):
        return AvailabilityResult(
            "temporarily_unverified",
            ("verification_older_than_limit",),
            verified_at,
        )
    return AvailabilityResult("available", (), verified_at)


def evaluate_candidate(record: dict[str, Any]) -> EligibilityResult:
    labels = record.get("labels") or []
    newcomer_signal = has_newcomer_label(labels)
    normalized_labels = {str(label).strip().casefold() for label in labels}
    reasons: list[str] = []
    warnings: list[str] = []

    if record.get("is_pull_request"):
        reasons.append("pull_request_not_issue")
    if record.get("state") != "open":
        reasons.append("not_open")
    if reasons:
        return EligibilityResult(
            "excluded", tuple(reasons), (), newcomer_signal
        )

    if record.get("assignment_state") == "assigned":
        reasons.append("already_assigned")
    if record.get("is_locked"):
        reasons.append("locked")
    if record.get("has_linked_open_pr") is True:
        reasons.append("linked_open_pr")
    blocking = sorted(normalized_labels.intersection(BLOCKING_LABELS))
    reasons.extend(f"blocking_label:{label}" for label in blocking)
    if reasons:
        return EligibilityResult(
            "temporarily_ineligible", tuple(reasons), (), newcomer_signal
        )

    if record.get("source_system") != "github_rest":
        return EligibilityResult(
            "unknown",
            ("requires_github_verification",),
            (),
            newcomer_signal,
        )
    if not record.get("github_issue_id"):
        return EligibilityResult(
            "unknown", ("missing_github_issue_id",), (), newcomer_signal
        )

    if record.get("has_linked_open_pr") is None:
        warnings.append("linked_pr_not_checked")
    if not record.get("body_text"):
        warnings.append("empty_body")
    return EligibilityResult(
        "eligible", (), tuple(warnings), newcomer_signal
    )
