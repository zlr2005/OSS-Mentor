"""Frozen shared contracts for the OSS-Mentor midterm profile workflow.

This module is the D-owned boundary between B's assessment business rules,
the platform session/storage layer, and C's recommendation inputs.  Private
B dictionaries remain allowed behind an adapter, but API and storage code
must exchange only the types declared here.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


CONTRACTS_MIDTERM_VERSION = "contracts-midterm-v0.6.0"

QUESTIONNAIRE_SCHEMA_VERSION = "profile-questionnaire-schema-v0.1"
ASSESSMENT_PREVIEW_SCHEMA_VERSION = "assessment-preview-v0.1"
PROFILE_FEATURE_STATE_SCHEMA_VERSION = "profile-feature-state-v0.1"
CONFIRMATION_PLAN_SCHEMA_VERSION = "assessment-confirmation-plan-v0.1"
CONFIRMATION_RESULT_SCHEMA_VERSION = "assessment-confirmation-result-v0.1"
ENHANCED_PROFILE_FEATURES_SCHEMA_VERSION = "enhanced-profile-export-v0.1"

EVIDENCE_SOURCE_GITHUB_WEAK = "github_weak"
EVIDENCE_SOURCE_ASSESSMENT_SELF_REPORT = "assessment_self_report"
EVIDENCE_SOURCE_GITHUB_DIRECT = "github_direct"
EVIDENCE_SOURCE_USER_CONFIRMED = "user_confirmed"
EVIDENCE_SOURCE_USER_LOCKED = "user_locked"
PROFILE_EVIDENCE_SOURCE_KINDS = (
    EVIDENCE_SOURCE_GITHUB_WEAK,
    EVIDENCE_SOURCE_ASSESSMENT_SELF_REPORT,
    EVIDENCE_SOURCE_GITHUB_DIRECT,
    EVIDENCE_SOURCE_USER_CONFIRMED,
    EVIDENCE_SOURCE_USER_LOCKED,
)

CONFIRMATION_DECISION_ACCEPT = "accept"
CONFIRMATION_DECISION_REJECT = "reject"
CONFIRMATION_DECISIONS = (
    CONFIRMATION_DECISION_ACCEPT,
    CONFIRMATION_DECISION_REJECT,
)

CONFIRMATION_SUGGESTION_PENDING = "pending"
CONFIRMATION_SUGGESTION_BLOCKED = "blocked"
CONFIRMATION_SUGGESTION_ACCEPTED = "accepted"
CONFIRMATION_SUGGESTION_REJECTED = "rejected"
CONFIRMATION_SUGGESTION_STATUSES = (
    CONFIRMATION_SUGGESTION_PENDING,
    CONFIRMATION_SUGGESTION_BLOCKED,
    CONFIRMATION_SUGGESTION_ACCEPTED,
    CONFIRMATION_SUGGESTION_REJECTED,
)

CONFIRMATION_PLAN_OPEN = "open"
CONFIRMATION_PLAN_COMPLETED = "completed"
CONFIRMATION_PLAN_STALE = "stale"
CONFIRMATION_PLAN_STATUSES = (
    CONFIRMATION_PLAN_OPEN,
    CONFIRMATION_PLAN_COMPLETED,
    CONFIRMATION_PLAN_STALE,
)


class MidtermContractError(ValueError):
    """Base error for invalid or conflicting midterm contract operations."""


class StalePlanError(MidtermContractError):
    """The plan's base profile version no longer matches the current profile."""

    code = "state_conflict"


class IdempotencyConflictError(MidtermContractError):
    """An idempotency key was reused for a different confirmation payload."""

    code = "state_conflict"


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _utc(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a UTC ISO 8601 string")
    raw = value.strip()
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _unit(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number")
    normalized = float(value)
    if not 0.0 <= normalized <= 1.0:
        raise ValueError(f"{field_name} must be between 0 and 1")
    return normalized


def _boolean(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be boolean")
    return value


def _text_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be an array")
    normalized: list[str] = []
    for item in value:
        normalized.append(_text(item, field_name))
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} must contain unique values")
    return tuple(normalized)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {
            item.name: _jsonable(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class ProfileEvidence:
    """One attributable piece of evidence for a single profile feature."""

    feature_name: str
    value: float
    confidence: float
    source_kind: str
    evidence_ref: str
    observed_at: str
    locked: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        source_kind = _text(self.source_kind, "source_kind")
        if source_kind not in PROFILE_EVIDENCE_SOURCE_KINDS:
            raise ValueError(f"unsupported source_kind: {source_kind}")
        locked = _boolean(self.locked, "locked")
        if (source_kind == EVIDENCE_SOURCE_USER_LOCKED) != locked:
            raise ValueError("locked must be true exactly for source_kind=user_locked")
        if not isinstance(self.details, dict):
            raise ValueError("details must be an object")
        object.__setattr__(self, "feature_name", _text(self.feature_name, "feature_name"))
        object.__setattr__(self, "source_kind", source_kind)
        object.__setattr__(self, "evidence_ref", _text(self.evidence_ref, "evidence_ref"))
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))
        object.__setattr__(self, "value", _unit(self.value, "value"))
        object.__setattr__(self, "confidence", _unit(self.confidence, "confidence"))
        object.__setattr__(self, "locked", locked)
        object.__setattr__(self, "details", dict(self.details))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ProfileEvidence:
        known = {
            "feature_name",
            "value",
            "confidence",
            "source_kind",
            "evidence_ref",
            "observed_at",
            "locked",
            "details",
        }
        details = dict(payload.get("details") or {})
        details.update({key: value for key, value in payload.items() if key not in known})
        return cls(
            feature_name=payload.get("feature_name"),
            value=payload.get("value"),
            confidence=payload.get("confidence"),
            source_kind=payload.get("source_kind"),
            evidence_ref=payload.get("evidence_ref"),
            observed_at=payload.get("observed_at"),
            locked=payload.get("locked", payload.get("source_kind") == EVIDENCE_SOURCE_USER_LOCKED),
            details=details,
        )

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True, slots=True)
class AssessmentSubmission:
    """Browser answer payload with no identity fields.

    ``user_id``, ``developer_key`` and ``profile_key`` must be supplied by
    the platform service after resolving the session.
    """

    questionnaire_id: str
    questionnaire_version: str
    submitted_at: str
    answers: dict[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.answers, dict):
            raise ValueError("answers must be an object")
        normalized: dict[str, Any] = {}
        for question_id, answer in self.answers.items():
            key = _text(question_id, "answers question id")
            if isinstance(answer, str):
                normalized[key] = _text(answer, f"answer for {key}")
            elif isinstance(answer, (list, tuple)):
                values = [_text(item, f"answer for {key}") for item in answer]
                if len(values) != len(set(values)):
                    raise ValueError(f"answer for {key} must contain unique option ids")
                normalized[key] = values
            else:
                raise ValueError(f"answer for {key} must be a string or array")
        object.__setattr__(self, "questionnaire_id", _text(self.questionnaire_id, "questionnaire_id"))
        object.__setattr__(
            self,
            "questionnaire_version",
            _text(self.questionnaire_version, "questionnaire_version"),
        )
        object.__setattr__(self, "submitted_at", _utc(self.submitted_at, "submitted_at"))
        object.__setattr__(self, "answers", normalized)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AssessmentSubmission:
        return cls(
            questionnaire_id=payload.get("questionnaire_id"),
            questionnaire_version=payload.get("questionnaire_version"),
            submitted_at=payload.get("submitted_at"),
            answers=payload.get("answers"),
        )

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True, slots=True)
class ProfileFeature:
    """One feature row in an assessment preview."""

    feature_name: str
    observed: bool
    proposed_value: float
    confidence: float
    source_kind: str | None
    evidence_refs: tuple[str, ...] = ()
    conflict: bool = False
    blocked_reason: str | None = None

    def __post_init__(self) -> None:
        observed = _boolean(self.observed, "observed")
        confidence = _unit(self.confidence, "confidence")
        value = _unit(self.proposed_value, "proposed_value")
        source_kind = self.source_kind
        if source_kind is not None:
            source_kind = _text(source_kind, "source_kind")
            if source_kind not in PROFILE_EVIDENCE_SOURCE_KINDS:
                raise ValueError(f"unsupported source_kind: {source_kind}")
        if not observed and value != 0.0:
            raise ValueError("an unobserved preview feature must use value 0")
        if self.blocked_reason not in {None, "field_locked"}:
            raise ValueError("blocked_reason must be null or field_locked")
        object.__setattr__(self, "feature_name", _text(self.feature_name, "feature_name"))
        object.__setattr__(self, "observed", observed)
        object.__setattr__(self, "proposed_value", value)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "source_kind", source_kind)
        object.__setattr__(self, "evidence_refs", _text_tuple(self.evidence_refs, "evidence_refs"))
        object.__setattr__(self, "conflict", _boolean(self.conflict, "conflict"))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ProfileFeature:
        return cls(
            feature_name=payload.get("feature_name"),
            observed=payload.get("observed"),
            proposed_value=payload.get("proposed_value", payload.get("value", 0.0)),
            confidence=payload.get("confidence", 0.0),
            source_kind=payload.get("source_kind"),
            evidence_refs=tuple(payload.get("evidence_refs") or ()),
            conflict=payload.get("conflict", False),
            blocked_reason=payload.get("blocked_reason"),
        )

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True, slots=True)
class ProfilePreview:
    """Persisted, user-owned assessment preview.

    The browser receives ``preview_id`` and may reference it later, but cannot
    choose ``profile_key`` or ``base_profile_version``.
    """

    preview_id: str
    profile_key: str
    base_profile_version: str
    questionnaire_id: str
    questionnaire_version: str
    feature_schema_version: str
    submitted_at: str
    answers: dict[str, Any]
    features: tuple[ProfileFeature, ...]
    current_interests: tuple[str, ...]
    interpretation: str
    persisted: bool = True

    def __post_init__(self) -> None:
        if not self.features:
            raise ValueError("features must not be empty")
        feature_names = [item.feature_name for item in self.features]
        if feature_names != sorted(feature_names) or len(feature_names) != len(set(feature_names)):
            raise ValueError("features must be unique and sorted by feature_name")
        interests = _text_tuple(self.current_interests, "current_interests")
        if not set(interests).issubset(feature_names):
            raise ValueError("current_interests must reference preview features")
        object.__setattr__(self, "preview_id", _text(self.preview_id, "preview_id"))
        object.__setattr__(self, "profile_key", _text(self.profile_key, "profile_key"))
        object.__setattr__(
            self,
            "base_profile_version",
            _text(self.base_profile_version, "base_profile_version"),
        )
        object.__setattr__(self, "questionnaire_id", _text(self.questionnaire_id, "questionnaire_id"))
        object.__setattr__(
            self,
            "questionnaire_version",
            _text(self.questionnaire_version, "questionnaire_version"),
        )
        object.__setattr__(
            self,
            "feature_schema_version",
            _text(self.feature_schema_version, "feature_schema_version"),
        )
        object.__setattr__(self, "submitted_at", _utc(self.submitted_at, "submitted_at"))
        object.__setattr__(self, "answers", dict(self.answers))
        object.__setattr__(self, "features", tuple(self.features))
        object.__setattr__(self, "current_interests", interests)
        object.__setattr__(self, "interpretation", _text(self.interpretation, "interpretation"))
        object.__setattr__(self, "persisted", _boolean(self.persisted, "persisted"))

    @classmethod
    def from_private(
        cls,
        payload: Mapping[str, Any],
        *,
        preview_id: str,
        profile_key: str,
        base_profile_version: str,
    ) -> ProfilePreview:
        return cls(
            preview_id=preview_id,
            profile_key=profile_key,
            base_profile_version=base_profile_version,
            questionnaire_id=_text(payload.get("questionnaire_id"), "questionnaire_id"),
            questionnaire_version=payload.get("questionnaire_version"),
            feature_schema_version=payload.get("feature_schema_version"),
            submitted_at=payload.get("submitted_at"),
            answers=payload.get("answers") or {},
            features=tuple(ProfileFeature.from_dict(item) for item in payload.get("features") or ()),
            current_interests=tuple(payload.get("current_interests") or ()),
            interpretation=payload.get("interpretation"),
            persisted=True,
        )

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True, slots=True)
class ProfileFeatureStateRow:
    """One confirmed/current feature value."""

    feature_name: str
    observed: bool
    value: float
    locked: bool
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        observed = _boolean(self.observed, "observed")
        locked = _boolean(self.locked, "locked")
        value = _unit(self.value, "value")
        if not observed and value != 0.0:
            raise ValueError("an unobserved feature must use value 0")
        if locked and not observed:
            raise ValueError("an unobserved feature cannot be locked")
        object.__setattr__(self, "feature_name", _text(self.feature_name, "feature_name"))
        object.__setattr__(self, "observed", observed)
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "locked", locked)
        object.__setattr__(self, "evidence_refs", _text_tuple(self.evidence_refs, "evidence_refs"))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ProfileFeatureStateRow:
        return cls(
            feature_name=payload.get("feature_name"),
            observed=payload.get("observed"),
            value=payload.get("value", 0.0),
            locked=payload.get("locked", False),
            evidence_refs=tuple(payload.get("evidence_refs") or ()),
        )

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True, slots=True)
class ProfileFeatureState:
    """Versioned complete feature state used by confirmation and export."""

    profile_key: str
    profile_version: str
    feature_schema_version: str
    updated_at: str
    features: tuple[ProfileFeatureStateRow, ...]
    schema_version: str = PROFILE_FEATURE_STATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROFILE_FEATURE_STATE_SCHEMA_VERSION:
            raise ValueError("unsupported profile feature state schema_version")
        names = [item.feature_name for item in self.features]
        if not names or names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("features must be unique and sorted by feature_name")
        object.__setattr__(self, "profile_key", _text(self.profile_key, "profile_key"))
        object.__setattr__(self, "profile_version", _text(self.profile_version, "profile_version"))
        object.__setattr__(
            self,
            "feature_schema_version",
            _text(self.feature_schema_version, "feature_schema_version"),
        )
        object.__setattr__(self, "updated_at", _utc(self.updated_at, "updated_at"))
        object.__setattr__(self, "features", tuple(self.features))

    @classmethod
    def from_private(cls, payload: Mapping[str, Any]) -> ProfileFeatureState:
        return cls(
            schema_version=payload.get("schema_version"),
            profile_key=payload.get("profile_key"),
            profile_version=payload.get("profile_version"),
            feature_schema_version=payload.get("feature_schema_version"),
            updated_at=payload.get("updated_at"),
            features=tuple(
                ProfileFeatureStateRow.from_dict(item)
                for item in payload.get("features") or ()
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True, slots=True)
class FeatureValueSnapshot:
    observed: bool
    value: float
    locked: bool
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed", _boolean(self.observed, "observed"))
        object.__setattr__(self, "value", _unit(self.value, "value"))
        object.__setattr__(self, "locked", _boolean(self.locked, "locked"))
        object.__setattr__(self, "evidence_refs", _text_tuple(self.evidence_refs, "evidence_refs"))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> FeatureValueSnapshot:
        return cls(
            observed=payload.get("observed"),
            value=payload.get("value", 0.0),
            locked=payload.get("locked", False),
            evidence_refs=tuple(payload.get("evidence_refs") or ()),
        )

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True, slots=True)
class FeatureProposal:
    observed: bool
    value: float
    confidence: float
    source_kind: str | None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        source_kind = self.source_kind
        if source_kind is not None:
            source_kind = _text(source_kind, "source_kind")
            if source_kind not in PROFILE_EVIDENCE_SOURCE_KINDS:
                raise ValueError(f"unsupported source_kind: {source_kind}")
        object.__setattr__(self, "observed", _boolean(self.observed, "observed"))
        object.__setattr__(self, "value", _unit(self.value, "value"))
        object.__setattr__(self, "confidence", _unit(self.confidence, "confidence"))
        object.__setattr__(self, "source_kind", source_kind)
        object.__setattr__(self, "evidence_refs", _text_tuple(self.evidence_refs, "evidence_refs"))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> FeatureProposal:
        return cls(
            observed=payload.get("observed"),
            value=payload.get("value", 0.0),
            confidence=payload.get("confidence", 0.0),
            source_kind=payload.get("source_kind"),
            evidence_refs=tuple(payload.get("evidence_refs") or ()),
        )

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True, slots=True)
class ConfirmationSuggestion:
    suggestion_id: str
    feature_name: str
    current: FeatureValueSnapshot
    proposed: FeatureProposal
    conflict: bool
    status: str
    blocked_reason: str | None = None

    def __post_init__(self) -> None:
        status = _text(self.status, "status")
        if status not in CONFIRMATION_SUGGESTION_STATUSES:
            raise ValueError(f"unsupported suggestion status: {status}")
        if self.blocked_reason not in {None, "field_locked"}:
            raise ValueError("blocked_reason must be null or field_locked")
        if status == CONFIRMATION_SUGGESTION_BLOCKED and self.blocked_reason != "field_locked":
            raise ValueError("blocked suggestions must have blocked_reason=field_locked")
        object.__setattr__(self, "suggestion_id", _text(self.suggestion_id, "suggestion_id"))
        object.__setattr__(self, "feature_name", _text(self.feature_name, "feature_name"))
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "conflict", _boolean(self.conflict, "conflict"))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ConfirmationSuggestion:
        return cls(
            suggestion_id=payload.get("suggestion_id"),
            feature_name=payload.get("feature_name"),
            current=FeatureValueSnapshot.from_dict(payload.get("current") or {}),
            proposed=FeatureProposal.from_dict(payload.get("proposed") or {}),
            conflict=payload.get("conflict", False),
            status=payload.get("status"),
            blocked_reason=payload.get("blocked_reason"),
        )

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True, slots=True)
class ConfirmationPlan:
    plan_id: str
    profile_key: str
    base_profile_version: str
    preview_id: str
    questionnaire_version: str
    feature_schema_version: str
    preview_submitted_at: str
    suggestions: tuple[ConfirmationSuggestion, ...]
    created_at: str
    status: str = CONFIRMATION_PLAN_OPEN
    persisted: bool = True

    def __post_init__(self) -> None:
        status = _text(self.status, "status")
        if status not in CONFIRMATION_PLAN_STATUSES:
            raise ValueError(f"unsupported plan status: {status}")
        suggestion_ids = [item.suggestion_id for item in self.suggestions]
        if len(suggestion_ids) != len(set(suggestion_ids)):
            raise ValueError("suggestions must have unique suggestion_id values")
        object.__setattr__(self, "plan_id", _text(self.plan_id, "plan_id"))
        object.__setattr__(self, "profile_key", _text(self.profile_key, "profile_key"))
        object.__setattr__(
            self,
            "base_profile_version",
            _text(self.base_profile_version, "base_profile_version"),
        )
        object.__setattr__(self, "preview_id", _text(self.preview_id, "preview_id"))
        object.__setattr__(
            self,
            "questionnaire_version",
            _text(self.questionnaire_version, "questionnaire_version"),
        )
        object.__setattr__(
            self,
            "feature_schema_version",
            _text(self.feature_schema_version, "feature_schema_version"),
        )
        object.__setattr__(
            self,
            "preview_submitted_at",
            _utc(self.preview_submitted_at, "preview_submitted_at"),
        )
        object.__setattr__(self, "suggestions", tuple(self.suggestions))
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "persisted", _boolean(self.persisted, "persisted"))

    @classmethod
    def from_private(
        cls,
        payload: Mapping[str, Any],
        *,
        preview_id: str,
        created_at: str,
    ) -> ConfirmationPlan:
        return cls(
            plan_id=payload.get("plan_id"),
            profile_key=payload.get("profile_key"),
            base_profile_version=payload.get("base_profile_version"),
            preview_id=preview_id,
            questionnaire_version=payload.get("questionnaire_version"),
            feature_schema_version=payload.get("feature_schema_version"),
            preview_submitted_at=payload.get("preview_submitted_at"),
            suggestions=tuple(
                ConfirmationSuggestion.from_dict(item)
                for item in payload.get("suggestions") or ()
            ),
            created_at=created_at,
            status=CONFIRMATION_PLAN_OPEN,
            persisted=True,
        )

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True, slots=True)
class ConfirmationDecision:
    suggestion_id: str
    feature_name: str
    decision: str
    conflict_was_visible: bool
    decided_at: str

    def __post_init__(self) -> None:
        decision = _text(self.decision, "decision")
        if decision not in CONFIRMATION_DECISIONS:
            raise ValueError(f"unsupported decision: {decision}")
        object.__setattr__(self, "suggestion_id", _text(self.suggestion_id, "suggestion_id"))
        object.__setattr__(self, "feature_name", _text(self.feature_name, "feature_name"))
        object.__setattr__(self, "decision", decision)
        object.__setattr__(
            self,
            "conflict_was_visible",
            _boolean(self.conflict_was_visible, "conflict_was_visible"),
        )
        object.__setattr__(self, "decided_at", _utc(self.decided_at, "decided_at"))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ConfirmationDecision:
        return cls(
            suggestion_id=payload.get("suggestion_id"),
            feature_name=payload.get("feature_name"),
            decision=payload.get("decision"),
            conflict_was_visible=payload.get("conflict_was_visible", False),
            decided_at=payload.get("decided_at", payload.get("confirmed_at")),
        )

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True, slots=True)
class AssessmentConfirmation:
    confirmation_id: str
    plan_id: str
    profile_key: str
    base_profile_version: str
    new_profile_version: str
    confirmed_at: str
    decisions: tuple[ConfirmationDecision, ...]
    applied_suggestion_ids: tuple[str, ...]
    rejected_suggestion_ids: tuple[str, ...]
    remaining_suggestion_ids: tuple[str, ...]
    blocked_suggestion_ids: tuple[str, ...]
    profile_state: ProfileFeatureState
    persisted: bool = True

    def __post_init__(self) -> None:
        suggestion_ids = [item.suggestion_id for item in self.decisions]
        if len(suggestion_ids) != len(set(suggestion_ids)):
            raise ValueError("decisions must have unique suggestion_id values")
        if self.profile_state.profile_version != self.new_profile_version:
            raise ValueError("profile_state.profile_version must equal new_profile_version")
        if self.profile_state.profile_key != self.profile_key:
            raise ValueError("profile_state.profile_key must equal confirmation.profile_key")
        object.__setattr__(self, "confirmation_id", _text(self.confirmation_id, "confirmation_id"))
        object.__setattr__(self, "plan_id", _text(self.plan_id, "plan_id"))
        object.__setattr__(self, "profile_key", _text(self.profile_key, "profile_key"))
        object.__setattr__(
            self,
            "base_profile_version",
            _text(self.base_profile_version, "base_profile_version"),
        )
        object.__setattr__(
            self,
            "new_profile_version",
            _text(self.new_profile_version, "new_profile_version"),
        )
        object.__setattr__(self, "confirmed_at", _utc(self.confirmed_at, "confirmed_at"))
        object.__setattr__(self, "decisions", tuple(self.decisions))
        object.__setattr__(
            self,
            "applied_suggestion_ids",
            _text_tuple(self.applied_suggestion_ids, "applied_suggestion_ids"),
        )
        object.__setattr__(
            self,
            "rejected_suggestion_ids",
            _text_tuple(self.rejected_suggestion_ids, "rejected_suggestion_ids"),
        )
        object.__setattr__(
            self,
            "remaining_suggestion_ids",
            _text_tuple(self.remaining_suggestion_ids, "remaining_suggestion_ids"),
        )
        object.__setattr__(
            self,
            "blocked_suggestion_ids",
            _text_tuple(self.blocked_suggestion_ids, "blocked_suggestion_ids"),
        )
        object.__setattr__(self, "persisted", _boolean(self.persisted, "persisted"))

    @classmethod
    def from_private(cls, payload: Mapping[str, Any]) -> AssessmentConfirmation:
        confirmed_at = payload.get("confirmed_at")
        return cls(
            confirmation_id=payload.get("confirmation_id"),
            plan_id=payload.get("plan_id"),
            profile_key=(payload.get("profile_state") or {}).get("profile_key"),
            base_profile_version=payload.get("base_profile_version"),
            new_profile_version=payload.get("new_profile_version"),
            confirmed_at=confirmed_at,
            decisions=tuple(
                ConfirmationDecision.from_dict({**item, "confirmed_at": confirmed_at})
                for item in payload.get("decisions") or ()
            ),
            applied_suggestion_ids=tuple(payload.get("applied_suggestion_ids") or ()),
            rejected_suggestion_ids=tuple(payload.get("rejected_suggestion_ids") or ()),
            remaining_suggestion_ids=tuple(payload.get("remaining_suggestion_ids") or ()),
            blocked_suggestion_ids=tuple(payload.get("blocked_suggestion_ids") or ()),
            profile_state=ProfileFeatureState.from_private(payload.get("profile_state") or {}),
            persisted=True,
        )

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True, slots=True)
class EnhancedProfileFeatures:
    """Confirmed feature export consumed by C."""

    profile_key: str
    profile_version: str
    feature_schema_version: str
    as_of: str
    feature_names: tuple[str, ...]
    values: tuple[float, ...]
    observed_mask: tuple[bool, ...]
    locked_mask: tuple[bool, ...]
    current_interests: tuple[str, ...]
    evidence_refs: tuple[tuple[str, ...], ...]
    developer_key: str | None = None
    profile_state: str = "confirmed"
    interpretation: str = "confirmed_profile_features_not_verified_ability_probabilities"
    schema_version: str = ENHANCED_PROFILE_FEATURES_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ENHANCED_PROFILE_FEATURES_SCHEMA_VERSION:
            raise ValueError("unsupported enhanced profile schema_version")
        if self.profile_state != "confirmed":
            raise ValueError("only confirmed enhanced profiles can be exported")
        names = _text_tuple(self.feature_names, "feature_names")
        if not names or names != tuple(sorted(names)):
            raise ValueError("feature_names must be non-empty and sorted")
        total = len(names)
        if not (
            len(self.values)
            == len(self.observed_mask)
            == len(self.locked_mask)
            == len(self.evidence_refs)
            == total
        ):
            raise ValueError("feature arrays must align with feature_names")
        values = tuple(_unit(item, "value") for item in self.values)
        observed_mask = tuple(_boolean(item, "observed_mask") for item in self.observed_mask)
        locked_mask = tuple(_boolean(item, "locked_mask") for item in self.locked_mask)
        if any(not observed and value != 0.0 for observed, value in zip(observed_mask, values)):
            raise ValueError("unobserved features must use value 0")
        if any(locked and not observed for locked, observed in zip(locked_mask, observed_mask)):
            raise ValueError("unobserved features cannot be locked")
        interests = _text_tuple(self.current_interests, "current_interests")
        if not set(interests).issubset(names):
            raise ValueError("current_interests must reference feature_names")
        refs = tuple(_text_tuple(item, "evidence_refs") for item in self.evidence_refs)
        developer_key = self.developer_key
        if developer_key is not None:
            developer_key = _text(developer_key, "developer_key")
        object.__setattr__(self, "profile_key", _text(self.profile_key, "profile_key"))
        object.__setattr__(self, "profile_version", _text(self.profile_version, "profile_version"))
        object.__setattr__(
            self,
            "feature_schema_version",
            _text(self.feature_schema_version, "feature_schema_version"),
        )
        object.__setattr__(self, "as_of", _utc(self.as_of, "as_of"))
        object.__setattr__(self, "feature_names", names)
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "observed_mask", observed_mask)
        object.__setattr__(self, "locked_mask", locked_mask)
        object.__setattr__(self, "current_interests", interests)
        object.__setattr__(self, "evidence_refs", refs)
        object.__setattr__(self, "developer_key", developer_key)
        object.__setattr__(self, "interpretation", _text(self.interpretation, "interpretation"))

    @classmethod
    def from_private(
        cls,
        payload: Mapping[str, Any],
        *,
        developer_key: str | None,
    ) -> EnhancedProfileFeatures:
        return cls(
            schema_version=payload.get("schema_version"),
            profile_state=payload.get("profile_state", "confirmed"),
            profile_key=payload.get("profile_key"),
            developer_key=developer_key,
            profile_version=payload.get("profile_version"),
            as_of=payload.get("as_of"),
            feature_schema_version=payload.get("feature_schema_version"),
            feature_names=tuple(payload.get("feature_names") or ()),
            values=tuple(payload.get("values") or ()),
            observed_mask=tuple(payload.get("observed_mask") or ()),
            locked_mask=tuple(payload.get("locked_mask") or ()),
            current_interests=tuple(payload.get("current_interests") or ()),
            evidence_refs=tuple(tuple(item) for item in payload.get("evidence_refs") or ()),
            interpretation=payload.get("interpretation") or (
                "confirmed_profile_features_not_verified_ability_probabilities"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True, slots=True)
class ModelMetadata:
    """Runtime model identity displayed by the platform UI."""

    variant: str
    version: str
    status: str
    dataset_version: str | None = None
    mapping_version: str | None = None
    feature_versions: dict[str, str] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "variant", _text(self.variant, "variant"))
        object.__setattr__(self, "version", _text(self.version, "version"))
        object.__setattr__(self, "status", _text(self.status, "status"))
        object.__setattr__(
            self,
            "feature_versions",
            {str(key): _text(value, "feature_version") for key, value in self.feature_versions.items()},
        )
        object.__setattr__(self, "warnings", _text_tuple(self.warnings, "warnings"))

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True, slots=True)
class PublicCandidateItem:
    """Non-personalized public candidate displayed without CODER claims."""

    task_candidate_id: int
    repository_full_name: str
    issue_number: int
    title: str
    html_url: str
    availability: str
    verified_at: str | None = None
    primary_language: str | None = None
    labels: tuple[str, ...] = ()
    task_types: tuple[str, ...] = ()
    comment_count: int = 0
    last_activity_at: str | None = None
    newcomer_label_signal: bool = False
    estimated_code_difficulty: int | None = None
    estimated_setup_difficulty: int | None = None
    score: None = None
    service_track: None = None

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True, slots=True)
class ProjectRecommendationItem:
    """Project card with explicit ranking provenance."""

    repository_full_name: str
    repository_url: str
    score: float
    ranking_source: str
    basis: tuple[str, ...]
    related_issues: tuple[dict[str, Any], ...]
    modules: tuple[str, ...] = ()
    contributing_url: str | None = None
    checked_at: str | None = None
    status: str = "available"
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "score", _unit(self.score, "score"))
        object.__setattr__(
            self,
            "repository_full_name",
            _text(self.repository_full_name, "repository_full_name"),
        )
        object.__setattr__(self, "repository_url", _text(self.repository_url, "repository_url"))
        object.__setattr__(self, "ranking_source", _text(self.ranking_source, "ranking_source"))
        object.__setattr__(self, "basis", _text_tuple(self.basis, "basis"))
        object.__setattr__(self, "modules", _text_tuple(self.modules, "modules"))
        object.__setattr__(self, "warnings", _text_tuple(self.warnings, "warnings"))

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)


__all__ = [
    "ASSESSMENT_PREVIEW_SCHEMA_VERSION",
    "AssessmentConfirmation",
    "AssessmentSubmission",
    "CONFIRMATION_DECISION_ACCEPT",
    "CONFIRMATION_DECISION_REJECT",
    "CONFIRMATION_DECISIONS",
    "CONFIRMATION_PLAN_COMPLETED",
    "CONFIRMATION_PLAN_OPEN",
    "CONFIRMATION_PLAN_STALE",
    "CONFIRMATION_RESULT_SCHEMA_VERSION",
    "CONTRACTS_MIDTERM_VERSION",
    "ConfirmationDecision",
    "ConfirmationPlan",
    "ConfirmationSuggestion",
    "ENHANCED_PROFILE_FEATURES_SCHEMA_VERSION",
    "EVIDENCE_SOURCE_ASSESSMENT_SELF_REPORT",
    "EVIDENCE_SOURCE_GITHUB_DIRECT",
    "EVIDENCE_SOURCE_GITHUB_WEAK",
    "EVIDENCE_SOURCE_USER_CONFIRMED",
    "EVIDENCE_SOURCE_USER_LOCKED",
    "EnhancedProfileFeatures",
    "FeatureProposal",
    "FeatureValueSnapshot",
    "IdempotencyConflictError",
    "MidtermContractError",
    "ModelMetadata",
    "PROFILE_FEATURE_STATE_SCHEMA_VERSION",
    "PROFILE_EVIDENCE_SOURCE_KINDS",
    "ProfileEvidence",
    "ProfileFeature",
    "ProfileFeatureState",
    "ProfileFeatureStateRow",
    "ProfilePreview",
    "ProjectRecommendationItem",
    "PublicCandidateItem",
    "QUESTIONNAIRE_SCHEMA_VERSION",
    "StalePlanError",
]
