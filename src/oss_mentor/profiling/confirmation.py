"""Deterministic confirmation rules for assessment feature previews.

These mappings are private B-module shapes. Persistence, session identity and
HTTP error conversion remain integration responsibilities after S0 is frozen.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Iterable, Mapping

from .assessment import ASSESSMENT_PREVIEW_SCHEMA_VERSION, _iso_utc, _unit_interval


PROFILE_FEATURE_STATE_SCHEMA_VERSION = "profile-feature-state-v0.1"
CONFIRMATION_PLAN_SCHEMA_VERSION = "assessment-confirmation-plan-v0.1"
CONFIRMATION_RESULT_SCHEMA_VERSION = "assessment-confirmation-result-v0.1"


def _non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _digest(payload: Any, prefix: str) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}{hashlib.sha256(canonical).hexdigest()[:20]}"


def _validate_evidence_refs(value: Any, field: str) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise ValueError(f"{field} must contain unique non-empty strings")
    return list(value)


def _validate_preview(preview: Mapping[str, Any]) -> list[dict[str, Any]]:
    if preview.get("schema_version") != ASSESSMENT_PREVIEW_SCHEMA_VERSION:
        raise ValueError("unsupported assessment preview schema_version")
    if preview.get("saved") is not False:
        raise ValueError("confirmation requires an unsaved assessment preview")
    _non_empty_string(preview.get("questionnaire_version"), "questionnaire_version")
    _non_empty_string(preview.get("feature_schema_version"), "feature_schema_version")
    _iso_utc(preview.get("submitted_at"), "submitted_at")
    rows = preview.get("features")
    if not isinstance(rows, list):
        raise ValueError("preview features must be an array")
    names = [row.get("feature_name") for row in rows if isinstance(row, Mapping)]
    if len(names) != len(rows) or names != sorted(names) or len(names) != len(set(names)):
        raise ValueError("preview features must be unique objects in sorted order")
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        assert isinstance(row, Mapping)
        feature_name = _non_empty_string(row.get("feature_name"), f"features[{index}].feature_name")
        if not isinstance(row.get("observed"), bool):
            raise ValueError(f"features[{index}].observed must be boolean")
        if not isinstance(row.get("conflict"), bool):
            raise ValueError(f"features[{index}].conflict must be boolean")
        blocked_reason = row.get("blocked_reason")
        if blocked_reason not in {None, "field_locked"}:
            raise ValueError(f"features[{index}].blocked_reason is unsupported")
        normalized.append(
            {
                "feature_name": feature_name,
                "observed": row["observed"],
                "proposed_value": _unit_interval(
                    row.get("proposed_value"), f"features[{index}].proposed_value"
                ),
                "confidence": _unit_interval(
                    row.get("confidence"), f"features[{index}].confidence"
                ),
                "source_kind": row.get("source_kind"),
                "evidence_refs": _validate_evidence_refs(
                    row.get("evidence_refs"), f"features[{index}].evidence_refs"
                ),
                "conflict": row["conflict"],
                "blocked_reason": blocked_reason,
            }
        )
    return normalized


def validate_feature_state(state: Mapping[str, Any]) -> None:
    if state.get("schema_version") != PROFILE_FEATURE_STATE_SCHEMA_VERSION:
        raise ValueError("unsupported profile feature state schema_version")
    for field in ("profile_key", "profile_version", "feature_schema_version"):
        _non_empty_string(state.get(field), field)
    _iso_utc(state.get("updated_at"), "updated_at")
    rows = state.get("features")
    if not isinstance(rows, list):
        raise ValueError("profile features must be an array")
    names = [row.get("feature_name") for row in rows if isinstance(row, Mapping)]
    if len(names) != len(rows) or names != sorted(names) or len(names) != len(set(names)):
        raise ValueError("profile features must be unique objects in sorted order")
    for index, row in enumerate(rows):
        assert isinstance(row, Mapping)
        _non_empty_string(row.get("feature_name"), f"features[{index}].feature_name")
        observed = row.get("observed")
        locked = row.get("locked")
        if not isinstance(observed, bool):
            raise ValueError(f"features[{index}].observed must be boolean")
        if not isinstance(locked, bool):
            raise ValueError(f"features[{index}].locked must be boolean")
        value = _unit_interval(row.get("value"), f"features[{index}].value")
        if not observed and value != 0.0:
            raise ValueError("an unknown profile feature must use value 0 as a placeholder")
        if locked and not observed:
            raise ValueError("an unknown profile feature cannot be locked")
        _validate_evidence_refs(row.get("evidence_refs"), f"features[{index}].evidence_refs")


def build_feature_state(
    preview: Mapping[str, Any],
    *,
    profile_key: str,
    profile_version: str,
    updated_at: str,
    feature_overrides: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Create a complete private feature state, defaulting every field to unknown."""
    preview_rows = _validate_preview(preview)
    rows = {
        row["feature_name"]: {
            "feature_name": row["feature_name"],
            "observed": False,
            "value": 0.0,
            "locked": False,
            "evidence_refs": [],
        }
        for row in preview_rows
    }
    seen: set[str] = set()
    for index, override in enumerate(feature_overrides):
        if not isinstance(override, Mapping):
            raise ValueError(f"feature_overrides[{index}] must be an object")
        feature_name = override.get("feature_name")
        if feature_name not in rows:
            raise ValueError(f"feature_overrides[{index}] has unknown feature_name")
        if feature_name in seen:
            raise ValueError(f"duplicate feature override: {feature_name}")
        seen.add(str(feature_name))
        rows[str(feature_name)] = {
            "feature_name": feature_name,
            "observed": override.get("observed"),
            "value": override.get("value"),
            "locked": override.get("locked", False),
            "evidence_refs": list(override.get("evidence_refs", [])),
        }
    state = {
        "schema_version": PROFILE_FEATURE_STATE_SCHEMA_VERSION,
        "profile_key": _non_empty_string(profile_key, "profile_key"),
        "profile_version": _non_empty_string(profile_version, "profile_version"),
        "feature_schema_version": preview["feature_schema_version"],
        "updated_at": _iso_utc(updated_at, "updated_at"),
        "features": [rows[name] for name in sorted(rows)],
    }
    validate_feature_state(state)
    return state


def build_confirmation_plan(
    preview: Mapping[str, Any],
    current_state: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare an unsaved preview with the current state and produce stable suggestions."""
    preview_rows = _validate_preview(preview)
    validate_feature_state(current_state)
    if preview["feature_schema_version"] != current_state["feature_schema_version"]:
        raise ValueError("feature schema version does not match current profile")
    current_rows = {row["feature_name"]: row for row in current_state["features"]}
    preview_names = [row["feature_name"] for row in preview_rows]
    if preview_names != list(current_rows):
        raise ValueError("preview and current profile feature names do not match")

    suggestions: list[dict[str, Any]] = []
    for proposed in preview_rows:
        if not proposed["observed"]:
            continue
        current = current_rows[proposed["feature_name"]]
        new_refs = [
            ref for ref in proposed["evidence_refs"] if ref not in current["evidence_refs"]
        ]
        changed = (
            not current["observed"]
            or float(current["value"]) != proposed["proposed_value"]
            or bool(new_refs)
        )
        if not changed:
            continue
        blocked_reason = (
            "field_locked"
            if current["locked"] or proposed["blocked_reason"] == "field_locked"
            else None
        )
        identity = {
            "profile_key": current_state["profile_key"],
            "base_profile_version": current_state["profile_version"],
            "feature_name": proposed["feature_name"],
            "proposed_value": proposed["proposed_value"],
            "observed": proposed["observed"],
            "evidence_refs": proposed["evidence_refs"],
        }
        suggestions.append(
            {
                "suggestion_id": _digest(identity, "suggestion:"),
                "feature_name": proposed["feature_name"],
                "current": {
                    "observed": current["observed"],
                    "value": float(current["value"]),
                    "locked": current["locked"],
                    "evidence_refs": list(current["evidence_refs"]),
                },
                "proposed": {
                    "observed": True,
                    "value": proposed["proposed_value"],
                    "confidence": proposed["confidence"],
                    "source_kind": proposed["source_kind"],
                    "evidence_refs": list(proposed["evidence_refs"]),
                },
                "conflict": proposed["conflict"],
                "status": "blocked" if blocked_reason else "pending",
                "blocked_reason": blocked_reason,
            }
        )

    core = {
        "profile_key": current_state["profile_key"],
        "base_profile_version": current_state["profile_version"],
        "questionnaire_version": preview["questionnaire_version"],
        "feature_schema_version": preview["feature_schema_version"],
        "preview_submitted_at": preview["submitted_at"],
        "suggestions": suggestions,
    }
    return {
        "schema_version": CONFIRMATION_PLAN_SCHEMA_VERSION,
        "plan_id": _digest(core, "plan:"),
        **core,
        "persisted": False,
    }


def confirm_assessment_plan(
    plan: Mapping[str, Any],
    current_state: Mapping[str, Any],
    decisions: Mapping[str, str],
    *,
    confirmed_at: str,
) -> dict[str, Any]:
    """Apply explicit decisions without persisting or mutating either input."""
    if plan.get("schema_version") != CONFIRMATION_PLAN_SCHEMA_VERSION:
        raise ValueError("unsupported confirmation plan schema_version")
    validate_feature_state(current_state)
    if plan.get("profile_key") != current_state["profile_key"]:
        raise ValueError("confirmation plan belongs to a different profile")
    if plan.get("base_profile_version") != current_state["profile_version"]:
        raise ValueError("profile version conflict: confirmation plan is stale")
    if not isinstance(decisions, Mapping) or not decisions:
        raise ValueError("at least one explicit confirmation decision is required")
    suggestions = plan.get("suggestions")
    if not isinstance(suggestions, list):
        raise ValueError("confirmation plan suggestions must be an array")
    by_id = {row["suggestion_id"]: row for row in suggestions}
    unknown_ids = sorted(set(decisions) - set(by_id))
    if unknown_ids:
        raise ValueError(f"unknown suggestion ids: {unknown_ids}")
    invalid = sorted(
        suggestion_id
        for suggestion_id, decision in decisions.items()
        if decision not in {"accept", "reject"}
    )
    if invalid:
        raise ValueError(f"unsupported decisions for suggestion ids: {invalid}")
    blocked = sorted(
        suggestion_id
        for suggestion_id in decisions
        if by_id[suggestion_id].get("status") == "blocked"
    )
    if blocked:
        raise ValueError(f"blocked suggestions cannot be decided: {blocked}")

    next_state = copy.deepcopy(dict(current_state))
    next_rows = {row["feature_name"]: row for row in next_state["features"]}
    decision_records: list[dict[str, Any]] = []
    for suggestion_id in sorted(decisions):
        decision = decisions[suggestion_id]
        suggestion = by_id[suggestion_id]
        decision_records.append(
            {
                "suggestion_id": suggestion_id,
                "feature_name": suggestion["feature_name"],
                "decision": decision,
                "conflict_was_visible": bool(suggestion["conflict"]),
            }
        )
        if decision == "accept":
            target = next_rows[suggestion["feature_name"]]
            proposed = suggestion["proposed"]
            target["observed"] = True
            target["value"] = float(proposed["value"])
            target["evidence_refs"] = list(
                dict.fromkeys(target["evidence_refs"] + proposed["evidence_refs"])
            )

    normalized_time = _iso_utc(confirmed_at, "confirmed_at")
    version_material = {
        "profile_key": next_state["profile_key"],
        "base_profile_version": current_state["profile_version"],
        "confirmed_at": normalized_time,
        "decisions": decision_records,
        "features": next_state["features"],
    }
    next_state["profile_version"] = _digest(version_material, "profile:")
    next_state["updated_at"] = normalized_time
    validate_feature_state(next_state)
    decided_ids = set(decisions)
    result_core = {
        "plan_id": plan.get("plan_id"),
        "base_profile_version": current_state["profile_version"],
        "new_profile_version": next_state["profile_version"],
        "confirmed_at": normalized_time,
        "decisions": decision_records,
    }
    return {
        "schema_version": CONFIRMATION_RESULT_SCHEMA_VERSION,
        "confirmation_id": _digest(result_core, "confirmation:"),
        **result_core,
        "applied_suggestion_ids": [
            row["suggestion_id"] for row in decision_records if row["decision"] == "accept"
        ],
        "rejected_suggestion_ids": [
            row["suggestion_id"] for row in decision_records if row["decision"] == "reject"
        ],
        "remaining_suggestion_ids": [
            row["suggestion_id"]
            for row in suggestions
            if row["status"] == "pending" and row["suggestion_id"] not in decided_ids
        ],
        "blocked_suggestion_ids": [
            row["suggestion_id"] for row in suggestions if row["status"] == "blocked"
        ],
        "profile_state": next_state,
        "persisted": False,
    }


def set_feature_lock(
    current_state: Mapping[str, Any],
    *,
    feature_name: str,
    locked: bool,
    expected_profile_version: str,
    updated_at: str,
) -> dict[str, Any]:
    """Return a new version after an explicit user lock or unlock action."""
    validate_feature_state(current_state)
    if current_state["profile_version"] != expected_profile_version:
        raise ValueError("profile version conflict while changing feature lock")
    if not isinstance(locked, bool):
        raise ValueError("locked must be boolean")
    next_state = copy.deepcopy(dict(current_state))
    rows = {row["feature_name"]: row for row in next_state["features"]}
    if feature_name not in rows:
        raise ValueError("unknown feature_name")
    row = rows[feature_name]
    if locked and not row["observed"]:
        raise ValueError("an unknown profile feature cannot be locked")
    if row["locked"] == locked:
        raise ValueError("feature already has the requested lock state")
    row["locked"] = locked
    normalized_time = _iso_utc(updated_at, "updated_at")
    version_material = {
        "profile_key": next_state["profile_key"],
        "base_profile_version": current_state["profile_version"],
        "updated_at": normalized_time,
        "feature_name": feature_name,
        "locked": locked,
        "features": next_state["features"],
    }
    next_state["profile_version"] = _digest(version_material, "profile:")
    next_state["updated_at"] = normalized_time
    validate_feature_state(next_state)
    return next_state
