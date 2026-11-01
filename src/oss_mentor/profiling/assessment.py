"""Deterministic questionnaire scoring and evidence fusion.

The dictionaries returned here are private B-module shapes. They deliberately
do not duplicate the shared midterm contracts that D will own. A later adapter
can map these results to ``contracts_midterm.py`` after S0 is frozen.
"""

from __future__ import annotations

import copy
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


QUESTIONNAIRE_SCHEMA_VERSION = "profile-questionnaire-schema-v0.1"
ASSESSMENT_PREVIEW_SCHEMA_VERSION = "assessment-preview-v0.1"
ENHANCED_FEATURE_EXPORT_SCHEMA_VERSION = "enhanced-profile-export-v0.1"

_SOURCE_PRIORITY = {
    "github_weak": 20,
    "assessment_self_report": 40,
    "github_direct": 60,
    "user_confirmed": 80,
    "user_locked": 100,
}


def _iso_utc(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a UTC ISO 8601 string")
    raw = value.strip()
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _unit_interval(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    normalized = float(value)
    if not 0.0 <= normalized <= 1.0:
        raise ValueError(f"{field} must be between 0 and 1")
    return normalized


def load_questionnaire(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_questionnaire(payload)
    return payload


def validate_questionnaire(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise ValueError("questionnaire must be an object")
    if payload.get("schema_version") != QUESTIONNAIRE_SCHEMA_VERSION:
        raise ValueError("unsupported questionnaire schema_version")

    for field in (
        "questionnaire_id",
        "questionnaire_version",
        "feature_schema_version",
    ):
        if not isinstance(payload.get(field), str) or not payload[field].strip():
            raise ValueError(f"{field} must be a non-empty string")

    feature_names = payload.get("feature_names")
    if (
        not isinstance(feature_names, list)
        or not feature_names
        or any(not isinstance(item, str) or not item for item in feature_names)
        or len(feature_names) != len(set(feature_names))
    ):
        raise ValueError("feature_names must contain unique non-empty strings")
    if feature_names != sorted(feature_names):
        raise ValueError("feature_names must use stable sorted order")
    feature_set = set(feature_names)

    questions = payload.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("questions must be a non-empty array")

    by_id: dict[str, dict[str, Any]] = {}
    option_ids: dict[str, set[str]] = {}
    for index, question in enumerate(questions):
        if not isinstance(question, dict):
            raise ValueError(f"questions[{index}] must be an object")
        question_id = question.get("id")
        if not isinstance(question_id, str) or not question_id:
            raise ValueError(f"questions[{index}].id must be a non-empty string")
        if question_id in by_id:
            raise ValueError(f"duplicate question id: {question_id}")
        if question.get("kind") not in {"single", "multi"}:
            raise ValueError(f"question {question_id} has unsupported kind")
        if not isinstance(question.get("revision"), int) or question["revision"] < 1:
            raise ValueError(f"question {question_id} revision must be positive")
        if not isinstance(question.get("required"), bool):
            raise ValueError(f"question {question_id} required must be boolean")

        options = question.get("options")
        if not isinstance(options, list) or not options:
            raise ValueError(f"question {question_id} options must be non-empty")
        seen_options: set[str] = set()
        for option in options:
            if not isinstance(option, dict):
                raise ValueError(f"question {question_id} option must be an object")
            option_id = option.get("id")
            if not isinstance(option_id, str) or not option_id:
                raise ValueError(f"question {question_id} option id is invalid")
            if option_id in seen_options:
                raise ValueError(f"duplicate option {option_id} in {question_id}")
            seen_options.add(option_id)
            effects = option.get("effects", [])
            if not isinstance(effects, list):
                raise ValueError(f"option {question_id}.{option_id} effects must be an array")
            for effect in effects:
                if not isinstance(effect, dict) or effect.get("feature") not in feature_set:
                    raise ValueError(f"option {question_id}.{option_id} has unknown feature")
                _unit_interval(effect.get("value"), "effect.value")
                _unit_interval(effect.get("confidence"), "effect.confidence")
        by_id[question_id] = question
        option_ids[question_id] = seen_options

    base_question_ids = payload.get("base_question_ids")
    if (
        not isinstance(base_question_ids, list)
        or len(base_question_ids) != len(set(base_question_ids))
        or any(item not in by_id for item in base_question_ids)
    ):
        raise ValueError("base_question_ids must reference unique questions")

    for question_id, question in by_id.items():
        condition = question.get("applies_when")
        if condition is None:
            continue
        if not isinstance(condition, dict):
            raise ValueError(f"question {question_id} applies_when must be an object")
        source_question = condition.get("question_id")
        expected_option = condition.get("includes")
        if source_question not in by_id or expected_option not in option_ids[source_question]:
            raise ValueError(f"question {question_id} has invalid applies_when")


def _selected_options(question: Mapping[str, Any], raw_answer: Any) -> list[str]:
    question_id = str(question["id"])
    kind = question["kind"]
    if kind == "single":
        if not isinstance(raw_answer, str) or not raw_answer:
            raise ValueError(f"answer for {question_id} must be one option id")
        selected = [raw_answer]
    else:
        if (
            not isinstance(raw_answer, list)
            or any(not isinstance(item, str) or not item for item in raw_answer)
            or len(raw_answer) != len(set(raw_answer))
        ):
            raise ValueError(f"answer for {question_id} must be unique option ids")
        selected = list(raw_answer)
        minimum = int(question.get("min_selections", 0))
        maximum = int(question.get("max_selections", len(question["options"])))
        if not minimum <= len(selected) <= maximum:
            raise ValueError(
                f"answer for {question_id} must select between {minimum} and {maximum} options"
            )

    allowed = {str(option["id"]) for option in question["options"]}
    unknown = sorted(set(selected) - allowed)
    if unknown:
        raise ValueError(f"answer for {question_id} contains unknown options: {unknown}")
    return selected


def _condition_matches(condition: Mapping[str, Any], answers: Mapping[str, Any]) -> bool:
    raw = answers.get(str(condition["question_id"]))
    expected = condition["includes"]
    if isinstance(raw, list):
        return expected in raw
    return raw == expected


def _normalize_external_evidence(
    evidence_rows: Iterable[Mapping[str, Any]],
    *,
    allowed_features: set[str],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(evidence_rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"evidence[{index}] must be an object")
        feature_name = row.get("feature_name")
        if feature_name not in allowed_features:
            raise ValueError(f"evidence[{index}] has unknown feature_name")
        source_kind = row.get("source_kind")
        if source_kind not in _SOURCE_PRIORITY or source_kind == "assessment_self_report":
            raise ValueError(f"evidence[{index}] has unsupported source_kind")
        evidence_ref = row.get("evidence_ref")
        if not isinstance(evidence_ref, str) or not evidence_ref:
            raise ValueError(f"evidence[{index}].evidence_ref must be non-empty")
        normalized.append(
            {
                "feature_name": feature_name,
                "value": _unit_interval(row.get("value"), f"evidence[{index}].value"),
                "confidence": _unit_interval(
                    row.get("confidence"), f"evidence[{index}].confidence"
                ),
                "source_kind": source_kind,
                "evidence_ref": evidence_ref,
                "observed_at": _iso_utc(row.get("observed_at"), f"evidence[{index}].observed_at"),
                "locked": source_kind == "user_locked",
            }
        )
    return normalized


def _fuse_feature(feature_name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(
        rows,
        key=lambda row: (
            -_SOURCE_PRIORITY[row["source_kind"]],
            -row["confidence"],
            row["evidence_ref"],
        ),
    )
    if not ordered:
        return {
            "feature_name": feature_name,
            "observed": False,
            "proposed_value": 0.0,
            "confidence": 0.0,
            "source_kind": None,
            "evidence_refs": [],
            "conflict": False,
            "blocked_reason": None,
        }

    highest_priority = _SOURCE_PRIORITY[ordered[0]["source_kind"]]
    leading = [
        row
        for row in ordered
        if _SOURCE_PRIORITY[row["source_kind"]] == highest_priority
    ]
    weight_sum = sum(max(row["confidence"], 0.01) for row in leading)
    proposed_value = sum(
        row["value"] * max(row["confidence"], 0.01) for row in leading
    ) / weight_sum
    conflict = any(
        abs(first["value"] - second["value"]) >= 0.25
        for index, first in enumerate(ordered)
        for second in ordered[index + 1 :]
    )
    locked = any(row["locked"] for row in leading)
    return {
        "feature_name": feature_name,
        "observed": True,
        "proposed_value": round(proposed_value, 4),
        "confidence": round(max(row["confidence"] for row in leading), 4),
        "source_kind": leading[0]["source_kind"],
        "evidence_refs": [row["evidence_ref"] for row in ordered],
        "conflict": conflict,
        "blocked_reason": "field_locked" if locked else None,
    }


def build_assessment_preview(
    questionnaire: Mapping[str, Any],
    submission: Mapping[str, Any],
    *,
    external_evidence: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build a deterministic preview without mutating any input."""
    validate_questionnaire(questionnaire)
    questionnaire_copy = copy.deepcopy(dict(questionnaire))
    submission_copy = copy.deepcopy(dict(submission))

    if submission_copy.get("questionnaire_version") != questionnaire_copy["questionnaire_version"]:
        raise ValueError("submission questionnaire_version does not match")
    submitted_at = _iso_utc(submission_copy.get("submitted_at"), "submitted_at")
    answers = submission_copy.get("answers")
    if not isinstance(answers, dict):
        raise ValueError("answers must be an object")

    questions = {question["id"]: question for question in questionnaire_copy["questions"]}
    unknown_questions = sorted(set(answers) - set(questions))
    if unknown_questions:
        raise ValueError(f"answers contain unknown question ids: {unknown_questions}")

    applicable: list[dict[str, Any]] = []
    for question in questionnaire_copy["questions"]:
        condition = question.get("applies_when")
        if condition is None or _condition_matches(condition, answers):
            applicable.append(question)
        elif question["id"] in answers:
            raise ValueError(f"answer supplied for inactive question {question['id']}")

    evidence_by_feature: dict[str, list[dict[str, Any]]] = defaultdict(list)
    normalized_answers: dict[str, Any] = {}
    for question in applicable:
        question_id = question["id"]
        if question_id not in answers:
            if question["required"]:
                raise ValueError(f"missing required answer: {question_id}")
            continue
        selected = _selected_options(question, answers[question_id])
        normalized_answers[question_id] = selected[0] if question["kind"] == "single" else selected
        options = {option["id"]: option for option in question["options"]}
        selected_features: set[str] = set()
        for option_id in selected:
            for effect in options[option_id].get("effects", []):
                selected_features.add(effect["feature"])
                evidence_by_feature[effect["feature"]].append(
                    {
                        "feature_name": effect["feature"],
                        "value": float(effect["value"]),
                        "confidence": float(effect["confidence"]),
                        "source_kind": "assessment_self_report",
                        "evidence_ref": f"assessment:{question_id}:{option_id}",
                        "observed_at": submitted_at,
                        "locked": False,
                    }
                )

        # A completed closed-choice question makes its unselected categories
        # known false. This is different from an unanswered or inactive
        # question, whose features must retain observed_mask=false.
        categorical_features: dict[str, float] = {}
        for option in question["options"]:
            for effect in option.get("effects", []):
                categorical_features[effect["feature"]] = max(
                    categorical_features.get(effect["feature"], 0.0),
                    float(effect["confidence"]),
                )
        if len(categorical_features) > 1:
            for feature_name, confidence in categorical_features.items():
                if feature_name in selected_features:
                    continue
                evidence_by_feature[feature_name].append(
                    {
                        "feature_name": feature_name,
                        "value": 0.0,
                        "confidence": confidence,
                        "source_kind": "assessment_self_report",
                        "evidence_ref": f"assessment:{question_id}:not_selected",
                        "observed_at": submitted_at,
                        "locked": False,
                    }
                )

    feature_names = list(questionnaire_copy["feature_names"])
    for row in _normalize_external_evidence(
        external_evidence, allowed_features=set(feature_names)
    ):
        evidence_by_feature[row["feature_name"]].append(row)

    features = [
        _fuse_feature(feature_name, evidence_by_feature.get(feature_name, []))
        for feature_name in feature_names
    ]
    current_interests = [
        feature["feature_name"]
        for feature in features
        if feature["feature_name"].startswith("current_interest.")
        and feature["observed"]
        and feature["proposed_value"] > 0.0
    ]
    return {
        "schema_version": ASSESSMENT_PREVIEW_SCHEMA_VERSION,
        "questionnaire_id": questionnaire_copy["questionnaire_id"],
        "questionnaire_version": questionnaire_copy["questionnaire_version"],
        "feature_schema_version": questionnaire_copy["feature_schema_version"],
        "submitted_at": submitted_at,
        "answers": normalized_answers,
        "features": features,
        "current_interests": current_interests,
        "interpretation": "self_report_and_evidence_estimate_not_verified_ability",
        "saved": False,
    }


def export_enhanced_features(
    preview: Mapping[str, Any],
    *,
    profile_key: str,
    profile_version: str,
    as_of: str,
    developer_key: str | None = None,
) -> dict[str, Any]:
    """Export ordered values and an explicit missing-value mask for C."""
    if preview.get("schema_version") != ASSESSMENT_PREVIEW_SCHEMA_VERSION:
        raise ValueError("unsupported assessment preview schema_version")
    if not isinstance(profile_key, str) or not profile_key:
        raise ValueError("profile_key must be non-empty")
    if not isinstance(profile_version, str) or not profile_version:
        raise ValueError("profile_version must be non-empty")
    if developer_key is not None and (not isinstance(developer_key, str) or not developer_key):
        raise ValueError("developer_key must be null or non-empty")

    features = preview.get("features")
    if not isinstance(features, list):
        raise ValueError("preview features must be an array")
    feature_names = [row["feature_name"] for row in features]
    if feature_names != sorted(feature_names) or len(feature_names) != len(set(feature_names)):
        raise ValueError("preview features must be unique and sorted")

    return {
        "schema_version": ENHANCED_FEATURE_EXPORT_SCHEMA_VERSION,
        "profile_key": profile_key,
        "developer_key": developer_key,
        "profile_version": profile_version,
        "as_of": _iso_utc(as_of, "as_of"),
        "feature_schema_version": preview.get("feature_schema_version"),
        "feature_names": feature_names,
        "values": [float(row["proposed_value"]) for row in features],
        "observed_mask": [bool(row["observed"]) for row in features],
        "current_interests": list(preview.get("current_interests", [])),
        "evidence_refs": [list(row.get("evidence_refs", [])) for row in features],
        "interpretation": preview.get("interpretation"),
    }
