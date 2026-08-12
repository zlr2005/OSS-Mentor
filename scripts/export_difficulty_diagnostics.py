"""Export read-only difficulty diagnostics for OSS-Mentor SQLite data.

The script compares the original candidate database with an extracted feature
snapshot. Both databases are opened through SQLite URI ``mode=ro`` and
``PRAGMA query_only = ON``. It never initializes stores, runs migrations, or
writes to either database. Only the JSON report path is written.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from oss_mentor.developer_profiles import ALLOWED_TASK_TYPES


DIAGNOSTIC_SCHEMA_VERSION = "difficulty_diagnostics_v0.2"
DEFAULT_BASELINE_DATABASE = Path("data/oss_mentor.sqlite3")
DEFAULT_AFTER_DATABASE = Path(
    "data/oss_mentor_task_features_v0.2_round3.sqlite3"
)
DEFAULT_OUTPUT_PATH = Path("data/reports/difficulty_diagnostics_v0.2.json")

PUBLIC_TASK_TYPES = frozenset(
    str(task_type).strip().casefold()
    for task_type in ALLOWED_TASK_TYPES
    if str(task_type).strip()
)
DIFFICULTY_FIELDS: tuple[tuple[str, str], ...] = (
    ("code", "estimated_code_difficulty"),
    ("setup", "estimated_setup_difficulty"),
    ("project_context", "estimated_project_context_difficulty"),
    ("collaboration", "estimated_collaboration_difficulty"),
)
SCORE_FIELDS: tuple[tuple[str, str, tuple[float, ...]], ...] = (
    ("text_clarity", "text_clarity_score", (0, 20, 40, 60, 80, 100)),
    (
        "novice_fit_probability",
        "novice_fit_probability",
        (0, 0.2, 0.4, 0.6, 0.8, 1.0),
    ),
    ("newcomer_score", "newcomer_score", (0, 20, 40, 60, 80, 100)),
    ("growth_value", "growth_value_score", (0, 20, 40, 60, 80, 100)),
)
EFFORT_ORDER = {
    "under_2h": 0,
    "half_day": 1,
    "one_day": 2,
    "multi_day": 3,
}
VALID_EFFORT = tuple(EFFORT_ORDER)

VALID_CONFIDENCE = ("low", "medium", "high")
VALID_ACTIONABILITY = (
    "actionable",
    "design_pending",
    "unclear",
    "non_actionable",
)
VALID_EFFORT_SCOPE = (
    "micro",
    "local",
    "module",
    "cross_module",
    "system",
    "unclear",
    "non_actionable",
)
VALID_DIFFICULTY_STATUS = ("ok", "missing", "invalid")
VALID_EVIDENCE_STRENGTH = ("weak", "medium", "strong")
DIFFICULTY_DIMENSION_NAMES = tuple(name for name, _ in DIFFICULTY_FIELDS)

_SETUP_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("compile", re.compile(r"\bcompil(?:e|er|ation|ing)?\b", re.IGNORECASE)),
    ("native", re.compile(r"\bnative\b", re.IGNORECASE)),
    ("toolchain", re.compile(r"\btoolchain\b", re.IGNORECASE)),
    ("backend", re.compile(r"\bbackend\b", re.IGNORECASE)),
    ("macos", re.compile(r"\bmac\s?os\b|\bmacos\b", re.IGNORECASE)),
    ("windows", re.compile(r"\bwindows\b", re.IGNORECASE)),
    ("linux", re.compile(r"\blinux\b", re.IGNORECASE)),
    ("docker", re.compile(r"\bdocker\b", re.IGNORECASE)),
    ("kubernetes", re.compile(r"\bkubernetes\b|\bk8s\b", re.IGNORECASE)),
)
_CONTEXT_LABEL_PATTERN = re.compile(r"core|architecture|api", re.IGNORECASE)
_DISCUSSION_LABEL_PATTERN = re.compile(r"discussion|design", re.IGNORECASE)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def readonly_uri(database_path: str | Path) -> str:
    """Return a SQLite read-only URI for an existing database file."""

    resolved = Path(database_path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {resolved}")
    return f"{resolved.as_uri()}?mode=ro"


def connect_readonly(database_path: str | Path) -> sqlite3.Connection:
    """Open SQLite with URI mode=ro and query_only enabled."""

    connection = sqlite3.connect(readonly_uri(database_path), uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _safe_json(value: Any) -> tuple[Any, str]:
    if value is None:
        return None, "missing"
    if isinstance(value, str):
        if not value.strip():
            return None, "missing"
        try:
            return json.loads(value), "ok"
        except json.JSONDecodeError:
            return None, "invalid"
    return value, "ok"


def _parse_string_list(value: Any) -> tuple[list[str], str]:
    parsed, status = _safe_json(value)
    if status != "ok":
        return [], status
    if not isinstance(parsed, (list, tuple)):
        return [], "invalid"
    if any(not isinstance(item, str) for item in parsed):
        return [], "invalid"
    cleaned = sorted(
        {item.strip() for item in parsed if item.strip()},
        key=str.casefold,
    )
    return (cleaned, "ok") if cleaned else ([], "missing")


def _parse_object(value: Any) -> tuple[dict[str, Any], str]:
    parsed, status = _safe_json(value)
    if status != "ok":
        return {}, status
    if not isinstance(parsed, dict):
        return {}, "invalid"
    return parsed, "ok"



def _is_plain_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _normalize_string_list(value: Any) -> tuple[list[str], bool]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return [], False
    return sorted({item.strip() for item in value if item.strip()}, key=str.casefold), True


def _mapping_list_sort_key(item: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(item.get(key) or "")
        for key in (
            "dimension",
            "source",
            "rule_id",
            "matched_value",
            "strength",
            "suggested_level",
            "reason",
            "lower_rule_id",
            "higher_rule_id",
        )
    )


def _normalize_mapping_list(value: Any) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        return [], False
    normalized = [dict(item) for item in value]
    normalized.sort(key=_mapping_list_sort_key)
    return normalized, True


def _parse_difficulty_assessment(
    feature_evidence: Mapping[str, Any],
) -> tuple[dict[str, Any], str, list[str]]:
    """Validate and normalize difficulty-rules-v0.2 evidence.

    Old task-features-v0.2 records legitimately lack this object and are
    classified as ``missing``. Malformed v0.3 evidence is classified as
    ``invalid`` without preventing the rest of the report from being built.
    """

    raw = feature_evidence.get("difficulty_assessment")
    if raw is None:
        return {}, "missing", []
    if not isinstance(raw, Mapping):
        return {}, "invalid", ["difficulty_assessment_not_object"]

    errors: list[str] = []
    formula_version = raw.get("formula_version")
    if not isinstance(formula_version, str) or not formula_version.strip():
        errors.append("formula_version_invalid")

    information = raw.get("information_quality")
    if not isinstance(information, Mapping):
        errors.append("information_quality_invalid")
        information = {}
    body_missing = information.get("body_missing")
    actionability = information.get("actionability")
    information_confidence = information.get("confidence")
    reasons, reasons_ok = _normalize_string_list(information.get("reasons"))
    if not _is_plain_bool(body_missing):
        errors.append("information_body_missing_invalid")
    if actionability not in VALID_ACTIONABILITY:
        errors.append("information_actionability_invalid")
    if information_confidence not in VALID_CONFIDENCE:
        errors.append("information_confidence_invalid")
    if not reasons_ok:
        errors.append("information_reasons_invalid")

    raw_dimensions = raw.get("dimensions")
    if not isinstance(raw_dimensions, Mapping):
        errors.append("dimensions_invalid")
        raw_dimensions = {}
    dimensions: dict[str, dict[str, Any]] = {}
    for dimension in DIFFICULTY_DIMENSION_NAMES:
        item = raw_dimensions.get(dimension)
        if not isinstance(item, Mapping):
            errors.append(f"dimension_{dimension}_invalid")
            continue
        prior = _safe_int(item.get("prior"))
        level = _safe_int(item.get("level"))
        confidence = item.get("confidence")
        evidence, evidence_ok = _normalize_mapping_list(item.get("evidence"))
        conflicts, conflicts_ok = _normalize_mapping_list(item.get("conflicts"))
        if prior is None or not 0 <= prior <= 3:
            errors.append(f"dimension_{dimension}_prior_invalid")
        if level is None or not 0 <= level <= 3:
            errors.append(f"dimension_{dimension}_level_invalid")
        if confidence not in VALID_CONFIDENCE:
            errors.append(f"dimension_{dimension}_confidence_invalid")
        if not evidence_ok:
            errors.append(f"dimension_{dimension}_evidence_invalid")
        if not conflicts_ok:
            errors.append(f"dimension_{dimension}_conflicts_invalid")
        for evidence_item in evidence:
            strength = evidence_item.get("strength")
            suggested = _safe_int(evidence_item.get("suggested_level"))
            if strength not in VALID_EVIDENCE_STRENGTH:
                errors.append(f"dimension_{dimension}_evidence_strength_invalid")
            if suggested is None or not 0 <= suggested <= 3:
                errors.append(f"dimension_{dimension}_evidence_level_invalid")
            for key in ("source", "rule_id", "reason"):
                if not isinstance(evidence_item.get(key), str) or not str(
                    evidence_item.get(key)
                ).strip():
                    errors.append(
                        f"dimension_{dimension}_evidence_{key}_invalid"
                    )
        dimensions[dimension] = {
            "prior": prior,
            "level": level,
            "confidence": confidence,
            "evidence": evidence,
            "conflicts": conflicts,
        }

    raw_effort = raw.get("effort")
    if not isinstance(raw_effort, Mapping):
        errors.append("effort_invalid")
        raw_effort = {}
    effort_bucket = raw_effort.get("bucket")
    effort_scope = raw_effort.get("scope")
    effort_applicable = raw_effort.get("applicable")
    effort_provisional = raw_effort.get("provisional")
    effort_confidence = raw_effort.get("confidence")
    effort_evidence, effort_evidence_ok = _normalize_mapping_list(
        raw_effort.get("evidence")
    )
    if effort_bucket not in VALID_EFFORT:
        errors.append("effort_bucket_invalid")
    if effort_scope not in VALID_EFFORT_SCOPE:
        errors.append("effort_scope_invalid")
    if not _is_plain_bool(effort_applicable):
        errors.append("effort_applicable_invalid")
    if not _is_plain_bool(effort_provisional):
        errors.append("effort_provisional_invalid")
    if effort_confidence not in VALID_CONFIDENCE:
        errors.append("effort_confidence_invalid")
    if not effort_evidence_ok:
        errors.append("effort_evidence_invalid")
    for evidence_item in effort_evidence:
        for key in ("source", "rule_id", "reason"):
            if not isinstance(evidence_item.get(key), str) or not str(
                evidence_item.get(key)
            ).strip():
                errors.append(f"effort_evidence_{key}_invalid")

    if errors:
        return {}, "invalid", sorted(set(errors))

    normalized = {
        "formula_version": str(formula_version).strip(),
        "information_quality": {
            "body_missing": bool(body_missing),
            "actionability": str(actionability),
            "confidence": str(information_confidence),
            "reasons": reasons,
        },
        "dimensions": dimensions,
        "effort": {
            "bucket": str(effort_bucket),
            "scope": str(effort_scope),
            "applicable": bool(effort_applicable),
            "provisional": bool(effort_provisional),
            "confidence": str(effort_confidence),
            "evidence": effort_evidence,
        },
    }
    return normalized, "ok", []


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, float):
        return value != 0.0
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return bool(value)


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            return None
        if parsed.is_integer():
            return int(parsed)
    return None


def _safe_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        result = float(value)
        return result if math.isfinite(result) else None
    if isinstance(value, str):
        try:
            result = float(value.strip())
        except ValueError:
            return None
        return result if math.isfinite(result) else None
    return None


def _record_sort_key(record: Mapping[str, Any]) -> tuple[str, int, int]:
    repository = str(record.get("repository") or "").casefold()
    issue_number = _safe_int(record.get("issue_number")) or 0
    task_candidate_id = _safe_int(record.get("task_candidate_id")) or 0
    return repository, issue_number, task_candidate_id


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def load_database_snapshot(database_path: str | Path) -> dict[str, Any]:
    """Load active repository candidates and skills without mutating SQLite."""

    resolved = Path(database_path).expanduser().resolve()
    connection = connect_readonly(resolved)
    try:
        rows = connection.execute(
            """
            SELECT
                tc.task_candidate_id,
                r.full_name AS repository,
                COALESCE(r.primary_language, '') AS primary_language,
                tc.issue_number,
                COALESCE(tc.html_url, '') AS html_url,
                COALESCE(tc.title, '') AS title,
                COALESCE(tc.body_text, '') AS body_text,
                tc.labels_json,
                tc.task_types_json,
                tc.newcomer_label_signal,
                tc.feature_evidence_json,
                COALESCE(tc.candidate_eligibility, '') AS candidate_eligibility,
                COALESCE(tc.comment_count, 0) AS comment_count,
                tc.text_clarity_score,
                tc.estimated_code_difficulty,
                tc.estimated_setup_difficulty,
                tc.estimated_project_context_difficulty,
                tc.estimated_collaboration_difficulty,
                tc.estimated_effort_bucket,
                tc.novice_fit_probability,
                tc.newcomer_score,
                tc.growth_value_score,
                COALESCE(tc.task_feature_version, '') AS task_feature_version
            FROM task_candidate AS tc
            JOIN repository AS r
              ON r.repository_id = tc.repository_id
            WHERE COALESCE(r.is_archived, 0) = 0
              AND COALESCE(r.is_disabled, 0) = 0
            ORDER BY
                LOWER(r.full_name),
                tc.issue_number,
                tc.task_candidate_id
            """
        ).fetchall()

        skill_rows: list[sqlite3.Row] = []
        if _table_exists(connection, "task_skill_requirement"):
            skill_rows = connection.execute(
                """
                SELECT
                    tsr.task_candidate_id,
                    tsr.skill_name,
                    tsr.minimum_level
                FROM task_skill_requirement AS tsr
                JOIN task_candidate AS tc
                  ON tc.task_candidate_id = tsr.task_candidate_id
                JOIN repository AS r
                  ON r.repository_id = tc.repository_id
                WHERE COALESCE(r.is_archived, 0) = 0
                  AND COALESCE(r.is_disabled, 0) = 0
                ORDER BY
                    tsr.task_candidate_id,
                    LOWER(tsr.skill_name),
                    tsr.minimum_level
                """
            ).fetchall()
    finally:
        connection.close()

    skills: dict[int, dict[str, int]] = defaultdict(dict)
    for row in skill_rows:
        task_id = int(row["task_candidate_id"])
        skill_name = str(row["skill_name"] or "").strip()
        minimum_level = _safe_int(row["minimum_level"])
        if skill_name and minimum_level is not None:
            skills[task_id][skill_name] = minimum_level

    return {
        "database_path": str(resolved),
        "records": [dict(row) for row in rows],
        "skills": {
            task_id: dict(sorted(values.items(), key=lambda item: item[0].casefold()))
            for task_id, values in sorted(skills.items())
        },
    }


def _performance_signal(task_types: Sequence[str], evidence: Mapping[str, Any]) -> bool:
    if "performance" in {value.casefold() for value in task_types}:
        return True
    auxiliary = evidence.get("auxiliary_signals")
    if not isinstance(auxiliary, Mapping):
        return False
    value = auxiliary.get("performance")
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return _truthy(value)


def _normalize_record(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise TypeError("each diagnostic record must be a mapping")

    labels_value = raw.get("labels") if "labels" in raw else raw.get("labels_json")
    task_types_value = (
        raw.get("task_types")
        if "task_types" in raw
        else raw.get("task_types_json")
    )
    evidence_value = (
        raw.get("feature_evidence")
        if "feature_evidence" in raw
        else raw.get("feature_evidence_json")
    )

    labels, labels_status = _parse_string_list(labels_value)
    task_types_original, task_types_status = _parse_string_list(task_types_value)
    task_types = sorted(
        {item.casefold() for item in task_types_original},
        key=str.casefold,
    )
    evidence, evidence_status = _parse_object(evidence_value)
    assessment, assessment_status, assessment_errors = (
        _parse_difficulty_assessment(evidence)
        if evidence_status == "ok"
        else ({}, "missing" if evidence_status == "missing" else "invalid", [])
    )
    if evidence_status == "invalid":
        assessment_errors = ["feature_evidence_invalid"]
    body_text = str(raw.get("body_text") or "")

    normalized = {
        "task_candidate_id": _safe_int(raw.get("task_candidate_id")) or 0,
        "repository": str(raw.get("repository") or ""),
        "primary_language": str(raw.get("primary_language") or ""),
        "issue_number": _safe_int(raw.get("issue_number")) or 0,
        "html_url": str(raw.get("html_url") or ""),
        "title": str(raw.get("title") or ""),
        "labels": labels,
        "labels_status": labels_status,
        "task_types": task_types,
        "task_types_status": task_types_status,
        "feature_evidence": evidence,
        "feature_evidence_status": evidence_status,
        "difficulty_assessment": assessment,
        "difficulty_assessment_status": assessment_status,
        "difficulty_assessment_errors": assessment_errors,
        "candidate_eligibility": str(raw.get("candidate_eligibility") or ""),
        "newcomer_label_signal": _truthy(raw.get("newcomer_label_signal")),
        "comment_count": _safe_int(raw.get("comment_count")) or 0,
        "text_clarity_score": _safe_float(raw.get("text_clarity_score")),
        "estimated_code_difficulty": _safe_int(
            raw.get("estimated_code_difficulty")
        ),
        "estimated_setup_difficulty": _safe_int(
            raw.get("estimated_setup_difficulty")
        ),
        "estimated_project_context_difficulty": _safe_int(
            raw.get("estimated_project_context_difficulty")
        ),
        "estimated_collaboration_difficulty": _safe_int(
            raw.get("estimated_collaboration_difficulty")
        ),
        "estimated_effort_bucket": str(
            raw.get("estimated_effort_bucket") or ""
        ),
        "novice_fit_probability": _safe_float(
            raw.get("novice_fit_probability")
        ),
        "newcomer_score": _safe_float(raw.get("newcomer_score")),
        "growth_value_score": _safe_float(raw.get("growth_value_score")),
        "task_feature_version": str(raw.get("task_feature_version") or ""),
        "body_missing": not body_text.strip(),
        "performance_signal": _performance_signal(task_types, evidence),
        # Internal-only fields. They are never copied verbatim to the report.
        "_body_text": body_text,
    }
    return normalized


def _normalize_records(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized = [_normalize_record(record) for record in records]
    normalized.sort(key=_record_sort_key)
    return normalized


def _rate(count: int, total: int) -> float | None:
    return round(count / total, 4) if total else None


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _percentile(sorted_values: Sequence[float], percentile: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return round(float(sorted_values[0]), 4)
    position = (len(sorted_values) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(float(sorted_values[lower]), 4)
    fraction = position - lower
    result = sorted_values[lower] + (
        sorted_values[upper] - sorted_values[lower]
    ) * fraction
    return round(float(result), 4)


def _difficulty_distribution(
    records: Sequence[Mapping[str, Any]], field: str
) -> dict[str, Any]:
    counts = {str(level): 0 for level in range(4)}
    missing = 0
    invalid = 0
    valid_values: list[float] = []
    for record in records:
        value = _safe_int(record.get(field))
        if value is None:
            missing += 1
        elif 0 <= value <= 3:
            counts[str(value)] += 1
            valid_values.append(float(value))
        else:
            invalid += 1
    total = len(records)
    return {
        "total": total,
        "valid_count": len(valid_values),
        "missing_count": missing,
        "invalid_count": invalid,
        "level_counts": counts,
        "level_rates": {
            level: _rate(count, total) for level, count in counts.items()
        },
        "mean": _mean(valid_values),
        "unused_levels": [
            int(level) for level, count in counts.items() if count == 0
        ],
    }


def _tuple_distribution(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counter: Counter[tuple[int, int, int, int]] = Counter()
    invalid_count = 0
    for record in records:
        values: list[int] = []
        for _, field in DIFFICULTY_FIELDS:
            value = _safe_int(record.get(field))
            if value is None or not 0 <= value <= 3:
                values = []
                break
            values.append(value)
        if len(values) == 4:
            counter[tuple(values)] += 1
        else:
            invalid_count += 1

    valid_count = sum(counter.values())
    combinations = [
        {
            "difficulty_tuple": list(values),
            "count": count,
            "rate": _rate(count, valid_count),
        }
        for values, count in sorted(
            counter.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    top_one = combinations[0]["count"] if combinations else 0
    top_three = sum(item["count"] for item in combinations[:3])
    hhi = (
        round(sum((count / valid_count) ** 2 for count in counter.values()), 6)
        if valid_count
        else None
    )
    return {
        "valid_count": valid_count,
        "invalid_or_missing_count": invalid_count,
        "unique_combination_count": len(combinations),
        "top_combination_rate": _rate(top_one, valid_count),
        "top_three_combination_rate": _rate(top_three, valid_count),
        "herfindahl_index": hhi,
        "combinations": combinations,
    }


def _categorical_distribution(
    values: Iterable[str], ordered_values: Sequence[str]
) -> dict[str, Any]:
    counter = Counter(str(value or "<missing>") for value in values)
    ordered = {
        value: counter.pop(value, 0) for value in ordered_values
    }
    for value in sorted(counter, key=str.casefold):
        ordered[value] = counter[value]
    total = sum(ordered.values())
    return {
        "total": total,
        "counts": ordered,
        "rates": {key: _rate(value, total) for key, value in ordered.items()},
    }


def _numeric_distribution(
    records: Sequence[Mapping[str, Any]],
    field: str,
    bin_edges: Sequence[float],
) -> dict[str, Any]:
    values = sorted(
        value
        for record in records
        if (value := _safe_float(record.get(field))) is not None
    )
    total = len(records)
    missing_count = total - len(values)
    counter = Counter(round(value, 6) for value in values)
    duplicated = {value: count for value, count in counter.items() if count > 1}

    bins: list[dict[str, Any]] = []
    for index in range(len(bin_edges) - 1):
        lower = float(bin_edges[index])
        upper = float(bin_edges[index + 1])
        is_last = index == len(bin_edges) - 2
        count = sum(
            lower <= value <= upper if is_last else lower <= value < upper
            for value in values
        )
        bins.append(
            {
                "lower": lower,
                "upper": upper,
                "upper_inclusive": is_last,
                "count": count,
                "rate": _rate(count, len(values)),
            }
        )

    top_duplicates = [
        {"value": value, "count": count}
        for value, count in sorted(
            duplicated.items(), key=lambda item: (-item[1], item[0])
        )[:20]
    ]
    return {
        "total": total,
        "valid_count": len(values),
        "missing_or_invalid_count": missing_count,
        "minimum": round(values[0], 4) if values else None,
        "maximum": round(values[-1], 4) if values else None,
        "mean": _mean(values),
        "p25": _percentile(values, 0.25),
        "median": _percentile(values, 0.50),
        "p75": _percentile(values, 0.75),
        "unique_value_count": len(counter),
        "duplicated_value_count": len(duplicated),
        "records_on_duplicated_values": sum(duplicated.values()),
        "top_duplicated_values": top_duplicates,
        "bins": bins,
    }


def legacy_sum_effort_bucket(record: Mapping[str, Any]) -> str | None:
    """Return the effort bucket produced by the retired four-dimension sum.

    This is a comparison baseline only. A mismatch is not a validation error for
    difficulty-rules-v0.2.
    """

    values: list[int] = []
    for _, field in DIFFICULTY_FIELDS:
        value = _safe_int(record.get(field))
        if value is None or not 0 <= value <= 3:
            return None
        values.append(value)
    total = sum(values)
    if total <= 2:
        return "under_2h"
    if total <= 4:
        return "half_day"
    if total <= 6:
        return "one_day"
    return "multi_day"


def expected_effort_bucket(record: Mapping[str, Any]) -> str | None:
    """Deprecated compatibility alias for :func:`legacy_sum_effort_bucket`."""

    return legacy_sum_effort_bucket(record)


def _legacy_effort_comparison(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    comparable_count = 0
    same_count = 0
    different_count = 0
    cross_two_or_more = 0
    direction = Counter(
        {
            "same": 0,
            "external_higher_than_legacy": 0,
            "external_lower_than_legacy": 0,
            "not_comparable": 0,
        }
    )
    for record in records:
        legacy = legacy_sum_effort_bucket(record)
        external = str(record.get("estimated_effort_bucket") or "")
        if legacy not in EFFORT_ORDER or external not in EFFORT_ORDER:
            direction["not_comparable"] += 1
            continue
        comparable_count += 1
        difference = EFFORT_ORDER[external] - EFFORT_ORDER[legacy]
        if difference == 0:
            same_count += 1
            direction["same"] += 1
        else:
            different_count += 1
            direction[
                "external_higher_than_legacy"
                if difference > 0
                else "external_lower_than_legacy"
            ] += 1
            if abs(difference) >= 2:
                cross_two_or_more += 1
    return {
        "semantic": (
            "comparison_with_retired_four_dimension_sum; differences_are_not_errors"
        ),
        "comparable_count": comparable_count,
        "same_as_legacy_count": same_count,
        "different_from_legacy_count": different_count,
        "same_as_legacy_rate": _rate(same_count, comparable_count),
        "difference_direction_counts": dict(direction),
        "cross_two_or_more_bucket_count": cross_two_or_more,
    }


def _count_distribution(values: Iterable[int]) -> dict[str, Any]:
    counter = Counter(int(value) for value in values)
    total = sum(counter.values())
    counts = {str(value): counter[value] for value in sorted(counter)}
    return {
        "total": total,
        "counts": counts,
        "rates": {key: _rate(value, total) for key, value in counts.items()},
    }


def _difficulty_assessment_summary(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    status_distribution = _categorical_distribution(
        (str(record.get("difficulty_assessment_status") or "invalid") for record in records),
        VALID_DIFFICULTY_STATUS,
    )
    valid_records = [
        record
        for record in records
        if record.get("difficulty_assessment_status") == "ok"
    ]
    invalid_reason_counter: Counter[str] = Counter()
    for record in records:
        if record.get("difficulty_assessment_status") == "invalid":
            invalid_reason_counter.update(
                str(value) for value in record.get("difficulty_assessment_errors") or []
            )

    formula_versions = _categorical_distribution(
        (
            str(record["difficulty_assessment"].get("formula_version") or "<missing>")
            for record in valid_records
        ),
        (),
    )
    actionability = _categorical_distribution(
        (
            str(
                record["difficulty_assessment"]["information_quality"].get(
                    "actionability"
                )
            )
            for record in valid_records
        ),
        VALID_ACTIONABILITY,
    )
    information_confidence = _categorical_distribution(
        (
            str(
                record["difficulty_assessment"]["information_quality"].get(
                    "confidence"
                )
            )
            for record in valid_records
        ),
        VALID_CONFIDENCE,
    )
    information_reason_counter: Counter[str] = Counter()
    information_body_missing_count = 0
    for record in valid_records:
        information = record["difficulty_assessment"]["information_quality"]
        information_body_missing_count += bool(information.get("body_missing"))
        information_reason_counter.update(
            str(value) for value in information.get("reasons") or []
        )

    dimensions: dict[str, Any] = {}
    evidence_source_counter: Counter[str] = Counter()
    evidence_strength_counter: Counter[str] = Counter()
    evidence_rule_counter: Counter[str] = Counter()
    evidence_reason_counter: Counter[str] = Counter()
    for dimension in DIFFICULTY_DIMENSION_NAMES:
        items = [
            record["difficulty_assessment"]["dimensions"][dimension]
            for record in valid_records
        ]
        dimension_evidence = [
            evidence
            for item in items
            for evidence in item.get("evidence") or []
        ]
        for evidence in dimension_evidence:
            evidence_source_counter[str(evidence.get("source") or "<missing>")] += 1
            evidence_strength_counter[
                str(evidence.get("strength") or "<missing>")
            ] += 1
            evidence_rule_counter[str(evidence.get("rule_id") or "<missing>")] += 1
            evidence_reason_counter[str(evidence.get("reason") or "<missing>")] += 1
        dimensions[dimension] = {
            "prior_distribution": _categorical_distribution(
                (str(item.get("prior")) for item in items),
                tuple(str(level) for level in range(4)),
            ),
            "level_distribution": _categorical_distribution(
                (str(item.get("level")) for item in items),
                tuple(str(level) for level in range(4)),
            ),
            "confidence_distribution": _categorical_distribution(
                (str(item.get("confidence")) for item in items),
                VALID_CONFIDENCE,
            ),
            "evidence_count_distribution": _count_distribution(
                len(item.get("evidence") or []) for item in items
            ),
            "conflict_count_total": sum(
                len(item.get("conflicts") or []) for item in items
            ),
            "records_with_conflicts": sum(
                bool(item.get("conflicts")) for item in items
            ),
        }

    effort_items = [
        record["difficulty_assessment"]["effort"] for record in valid_records
    ]
    external_bucket_mismatch = 0
    external_bucket_comparable = 0
    for record, effort in zip(valid_records, effort_items):
        external = str(record.get("estimated_effort_bucket") or "")
        evidence_bucket = str(effort.get("bucket") or "")
        if external in VALID_EFFORT and evidence_bucket in VALID_EFFORT:
            external_bucket_comparable += 1
            external_bucket_mismatch += external != evidence_bucket
        for evidence in effort.get("evidence") or []:
            evidence_source_counter[str(evidence.get("source") or "<missing>")] += 1
            evidence_strength_counter["<not_applicable>"] += 1
            evidence_rule_counter[str(evidence.get("rule_id") or "<missing>")] += 1
            evidence_reason_counter[str(evidence.get("reason") or "<missing>")] += 1

    return {
        "status": status_distribution,
        "invalid_reason_counts": dict(sorted(invalid_reason_counter.items())),
        "formula_version_distribution": formula_versions,
        "information_quality": {
            "actionability_distribution": actionability,
            "confidence_distribution": information_confidence,
            "reason_counts": dict(sorted(information_reason_counter.items())),
            "body_missing_count": information_body_missing_count,
        },
        "dimensions": dimensions,
        "effort": {
            "scope_distribution": _categorical_distribution(
                (str(item.get("scope")) for item in effort_items),
                VALID_EFFORT_SCOPE,
            ),
            "applicable_distribution": _categorical_distribution(
                (str(bool(item.get("applicable"))).lower() for item in effort_items),
                ("true", "false"),
            ),
            "provisional_distribution": _categorical_distribution(
                (str(bool(item.get("provisional"))).lower() for item in effort_items),
                ("true", "false"),
            ),
            "confidence_distribution": _categorical_distribution(
                (str(item.get("confidence")) for item in effort_items),
                VALID_CONFIDENCE,
            ),
            "evidence_bucket_external_comparison": {
                "comparable_count": external_bucket_comparable,
                "mismatch_count": external_bucket_mismatch,
                "match_count": external_bucket_comparable - external_bucket_mismatch,
                "match_rate": _rate(
                    external_bucket_comparable - external_bucket_mismatch,
                    external_bucket_comparable,
                ),
            },
        },
        "evidence": {
            "source_counts": dict(sorted(evidence_source_counter.items())),
            "strength_counts": dict(sorted(evidence_strength_counter.items())),
            "rule_id_counts": dict(sorted(evidence_rule_counter.items())),
            "reason_counts": dict(sorted(evidence_reason_counter.items())),
        },
    }


def _summary(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    dimensions = {
        name: _difficulty_distribution(records, field)
        for name, field in DIFFICULTY_FIELDS
    }
    scores = {
        name: _numeric_distribution(records, field, bins)
        for name, field, bins in SCORE_FIELDS
    }
    legacy = _legacy_effort_comparison(records)
    deprecated_consistency_alias = {
        "semantic": "deprecated_alias_of_legacy_effort_comparison",
        "comparable_count": legacy["comparable_count"],
        "mismatch_count": legacy["different_from_legacy_count"],
        "consistent_count": legacy["same_as_legacy_count"],
        "consistent_rate": legacy["same_as_legacy_rate"],
    }

    return {
        "record_count": len(records),
        "candidate_eligibility_counts": dict(
            sorted(
                Counter(
                    str(record.get("candidate_eligibility") or "<missing>")
                    for record in records
                ).items()
            )
        ),
        "newcomer_label_count": sum(
            bool(record.get("newcomer_label_signal")) for record in records
        ),
        "performance_signal_count": sum(
            bool(record.get("performance_signal")) for record in records
        ),
        "body_missing_count": sum(
            bool(record.get("body_missing")) for record in records
        ),
        "malformed_json": {
            "labels_invalid_count": sum(
                record.get("labels_status") == "invalid" for record in records
            ),
            "task_types_invalid_count": sum(
                record.get("task_types_status") == "invalid" for record in records
            ),
            "feature_evidence_invalid_count": sum(
                record.get("feature_evidence_status") == "invalid"
                for record in records
            ),
        },
        "difficulty_dimensions": dimensions,
        "difficulty_tuples": _tuple_distribution(records),
        "effort_bucket": _categorical_distribution(
            (
                str(record.get("estimated_effort_bucket") or "<missing>")
                for record in records
            ),
            VALID_EFFORT,
        ),
        "score_distributions": scores,
        "legacy_effort_comparison": legacy,
        "effort_consistency": deprecated_consistency_alias,
        "difficulty_assessment": _difficulty_assessment_summary(records),
    }


def _group_summary(name: str, records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    difficulty = {}
    for short_name, field in DIFFICULTY_FIELDS:
        distribution = _difficulty_distribution(records, field)
        difficulty[short_name] = {
            "mean": distribution["mean"],
            "level_counts": distribution["level_counts"],
            "unused_levels": distribution["unused_levels"],
        }
    return {
        "name": name,
        "record_count": len(records),
        "difficulty": difficulty,
        "effort_bucket_counts": _categorical_distribution(
            (
                str(record.get("estimated_effort_bucket") or "<missing>")
                for record in records
            ),
            VALID_EFFORT,
        )["counts"],
        "mean_text_clarity": _mean(
            [
                value
                for record in records
                if (value := _safe_float(record.get("text_clarity_score")))
                is not None
            ]
        ),
        "mean_newcomer_score": _mean(
            [
                value
                for record in records
                if (value := _safe_float(record.get("newcomer_score")))
                is not None
            ]
        ),
        "mean_growth_value_score": _mean(
            [
                value
                for record in records
                if (value := _safe_float(record.get("growth_value_score")))
                is not None
            ]
        ),
    }


def _grouped(
    records: Sequence[Mapping[str, Any]],
    key_name: str,
    key_function: Any,
) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        values = key_function(record)
        if isinstance(values, str):
            values = [values]
        for value in values:
            groups[str(value or "<unknown>")].append(record)
    result = []
    for value, items in groups.items():
        summary = _group_summary(value, items)
        summary[key_name] = summary.pop("name")
        result.append(summary)
    return sorted(
        result,
        key=lambda item: (-int(item["record_count"]), str(item[key_name]).casefold()),
    )


def _exact_task_group(record: Mapping[str, Any]) -> str | None:
    task_types = set(record.get("task_types") or [])
    exact = {
        "documentation_only": {"documentation"},
        "testing_only": {"testing"},
        "build_tooling_only": {"build_tooling"},
        "refactor_only": {"refactor"},
        "feature_only": {"feature"},
        "bug_fix_only": {"bug_fix"},
        "other": {"other"},
    }
    for name, expected in exact.items():
        if task_types == expected:
            return name
    return None


def _compact_record(record: Mapping[str, Any], reason: str) -> dict[str, Any]:
    return {
        "task_candidate_id": _safe_int(record.get("task_candidate_id")) or 0,
        "repository": str(record.get("repository") or ""),
        "issue_number": _safe_int(record.get("issue_number")) or 0,
        "html_url": str(record.get("html_url") or ""),
        "title": str(record.get("title") or ""),
        "labels": list(record.get("labels") or []),
        "task_types": list(record.get("task_types") or []),
        "difficulty": {
            short_name: _safe_int(record.get(field))
            for short_name, field in DIFFICULTY_FIELDS
        },
        "effort": str(record.get("estimated_effort_bucket") or ""),
        "clarity": _safe_float(record.get("text_clarity_score")),
        "newcomer_score": _safe_float(record.get("newcomer_score")),
        "growth_value_score": _safe_float(record.get("growth_value_score")),
        "difficulty_assessment_status": str(
            record.get("difficulty_assessment_status") or "missing"
        ),
        "actionability": (
            record.get("difficulty_assessment", {})
            .get("information_quality", {})
            .get("actionability")
        ),
        "information_confidence": (
            record.get("difficulty_assessment", {})
            .get("information_quality", {})
            .get("confidence")
        ),
        "effort_applicable": (
            record.get("difficulty_assessment", {}).get("effort", {}).get("applicable")
        ),
        "trigger_reason": reason,
    }


def _record_list(
    records: Iterable[tuple[Mapping[str, Any], str]],
) -> list[dict[str, Any]]:
    output = [_compact_record(record, reason) for record, reason in records]
    output.sort(key=_record_sort_key)
    return output


def _label_text(record: Mapping[str, Any]) -> str:
    return " ".join(str(label) for label in record.get("labels") or []).casefold()


def _setup_matches(text: str) -> list[str]:
    return [name for name, pattern in _SETUP_PATTERNS if pattern.search(text)]


def _rule_trigger_queues(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    queues: dict[str, list[dict[str, Any]]] = {}

    newcomer_items = _record_list(
        (
            record,
            "newcomer label present and code/context are capped at or below 1",
        )
        for record in records
        if record.get("newcomer_label_signal")
        and (_safe_int(record.get("estimated_code_difficulty")) or 0) <= 1
        and (
            _safe_int(record.get("estimated_project_context_difficulty")) or 0
        )
        <= 1
    )
    queues["newcomer_cap"] = newcomer_items

    performance_items = _record_list(
        (
            record,
            "performance signal with code=3 or project_context=3",
        )
        for record in records
        if record.get("performance_signal")
        and (
            _safe_int(record.get("estimated_code_difficulty")) == 3
            or _safe_int(record.get("estimated_project_context_difficulty")) == 3
        )
    )
    queues["performance_escalation"] = performance_items

    queues["documentation_only_reduction"] = _record_list(
        (record, "documentation-only task reduced to code=0 and setup=0")
        for record in records
        if set(record.get("task_types") or []) == {"documentation"}
        and _safe_int(record.get("estimated_code_difficulty")) == 0
        and _safe_int(record.get("estimated_setup_difficulty")) == 0
    )

    queues["refactor_context_escalation"] = _record_list(
        (record, "refactor task escalated project_context to 3")
        for record in records
        if "refactor" in set(record.get("task_types") or [])
        and _safe_int(record.get("estimated_project_context_difficulty")) == 3
    )

    setup_queue: list[tuple[Mapping[str, Any], str]] = []
    for record in records:
        if _safe_int(record.get("estimated_setup_difficulty")) != 2:
            continue
        title_matches = _setup_matches(str(record.get("title") or ""))
        body_matches = _setup_matches(str(record.get("_body_text") or ""))
        matches = sorted(set(title_matches + body_matches))
        if matches:
            sources = []
            if title_matches:
                sources.append("title")
            if body_matches:
                sources.append("body")
            setup_queue.append(
                (
                    record,
                    "setup keyword escalation from "
                    + "+".join(sources)
                    + ": "
                    + ", ".join(matches),
                )
            )
    queues["setup_keyword_escalation"] = _record_list(setup_queue)

    queues["core_architecture_api_context_escalation"] = _record_list(
        (
            record,
            "context label substring matched core/architecture/api",
        )
        for record in records
        if (_safe_int(record.get("estimated_project_context_difficulty")) or 0) >= 2
        and _CONTEXT_LABEL_PATTERN.search(_label_text(record))
    )

    queues["discussion_design_collaboration_escalation"] = _record_list(
        (
            record,
            "discussion/design label raised collaboration to 2",
        )
        for record in records
        if (_safe_int(record.get("estimated_collaboration_difficulty")) or 0) >= 2
        and _DISCUSSION_LABEL_PATTERN.search(_label_text(record))
    )

    comment_items: list[tuple[Mapping[str, Any], str]] = []
    for record in records:
        comments = _safe_int(record.get("comment_count")) or 0
        collaboration = _safe_int(
            record.get("estimated_collaboration_difficulty")
        )
        expected = 0 if comments < 3 else 1 if comments < 10 else 2
        comment_items.append(
            (
                record,
                f"comment_count={comments} maps to base collaboration={expected}; "
                f"stored={collaboration}",
            )
        )
    queues["comment_count_collaboration_thresholds"] = _record_list(comment_items)

    return {
        name: {"count": len(items), "records": items}
        for name, items in sorted(queues.items())
    }


def _after_anomalies(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    anomaly_pairs: dict[str, Iterable[tuple[Mapping[str, Any], str]]] = {
        "other_multi_day": (
            (record, "task_types is only other while effort is multi_day")
            for record in records
            if set(record.get("task_types") or []) == {"other"}
            and record.get("estimated_effort_bucket") == "multi_day"
        ),
        "performance_newcomer": (
            (record, "performance signal and newcomer label are both present")
            for record in records
            if record.get("performance_signal")
            and record.get("newcomer_label_signal")
        ),
        "documentation_only_high_effort": (
            (
                record,
                "documentation-only task has one_day or multi_day effort",
            )
            for record in records
            if set(record.get("task_types") or []) == {"documentation"}
            and record.get("estimated_effort_bucket") in {"one_day", "multi_day"}
        ),
        "body_missing": (
            (record, "body_text is missing or blank")
            for record in records
            if record.get("body_missing")
        ),
        "code_zero_high_effort": (
            (record, "code difficulty is 0 but effort is one_day or multi_day")
            for record in records
            if _safe_int(record.get("estimated_code_difficulty")) == 0
            and record.get("estimated_effort_bucket") in {"one_day", "multi_day"}
        ),
        "code_three_low_effort": (
            (record, "code difficulty is 3 but effort is under_2h or half_day")
            for record in records
            if _safe_int(record.get("estimated_code_difficulty")) == 3
            and record.get("estimated_effort_bucket") in {"under_2h", "half_day"}
        ),
        "context_high_broad_label_only": (
            (
                record,
                "project context is 2/3 with broad "
                "core/architecture/api label evidence only",
            )
            for record in records
            if (_safe_int(record.get("estimated_project_context_difficulty")) or 0) >= 2
            and _CONTEXT_LABEL_PATTERN.search(_label_text(record))
            and "refactor" not in set(record.get("task_types") or [])
            and not record.get("performance_signal")
        ),
        "collaboration_two_without_discussion_design": (
            (
                record,
                "collaboration is 2 without a discussion/design label",
            )
            for record in records
            if _safe_int(record.get("estimated_collaboration_difficulty")) == 2
            and not _DISCUSSION_LABEL_PATTERN.search(_label_text(record))
        ),
    }

    setup_body_only: list[tuple[Mapping[str, Any], str]] = []
    for record in records:
        if _safe_int(record.get("estimated_setup_difficulty")) != 2:
            continue
        title_matches = _setup_matches(str(record.get("title") or ""))
        body_matches = _setup_matches(str(record.get("_body_text") or ""))
        if not title_matches and body_matches:
            setup_body_only.append(
                (
                    record,
                    "setup=2 triggered only by body keywords: "
                    + ", ".join(sorted(set(body_matches))),
                )
            )
    anomaly_pairs["setup_two_body_only_keyword"] = setup_body_only

    output: dict[str, dict[str, Any]] = {}
    for name, pairs in anomaly_pairs.items():
        items = _record_list(pairs)
        output[name] = {"count": len(items), "records": items}
    return dict(sorted(output.items()))



def _assessment_part(
    record: Mapping[str, Any], *path: str
) -> Any:
    value: Any = record.get("difficulty_assessment") or {}
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _newcomer_technical_difficulty(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    newcomer = [record for record in records if record.get("newcomer_label_signal")]
    return {
        "newcomer_count": len(newcomer),
        "code_level_counts": _difficulty_distribution(
            newcomer, "estimated_code_difficulty"
        )["level_counts"],
        "context_level_counts": _difficulty_distribution(
            newcomer, "estimated_project_context_difficulty"
        )["level_counts"],
        "setup_level_counts": _difficulty_distribution(
            newcomer, "estimated_setup_difficulty"
        )["level_counts"],
        "effort_counts": _categorical_distribution(
            (str(record.get("estimated_effort_bucket") or "") for record in newcomer),
            VALID_EFFORT,
        )["counts"],
    }


def _has_collaboration_design_evidence(record: Mapping[str, Any]) -> bool:
    evidence = _assessment_part(record, "dimensions", "collaboration", "evidence")
    if not isinstance(evidence, list):
        return False
    semantic_markers = (
        "discussion",
        "design",
        "rfc",
        "proposal",
        "multiple_options",
        "cross_team",
        "breaking",
        "dispute",
    )
    for item in evidence:
        if not isinstance(item, Mapping):
            continue
        if item.get("strength") not in {"medium", "strong"}:
            continue
        text = " ".join(
            str(item.get(key) or "") for key in ("rule_id", "reason")
        ).casefold()
        if any(marker in text for marker in semantic_markers):
            return True
    return False


def _task_type_evidence_regression(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    invalid_contract: list[tuple[Mapping[str, Any], str]] = []
    missing_public_evidence: list[tuple[Mapping[str, Any], str]] = []
    valid_output_types = set(PUBLIC_TASK_TYPES) | {"other"}
    for record in records:
        task_types = set(record.get("task_types") or [])
        if (
            not task_types
            or not task_types.issubset(valid_output_types)
            or ("other" in task_types and len(task_types) > 1)
        ):
            invalid_contract.append(
                (record, "task_types violate public-types-or-other output contract")
            )
        task_type_evidence = (
            record.get("feature_evidence", {}).get("task_type_evidence")
            if isinstance(record.get("feature_evidence"), Mapping)
            else None
        )
        if not isinstance(task_type_evidence, Mapping):
            task_type_evidence = {}
        missing = sorted(
            task_type
            for task_type in task_types
            if task_type in PUBLIC_TASK_TYPES
            and not isinstance(task_type_evidence.get(task_type), list)
        )
        if missing:
            missing_public_evidence.append(
                (
                    record,
                    "accepted public task types lack task_type_evidence: "
                    + ", ".join(missing),
                )
            )
    invalid_records = _record_list(invalid_contract)
    missing_records = _record_list(missing_public_evidence)
    return {
        "invalid_output_contract": {
            "count": len(invalid_records),
            "records": invalid_records,
        },
        "missing_public_task_type_evidence": {
            "count": len(missing_records),
            "records": missing_records,
        },
    }


def _difficulty_v02_checks(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    performance_hard_three = _record_list(
        (
            record,
            "performance signal with code=3 and project_context=3",
        )
        for record in records
        if record.get("performance_signal")
        and _safe_int(record.get("estimated_code_difficulty")) == 3
        and _safe_int(record.get("estimated_project_context_difficulty")) == 3
    )

    setup_reported_only: list[tuple[Mapping[str, Any], str]] = []
    collaboration_comment_only: list[tuple[Mapping[str, Any], str]] = []
    unclear_high: list[tuple[Mapping[str, Any], str]] = []
    body_missing_high: list[tuple[Mapping[str, Any], str]] = []
    effort_contract: list[tuple[Mapping[str, Any], str]] = []
    level_three_without_strong: dict[str, list[tuple[Mapping[str, Any], str]]] = {
        dimension: [] for dimension in DIFFICULTY_DIMENSION_NAMES
    }
    non_actionable: list[Mapping[str, Any]] = []

    for record in records:
        if record.get("difficulty_assessment_status") != "ok":
            continue
        assessment = record["difficulty_assessment"]
        information = assessment["information_quality"]
        effort = assessment["effort"]
        dimensions = assessment["dimensions"]

        setup = dimensions["setup"]
        setup_evidence = setup.get("evidence") or []
        if (
            int(setup.get("level") or 0) > 1
            and setup_evidence
            and all(
                str(item.get("rule_id") or "")
                == "difficulty.setup.reported_environment_only"
                for item in setup_evidence
            )
        ):
            setup_reported_only.append(
                (
                    record,
                    "setup>1 although only reported_environment_only evidence exists",
                )
            )

        if int(dimensions["collaboration"].get("level") or 0) > 1 and not (
            _has_collaboration_design_evidence(record)
        ):
            collaboration_comment_only.append(
                (
                    record,
                    "collaboration>1 without medium/strong design coordination evidence",
                )
            )

        if information.get("actionability") == "unclear" and information.get(
            "confidence"
        ) == "high":
            unclear_high.append(
                (record, "unclear actionability has high information confidence")
            )
        if information.get("body_missing") and information.get("confidence") == "high":
            body_missing_high.append(
                (record, "body is missing but information confidence is high")
            )
        if not effort.get("applicable") and not (
            effort.get("provisional") is True and effort.get("confidence") == "low"
        ):
            effort_contract.append(
                (
                    record,
                    "applicable=false must imply provisional=true and confidence=low",
                )
            )
        if information.get("actionability") == "non_actionable":
            non_actionable.append(record)

        for dimension in DIFFICULTY_DIMENSION_NAMES:
            item = dimensions[dimension]
            if int(item.get("level") or 0) != 3:
                continue
            evidence = item.get("evidence") or []
            if not any(
                isinstance(evidence_item, Mapping)
                and evidence_item.get("strength") == "strong"
                for evidence_item in evidence
            ):
                level_three_without_strong[dimension].append(
                    (record, f"{dimension}=3 without strong evidence")
                )

    exact_group_names = (
        "documentation_only",
        "testing_only",
        "build_tooling_only",
        "refactor_only",
        "feature_only",
        "bug_fix_only",
    )
    exact_groups = {
        name: [record for record in records if _exact_task_group(record) == name]
        for name in exact_group_names
    }

    non_actionable_effort_counts = _categorical_distribution(
        (
            str(record.get("estimated_effort_bucket") or "")
            for record in non_actionable
        ),
        VALID_EFFORT,
    )["counts"]
    non_actionable_applicable_false = sum(
        _assessment_part(record, "effort", "applicable") is False
        for record in non_actionable
    )
    non_actionable_applicable_true = sum(
        _assessment_part(record, "effort", "applicable") is True
        for record in non_actionable
    )

    return {
        "newcomer_technical_difficulty": _newcomer_technical_difficulty(records),
        "performance_uniform_hard_three": {
            "count": len(performance_hard_three),
            "records": performance_hard_three,
        },
        "setup_reported_environment_only": {
            "count": len(setup_reported_only),
            "records": _record_list(setup_reported_only),
        },
        "collaboration_comment_only_above_one": {
            "count": len(collaboration_comment_only),
            "records": _record_list(collaboration_comment_only),
        },
        "task_type_internal_stratification": {
            name: _group_summary(name, items)
            for name, items in sorted(exact_groups.items())
        },
        "non_actionable_effort": {
            "count": len(non_actionable),
            "external_effort_bucket_counts": non_actionable_effort_counts,
            "applicable_false_count": non_actionable_applicable_false,
            "applicable_true_error_count": non_actionable_applicable_true,
            "records": _record_list(
                (record, "actionability=non_actionable")
                for record in non_actionable
            ),
        },
        "unclear_high_confidence": {
            "count": len(unclear_high),
            "records": _record_list(unclear_high),
        },
        "body_missing_high_confidence": {
            "count": len(body_missing_high),
            "records": _record_list(body_missing_high),
        },
        "effort_not_applicable_contract": {
            "count": len(effort_contract),
            "records": _record_list(effort_contract),
        },
        "dimension_level_three_without_strong_evidence": {
            dimension: {
                "count": len(items),
                "records": _record_list(items),
            }
            for dimension, items in sorted(level_three_without_strong.items())
        },
        "task_type_evidence_regression": _task_type_evidence_regression(records),
    }


def _comparison_record(
    after: Mapping[str, Any],
    reason: str,
    changes: Mapping[str, Any],
) -> dict[str, Any]:
    result = _compact_record(after, reason)
    result["changes"] = dict(changes)
    return result


def _build_comparison(
    baseline_records: Sequence[Mapping[str, Any]],
    after_records: Sequence[Mapping[str, Any]],
    baseline_skills: Mapping[int, Mapping[str, int]],
    after_skills: Mapping[int, Mapping[str, int]],
) -> dict[str, Any]:
    baseline_eligible = {
        int(record["task_candidate_id"]): record
        for record in baseline_records
        if record.get("candidate_eligibility") == "eligible"
    }
    after_eligible = {
        int(record["task_candidate_id"]): record
        for record in after_records
        if record.get("candidate_eligibility") == "eligible"
    }
    common_ids = sorted(set(baseline_eligible) & set(after_eligible))

    task_type_changes: list[dict[str, Any]] = []
    difficulty_changes: list[dict[str, Any]] = []
    per_dimension_counts = {name: 0 for name, _ in DIFFICULTY_FIELDS}
    per_dimension_direction = {
        name: {"upgraded": 0, "downgraded": 0, "two_or_more_levels": 0}
        for name, _ in DIFFICULTY_FIELDS
    }
    effort_changes: list[dict[str, Any]] = []
    effort_adjacent_count = 0
    effort_two_or_more_count = 0
    effort_direction = {"higher": 0, "lower": 0}
    novice_changes: list[dict[str, Any]] = []
    growth_changes: list[dict[str, Any]] = []
    skill_changes: list[dict[str, Any]] = []
    effort_jumps: list[dict[str, Any]] = []
    information_quality_added: list[dict[str, Any]] = []
    code_increase_records: list[dict[str, Any]] = []
    setup_increase_records: list[dict[str, Any]] = []

    for task_id in common_ids:
        before = baseline_eligible[task_id]
        after = after_eligible[task_id]
        before_types = list(before.get("task_types") or [])
        after_types = list(after.get("task_types") or [])
        if before_types != after_types:
            task_type_changes.append(
                _comparison_record(
                    after,
                    "task types changed between baseline and after",
                    {"task_types": {"before": before_types, "after": after_types}},
                )
            )

        changed_dimensions: dict[str, Any] = {}
        for short_name, field in DIFFICULTY_FIELDS:
            before_value = _safe_int(before.get(field))
            after_value = _safe_int(after.get(field))
            if before_value == after_value:
                continue
            per_dimension_counts[short_name] += 1
            changed_dimensions[short_name] = {
                "before": before_value,
                "after": after_value,
            }
            if before_value is not None and after_value is not None:
                difference = after_value - before_value
                if difference > 0:
                    per_dimension_direction[short_name]["upgraded"] += 1
                elif difference < 0:
                    per_dimension_direction[short_name]["downgraded"] += 1
                if abs(difference) >= 2:
                    per_dimension_direction[short_name]["two_or_more_levels"] += 1
                if short_name == "code" and difference > 0:
                    code_increase_records.append(
                        _comparison_record(
                            after,
                            "code difficulty increased; static matching filter risk",
                            {"code": {"before": before_value, "after": after_value}},
                        )
                    )
                if short_name == "setup" and difference > 0:
                    setup_increase_records.append(
                        _comparison_record(
                            after,
                            "setup difficulty increased; static matching filter risk",
                            {"setup": {"before": before_value, "after": after_value}},
                        )
                    )
        if changed_dimensions:
            difficulty_changes.append(
                _comparison_record(
                    after,
                    "one or more difficulty dimensions changed",
                    {"difficulty": changed_dimensions},
                )
            )

        before_effort = str(before.get("estimated_effort_bucket") or "")
        after_effort = str(after.get("estimated_effort_bucket") or "")
        if before_effort != after_effort:
            change = {"effort": {"before": before_effort, "after": after_effort}}
            effort_changes.append(
                _comparison_record(after, "effort bucket changed", change)
            )
            if before_effort in EFFORT_ORDER and after_effort in EFFORT_ORDER:
                difference = EFFORT_ORDER[after_effort] - EFFORT_ORDER[before_effort]
                if difference > 0:
                    effort_direction["higher"] += 1
                elif difference < 0:
                    effort_direction["lower"] += 1
                if abs(difference) == 1:
                    effort_adjacent_count += 1
                if abs(difference) >= 2:
                    effort_two_or_more_count += 1
                    effort_jumps.append(
                        _comparison_record(
                            after,
                            "effort changed by two or more buckets",
                            change,
                        )
                    )

        before_novice = _safe_float(before.get("novice_fit_probability"))
        after_novice = _safe_float(after.get("novice_fit_probability"))
        before_newcomer = _safe_float(before.get("newcomer_score"))
        after_newcomer = _safe_float(after.get("newcomer_score"))
        if before_novice != after_novice or before_newcomer != after_newcomer:
            novice_changes.append(
                _comparison_record(
                    after,
                    "novice/newcomer score changed",
                    {
                        "novice_fit_probability": {
                            "before": before_novice,
                            "after": after_novice,
                        },
                        "newcomer_score": {
                            "before": before_newcomer,
                            "after": after_newcomer,
                        },
                    },
                )
            )

        before_growth = _safe_float(before.get("growth_value_score"))
        after_growth = _safe_float(after.get("growth_value_score"))
        if before_growth != after_growth:
            growth_changes.append(
                _comparison_record(
                    after,
                    "growth score changed",
                    {
                        "growth_value_score": {
                            "before": before_growth,
                            "after": after_growth,
                        }
                    },
                )
            )

        before_skill_map = dict(baseline_skills.get(task_id, {}))
        after_skill_map = dict(after_skills.get(task_id, {}))
        all_skills = sorted(
            set(before_skill_map) | set(after_skill_map), key=str.casefold
        )
        changed_skills = {
            skill: {
                "before": before_skill_map.get(skill),
                "after": after_skill_map.get(skill),
            }
            for skill in all_skills
            if before_skill_map.get(skill) != after_skill_map.get(skill)
        }
        if changed_skills:
            skill_changes.append(
                _comparison_record(
                    after,
                    "skill minimum level changed between snapshots",
                    {"skill_minimum_levels": changed_skills},
                )
            )

        if (
            before.get("difficulty_assessment_status") != "ok"
            and after.get("difficulty_assessment_status") == "ok"
        ):
            information_quality_added.append(
                _comparison_record(
                    after,
                    "difficulty information quality was added in after snapshot",
                    {
                        "difficulty_assessment_status": {
                            "before": before.get("difficulty_assessment_status"),
                            "after": "ok",
                        }
                    },
                )
            )

    for collection in (
        task_type_changes,
        difficulty_changes,
        effort_changes,
        novice_changes,
        growth_changes,
        skill_changes,
        effort_jumps,
        information_quality_added,
        code_increase_records,
        setup_increase_records,
    ):
        collection.sort(key=_record_sort_key)

    after_common_records = [after_eligible[task_id] for task_id in common_ids]
    actionability_counts = Counter(
        str(_assessment_part(record, "information_quality", "actionability") or "<missing>")
        for record in after_common_records
    )
    applicable_false_count = sum(
        _assessment_part(record, "effort", "applicable") is False
        for record in after_common_records
    )

    return {
        "scope": "common active eligible candidates aligned by task_candidate_id",
        "baseline_eligible_count": len(baseline_eligible),
        "after_eligible_count": len(after_eligible),
        "common_eligible_count": len(common_ids),
        "baseline_only_task_candidate_ids": sorted(
            set(baseline_eligible) - set(after_eligible)
        ),
        "after_only_task_candidate_ids": sorted(
            set(after_eligible) - set(baseline_eligible)
        ),
        "task_type_changes": {
            "count": len(task_type_changes),
            "records": task_type_changes,
        },
        "difficulty_changes": {
            "count": len(difficulty_changes),
            "by_dimension": per_dimension_counts,
            "by_dimension_direction": per_dimension_direction,
            "records": difficulty_changes,
        },
        "effort_changes": {
            "count": len(effort_changes),
            "adjacent_bucket_count": effort_adjacent_count,
            "two_or_more_bucket_count": effort_two_or_more_count,
            "direction_counts": effort_direction,
            "records": effort_changes,
        },
        "novice_newcomer_score_changes": {
            "count": len(novice_changes),
            "records": novice_changes,
        },
        "growth_score_changes": {
            "count": len(growth_changes),
            "records": growth_changes,
        },
        "skill_minimum_level_changes": {
            "count": len(skill_changes),
            "records": skill_changes,
        },
        "effort_jump_two_or_more_buckets": {
            "count": len(effort_jumps),
            "records": effort_jumps,
        },
        "difficulty_information_quality_added": {
            "count": len(information_quality_added),
            "records": information_quality_added,
        },
        "after_actionability_counts": dict(sorted(actionability_counts.items())),
        "after_effort_applicable_false_count": applicable_false_count,
        "matching_static_risk": {
            "code_increase_count": len(code_increase_records),
            "setup_increase_count": len(setup_increase_records),
            "code_increase_records": code_increase_records,
            "setup_increase_records": setup_increase_records,
            "semantic": (
                "static filter risk only; matching.py was not called and rankings were not computed"
            ),
        },
    }


def build_difficulty_diagnostics(
    after_records: Iterable[Mapping[str, Any]],
    *,
    baseline_records: Iterable[Mapping[str, Any]] = (),
    after_skills: Mapping[int, Mapping[str, int]] | None = None,
    baseline_skills: Mapping[int, Mapping[str, int]] | None = None,
    after_database_path: str | Path | None = None,
    baseline_database_path: str | Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic report from ordinary records and skill mappings."""

    after = _normalize_records(after_records)
    baseline = _normalize_records(baseline_records)
    after_skill_map = after_skills or {}
    baseline_skill_map = baseline_skills or {}

    eligible = [
        record
        for record in after
        if record.get("candidate_eligibility") == "eligible"
    ]
    newcomer_eligible = [
        record
        for record in eligible
        if record.get("newcomer_label_signal")
    ]

    grouped_by_task_type = _grouped(
        eligible,
        "task_type",
        lambda record: list(record.get("task_types") or ["<missing>"]),
    )
    grouped_by_language = _grouped(
        eligible,
        "primary_language",
        lambda record: str(record.get("primary_language") or "<unknown>"),
    )
    grouped_by_repository = _grouped(
        eligible,
        "repository",
        lambda record: str(record.get("repository") or "<unknown>"),
    )

    exact_group_names = (
        "documentation_only",
        "testing_only",
        "build_tooling_only",
        "refactor_only",
        "feature_only",
        "bug_fix_only",
        "other",
    )
    exact_groups: dict[str, list[Mapping[str, Any]]] = {
        name: [] for name in exact_group_names
    }
    for record in eligible:
        group = _exact_task_group(record)
        if group is not None:
            exact_groups[group].append(record)

    comparison_groups = {
        "newcomer": _group_summary("newcomer", newcomer_eligible),
        "non_newcomer": _group_summary(
            "non_newcomer",
            [record for record in eligible if not record.get("newcomer_label_signal")],
        ),
        "performance": _group_summary(
            "performance",
            [record for record in eligible if record.get("performance_signal")],
        ),
        "non_performance": _group_summary(
            "non_performance",
            [record for record in eligible if not record.get("performance_signal")],
        ),
        "body_missing": _group_summary(
            "body_missing",
            [record for record in eligible if record.get("body_missing")],
        ),
        "body_present": _group_summary(
            "body_present",
            [record for record in eligible if not record.get("body_missing")],
        ),
        "exact_task_type_groups": {
            name: _group_summary(name, items)
            for name, items in sorted(exact_groups.items())
        },
    }

    comparison = _build_comparison(
        baseline,
        after,
        baseline_skill_map,
        after_skill_map,
    )
    anomalies = _after_anomalies(eligible)
    anomalies["effort_jump_two_or_more_buckets"] = comparison[
        "effort_jump_two_or_more_buckets"
    ]
    anomalies["skill_minimum_level_changes"] = comparison[
        "skill_minimum_level_changes"
    ]

    return {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "generated_at": generated_at or _utc_now(),
        "databases": {
            "baseline": (
                str(Path(baseline_database_path).expanduser().resolve())
                if baseline_database_path
                else None
            ),
            "after": (
                str(Path(after_database_path).expanduser().resolve())
                if after_database_path
                else None
            ),
            "access_mode": "sqlite_uri_mode_ro_and_pragma_query_only",
        },
        "scope": {
            "active_repositories_only": True,
            "primary_scope": "candidate_eligibility=eligible",
            "additional_scopes": ["all_candidates", "newcomer_eligible"],
            "body_text_in_output": False,
        },
        "record_counts": {
            "baseline_all_active_candidates": len(baseline),
            "after_all_active_candidates": len(after),
            "after_eligible_candidates": len(eligible),
            "after_newcomer_eligible_candidates": len(newcomer_eligible),
        },
        "after": {
            "summaries": {
                "all_candidates": _summary(after),
                "eligible": _summary(eligible),
                "newcomer_eligible": _summary(newcomer_eligible),
            },
            "eligible_analysis": {
                "by_task_type": grouped_by_task_type,
                "by_language": grouped_by_language,
                "by_repository": grouped_by_repository,
                "comparative_groups": comparison_groups,
                "rule_trigger_queues": _rule_trigger_queues(eligible),
                "difficulty_v02_checks": _difficulty_v02_checks(eligible),
                "anomalies": dict(sorted(anomalies.items())),
            },
        },
        "baseline_to_after": comparison,
    }


def write_diagnostics_report(
    report: Mapping[str, Any], output_path: str | Path
) -> Path:
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export read-only difficulty and effort diagnostics for OSS-Mentor."
        )
    )
    parser.add_argument(
        "--baseline-database",
        "--baseline-db",
        dest="baseline_database",
        default=str(DEFAULT_BASELINE_DATABASE),
        help=f"Baseline SQLite path (default: {DEFAULT_BASELINE_DATABASE}).",
    )
    parser.add_argument(
        "--after-database",
        "--after-db",
        dest="after_database",
        default=str(DEFAULT_AFTER_DATABASE),
        help=f"After SQLite path (default: {DEFAULT_AFTER_DATABASE}).",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help=f"JSON output path (default: {DEFAULT_OUTPUT_PATH}).",
    )
    parser.add_argument(
        "--generated-at",
        default=None,
        help=(
            "Optional fixed ISO timestamp for deterministic test runs. "
            "Normal CLI use should omit it."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    baseline_snapshot = load_database_snapshot(args.baseline_database)
    after_snapshot = load_database_snapshot(args.after_database)

    report = build_difficulty_diagnostics(
        after_snapshot["records"],
        baseline_records=baseline_snapshot["records"],
        after_skills=after_snapshot["skills"],
        baseline_skills=baseline_snapshot["skills"],
        after_database_path=after_snapshot["database_path"],
        baseline_database_path=baseline_snapshot["database_path"],
        generated_at=args.generated_at,
    )
    output_path = write_diagnostics_report(report, args.output)

    eligible = report["after"]["summaries"]["eligible"]
    comparison = report["baseline_to_after"]
    print(
        json.dumps(
            {
                "event": "difficulty_diagnostics_exported",
                "output_path": str(output_path),
                "baseline_database": baseline_snapshot["database_path"],
                "after_database": after_snapshot["database_path"],
                "eligible_count": eligible["record_count"],
                "legacy_effort_difference_count": eligible[
                    "legacy_effort_comparison"
                ]["different_from_legacy_count"],
                "task_type_change_count": comparison["task_type_changes"][
                    "count"
                ],
                "difficulty_change_count": comparison["difficulty_changes"][
                    "count"
                ],
                "effort_change_count": comparison["effort_changes"]["count"],
                "skill_minimum_level_change_count": comparison[
                    "skill_minimum_level_changes"
                ]["count"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())