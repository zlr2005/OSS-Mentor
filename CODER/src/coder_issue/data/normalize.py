"""Deterministic identifiers and conservative value normalization."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from typing import Any


PROJECT_FEATURE_FIELDS = (
    "n_stars",
    "n_contributors",
    "n_commits",
    "n_pulls",
    "n_open_issues",
    "n_closed_issues",
    "r_open_issues",
)

TEXT_FEATURE_FIELDS = (
    "len_title",
    "len_body",
    "n_code_snips",
    "n_urls",
    "n_imgs",
    "coleman_liau_index",
    "flesch_reading_ease",
    "flesch_kincaid_grade",
    "automated_readability_index",
)


def normalize_component(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    normalized = value.strip().casefold()
    if not normalized:
        raise ValueError(f"{field} must not be blank")
    return normalized


def repository_key(owner: Any, name: Any) -> str:
    owner_key = normalize_component(owner, field="owner")
    name_key = normalize_component(name, field="name")
    return f"{owner_key}/{name_key}"


def issue_key(owner: Any, name: Any, number: Any) -> str:
    repo = repository_key(owner, name)
    if isinstance(number, bool):
        raise ValueError("number must be an integer")
    try:
        issue_number = int(number)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("number must be an integer") from exc
    if issue_number <= 0:
        raise ValueError("number must be positive")
    return f"{repo}#{issue_number}"


def developer_key(value: Any) -> str:
    return normalize_component(value, field="developer")


def label_key(value: Any) -> str:
    return normalize_component(value, field="label")


def utc_datetime(value: Any) -> datetime | None:
    """Return an aware UTC datetime, preserving missing values as ``None``."""

    if value is None:
        return None
    parsed: datetime
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError(f"unsupported timestamp type: {type(value).__name__}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def timestamp_text(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def object_id_text(value: Any) -> str:
    return "" if value is None else str(value)


def normalize_labels(value: Any) -> tuple[list[str], int]:
    """Return deterministic unique labels and the count of invalid elements."""

    if value is None:
        return [], 0
    if not isinstance(value, list):
        return [], 1
    labels: dict[str, str] = {}
    invalid = 0
    for raw in value:
        try:
            key = label_key(raw)
        except ValueError:
            invalid += 1
            continue
        display = str(raw).strip()
        current = labels.get(key)
        if current is None or display < current:
            labels[key] = display
    return [labels[key] for key in sorted(labels)], invalid


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        normalized = utc_datetime(value)
        assert normalized is not None
        return normalized.isoformat()
    return str(value)


def stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def safe_numeric_features(document: dict[str, Any]) -> dict[str, int | float | None]:
    """Keep snapshot-local numeric fields without treating them as static features."""

    result: dict[str, int | float | None] = {}
    for field in PROJECT_FEATURE_FIELDS + TEXT_FEATURE_FIELDS:
        value = document.get(field)
        if value is None:
            result[field] = None
        elif isinstance(value, bool) or not isinstance(value, (int, float)):
            result[field] = None
        elif isinstance(value, float) and not math.isfinite(value):
            result[field] = None
        else:
            result[field] = value
    return result


def bson_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, datetime):
        return "datetime"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return type(value).__name__
