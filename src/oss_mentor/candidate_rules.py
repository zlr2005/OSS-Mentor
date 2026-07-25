"""Deterministic MVP candidate eligibility rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


FEATURE_VERSION = "candidate-eligibility-v0.1"
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


def has_newcomer_label(labels: Iterable[str]) -> bool:
    normalized = {label.strip().casefold() for label in labels}
    return bool(normalized.intersection(NEWCOMER_LABELS))


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
