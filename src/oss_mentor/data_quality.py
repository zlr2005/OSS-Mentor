"""Pure data-quality analysis for task candidates and extracted task features.

This module deliberately does not access SQLite.  Callers pass ordinary mapping
records, where each task record may contain a ``requirements`` list populated by
the persistence layer.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from oss_mentor.developer_profiles import ALLOWED_TASK_TYPES


REPORT_SCHEMA_VERSION = "data_quality_report_v0.2"
PRIMARY_SCOPE = "eligible_candidates"
PUBLIC_TASK_TYPES = frozenset(ALLOWED_TASK_TYPES)
VALID_EFFORT_BUCKETS = frozenset({"under_2h", "half_day", "one_day", "multi_day"})
VALID_PLATFORM_SKILLS = frozenset(
    {"platform:windows", "platform:linux", "platform:macos"}
)
PLAIN_PLATFORM_SKILLS = frozenset({"windows", "linux", "macos"})
DIFFICULTY_FIELDS = (
    "estimated_code_difficulty",
    "estimated_setup_difficulty",
    "estimated_project_context_difficulty",
    "estimated_collaboration_difficulty",
)
SCORE_RANGES = {
    "text_clarity_score": (0.0, 100.0),
    "novice_fit_probability": (0.0, 1.0),
    "newcomer_score": (0.0, 100.0),
    "growth_value_score": (0.0, 100.0),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _is_truthy_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return False


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def _missing_metric(missing_count: int, total_count: int) -> dict[str, Any]:
    return {
        "missing_count": missing_count,
        "total_count": total_count,
        "missing_rate": _rate(missing_count, total_count),
    }


def _coverage_metric(covered_count: int, total_count: int) -> dict[str, Any]:
    return {
        "covered_count": covered_count,
        "total_count": total_count,
        "coverage_rate": _rate(covered_count, total_count),
    }


def _safe_json(value: Any) -> tuple[Any, str]:
    """Return ``(parsed_value, status)`` where status is ok/missing/invalid."""

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


def _parse_string_list(value: Any) -> tuple[tuple[str, ...], str]:
    parsed, status = _safe_json(value)
    if status != "ok":
        return (), status
    if not isinstance(parsed, (list, tuple)):
        return (), "invalid"
    if any(not isinstance(item, str) for item in parsed):
        return (), "invalid"
    cleaned = tuple(item.strip() for item in parsed if item.strip())
    return (cleaned, "ok") if cleaned else ((), "missing")


def _parse_object(value: Any) -> tuple[dict[str, Any] | None, str]:
    parsed, status = _safe_json(value)
    if status != "ok":
        return None, status
    if not isinstance(parsed, dict):
        return None, "invalid"
    if not parsed:
        return None, "missing"
    return parsed, "ok"


def _task_types(record: Mapping[str, Any]) -> tuple[tuple[str, ...], str]:
    raw = (
        record.get("task_types_json")
        if "task_types_json" in record
        else record.get("task_types")
    )
    values, status = _parse_string_list(raw)
    return tuple(value.casefold() for value in values), status


def _requirements(record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = record.get("requirements", record.get("skill_requirements", []))
    if not isinstance(raw, (list, tuple)):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _valid_requirement(requirement: Mapping[str, Any]) -> bool:
    skill_name = requirement.get("skill_name")
    level = requirement.get("minimum_level")
    importance = requirement.get("importance")
    source = requirement.get("requirement_source")
    version = requirement.get("feature_version")
    return (
        isinstance(skill_name, str)
        and bool(skill_name.strip())
        and isinstance(level, int)
        and not isinstance(level, bool)
        and 0 <= level <= 4
        and isinstance(importance, (int, float))
        and not isinstance(importance, bool)
        and 0 < float(importance) <= 1
        and isinstance(source, str)
        and bool(source.strip())
        and isinstance(version, str)
        and bool(version.strip())
    )


def _is_valid_integer(value: Any, lower: int, upper: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and lower <= value <= upper


def _is_valid_number(value: Any, lower: float, upper: float) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and lower <= float(value) <= upper
    )


def _is_active(record: Mapping[str, Any]) -> bool:
    return (
        not _is_truthy_flag(record.get("is_archived"))
        and not _is_truthy_flag(record.get("is_disabled"))
        and str(record.get("maintenance_status") or "active") != "inactive"
    )


def _is_eligible(record: Mapping[str, Any]) -> bool:
    return _is_active(record) and record.get("candidate_eligibility") == "eligible"


def _scope_records(records: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    active = [record for record in records if _is_active(record)]
    eligible = [record for record in active if record.get("candidate_eligibility") == "eligible"]
    newcomer = [
        record for record in eligible if _is_truthy_flag(record.get("newcomer_label_signal"))
    ]
    return {
        "all_candidates": list(records),
        "active_candidates": active,
        "eligible_candidates": eligible,
        "newcomer_eligible_candidates": newcomer,
    }


def _input_completeness(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(records)
    body_missing = sum(_is_blank(record.get("body_text")) for record in records)
    language_missing = sum(
        _is_blank(record.get("primary_language"))
        or str(record.get("primary_language") or "").strip().casefold() == "unknown"
        for record in records
    )
    github_missing = sum(_is_blank(record.get("github_verified_at")) for record in records)
    title_missing = sum(_is_blank(record.get("title")) for record in records)
    labels_missing = 0
    labels_invalid = 0
    for record in records:
        _, status = _parse_string_list(record.get("labels_json", record.get("labels")))
        if status != "ok":
            labels_missing += 1
        if status == "invalid":
            labels_invalid += 1
    last_activity_missing = sum(_is_blank(record.get("last_activity_at")) for record in records)
    linked_pr_unknown = sum(record.get("has_linked_open_pr") is None for record in records)
    return {
        "body_text": _missing_metric(body_missing, total),
        "primary_language": _missing_metric(language_missing, total),
        "github_verification": _missing_metric(github_missing, total),
        "title": _missing_metric(title_missing, total),
        "labels": {
            **_missing_metric(labels_missing, total),
            "invalid_count": labels_invalid,
        },
        "last_activity": _missing_metric(last_activity_missing, total),
        "linked_pr_verification": _missing_metric(linked_pr_unknown, total),
    }


def _task_type_quality(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(records)
    recognized = 0
    field_missing = 0
    invalid_field = 0
    other_only = 0
    unsupported_only = 0
    mixed_supported_unsupported = 0

    for record in records:
        values, status = _task_types(record)
        if status != "ok":
            field_missing += 1
        if status == "invalid":
            invalid_field += 1
        value_set = set(values)
        supported = value_set.intersection(PUBLIC_TASK_TYPES)
        unsupported = value_set.difference(PUBLIC_TASK_TYPES)
        if supported:
            recognized += 1
        if value_set == {"other"}:
            other_only += 1
        if value_set and not supported:
            unsupported_only += 1
        if supported and unsupported:
            mixed_supported_unsupported += 1

    coverage = _coverage_metric(recognized, total)
    return {
        "recognized_count": recognized,
        "total_count": total,
        "coverage_rate": coverage["coverage_rate"],
        "target": 0.9,
        "passed": coverage["coverage_rate"] is not None
        and coverage["coverage_rate"] >= 0.9,
        "field_missing_count": field_missing,
        "field_missing_rate": _rate(field_missing, total),
        "invalid_field_count": invalid_field,
        "other_only_count": other_only,
        "unsupported_only_count": unsupported_only,
        "mixed_supported_unsupported_count": mixed_supported_unsupported,
    }


def _skill_requirement_quality(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(records)
    covered = 0
    invalid_requirement_tasks = 0
    only_repository_language = 0
    platform_requirement_tasks = 0
    invalid_platform_tasks = 0
    plain_platform_tasks = 0
    version_mismatch_tasks = 0

    for record in records:
        requirements = _requirements(record)
        valid = [requirement for requirement in requirements if _valid_requirement(requirement)]
        if valid:
            covered += 1
        if requirements and len(valid) != len(requirements):
            invalid_requirement_tasks += 1
        if valid and all(
            str(requirement.get("requirement_source") or "").strip()
            == "repository_primary_language"
            for requirement in valid
        ):
            only_repository_language += 1

        names = {
            str(requirement.get("skill_name") or "").strip().casefold()
            for requirement in requirements
            if not _is_blank(requirement.get("skill_name"))
        }
        platform_names = {name for name in names if name.startswith("platform:")}
        if platform_names:
            platform_requirement_tasks += 1
        if platform_names.difference(VALID_PLATFORM_SKILLS):
            invalid_platform_tasks += 1
        if names.intersection(PLAIN_PLATFORM_SKILLS):
            plain_platform_tasks += 1

        task_version = str(record.get("task_feature_version") or "").strip()
        requirement_versions = {
            str(requirement.get("feature_version") or "").strip()
            for requirement in requirements
            if not _is_blank(requirement.get("feature_version"))
        }
        if task_version and any(version != task_version for version in requirement_versions):
            version_mismatch_tasks += 1

    coverage = _coverage_metric(covered, total)
    return {
        "covered_count": covered,
        "total_count": total,
        "coverage_rate": coverage["coverage_rate"],
        "target": 0.9,
        "passed": coverage["coverage_rate"] is not None
        and coverage["coverage_rate"] >= 0.9,
        "missing_count": total - covered,
        "missing_rate": _rate(total - covered, total),
        "invalid_requirement_task_count": invalid_requirement_tasks,
        "only_repository_language_count": only_repository_language,
        "platform_requirement_task_count": platform_requirement_tasks,
        "invalid_platform_requirement_count": invalid_platform_tasks,
        "plain_platform_skill_count": plain_platform_tasks,
        "feature_version_mismatch_count": version_mismatch_tasks,
    }


def _difficulty_quality(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(records)
    by_field: dict[str, dict[str, Any]] = {}
    task_missing = 0
    task_invalid = 0
    task_complete = 0
    task_valid = 0

    all_fields = (*DIFFICULTY_FIELDS, "estimated_effort_bucket")
    for field in all_fields:
        missing = 0
        invalid = 0
        for record in records:
            value = record.get(field)
            if _is_blank(value):
                missing += 1
                continue
            if field == "estimated_effort_bucket":
                valid = isinstance(value, str) and value.strip() in VALID_EFFORT_BUCKETS
            else:
                valid = _is_valid_integer(value, 0, 3)
            if not valid:
                invalid += 1
        by_field[field] = {
            "missing_count": missing,
            "invalid_count": invalid,
            "valid_count": total - missing - invalid,
            "total_count": total,
            "missing_rate": _rate(missing, total),
            "invalid_rate": _rate(invalid, total),
        }

    for record in records:
        missing = any(_is_blank(record.get(field)) for field in all_fields)
        invalid = False
        for field in DIFFICULTY_FIELDS:
            value = record.get(field)
            if not _is_blank(value) and not _is_valid_integer(value, 0, 3):
                invalid = True
        effort = record.get("estimated_effort_bucket")
        if not _is_blank(effort) and not (
            isinstance(effort, str) and effort.strip() in VALID_EFFORT_BUCKETS
        ):
            invalid = True
        if missing:
            task_missing += 1
        if invalid:
            task_invalid += 1
        if not missing:
            task_complete += 1
        if not missing and not invalid:
            task_valid += 1

    return {
        "complete_count": task_complete,
        "valid_count": task_valid,
        "missing_count": task_missing,
        "invalid_count": task_invalid,
        "total_count": total,
        "complete_rate": _rate(task_complete, total),
        "valid_rate": _rate(task_valid, total),
        "missing_rate": _rate(task_missing, total),
        "invalid_rate": _rate(task_invalid, total),
        "by_field": by_field,
    }


def _feature_quality(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(records)
    extraction_missing = 0
    evidence_missing = 0
    score_invalid_tasks = 0
    score_by_field: dict[str, dict[str, Any]] = {}

    for record in records:
        if _is_blank(record.get("task_feature_version")) or _is_blank(
            record.get("feature_extracted_at")
        ):
            extraction_missing += 1
        _, evidence_status = _parse_object(record.get("feature_evidence_json"))
        if evidence_status != "ok":
            evidence_missing += 1

        has_invalid_score = False
        for field, (lower, upper) in SCORE_RANGES.items():
            value = record.get(field)
            if not _is_blank(value) and not _is_valid_number(value, lower, upper):
                has_invalid_score = True
        if has_invalid_score:
            score_invalid_tasks += 1

    for field, (lower, upper) in SCORE_RANGES.items():
        missing = 0
        invalid = 0
        for record in records:
            value = record.get(field)
            if _is_blank(value):
                missing += 1
            elif not _is_valid_number(value, lower, upper):
                invalid += 1
        score_by_field[field] = {
            "missing_count": missing,
            "invalid_count": invalid,
            "valid_count": total - missing - invalid,
            "total_count": total,
            "missing_rate": _rate(missing, total),
            "invalid_rate": _rate(invalid, total),
        }

    return {
        "feature_extraction_missing_count": extraction_missing,
        "feature_extraction_missing_rate": _rate(extraction_missing, total),
        "feature_evidence_missing_count": evidence_missing,
        "feature_evidence_missing_rate": _rate(evidence_missing, total),
        "feature_score_invalid_count": score_invalid_tasks,
        "feature_score_invalid_rate": _rate(score_invalid_tasks, total),
        "score_fields": score_by_field,
    }


def _build_scope_quality(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "input_completeness": _input_completeness(records),
        "task_type_quality": _task_type_quality(records),
        "skill_requirement_quality": _skill_requirement_quality(records),
        "difficulty_quality": _difficulty_quality(records),
        "feature_quality": _feature_quality(records),
    }


def _distributions(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    task_type_values: Counter[str] = Counter()
    unsupported_values: Counter[str] = Counter()
    task_versions: Counter[str] = Counter()
    requirement_sources: Counter[str] = Counter()
    skill_versions: Counter[str] = Counter()
    effort_buckets: Counter[str] = Counter()

    for record in records:
        values, _ = _task_types(record)
        task_type_values.update(values)
        unsupported_values.update(value for value in values if value not in PUBLIC_TASK_TYPES)
        task_version = str(record.get("task_feature_version") or "").strip() or "missing"
        task_versions[task_version] += 1
        effort = str(record.get("estimated_effort_bucket") or "").strip() or "missing"
        effort_buckets[effort] += 1
        for requirement in _requirements(record):
            source = str(requirement.get("requirement_source") or "").strip() or "missing"
            version = str(requirement.get("feature_version") or "").strip() or "missing"
            requirement_sources[source] += 1
            skill_versions[version] += 1

    def sorted_dict(counter: Counter[str]) -> dict[str, int]:
        return {key: counter[key] for key in sorted(counter)}

    return {
        "task_type_values": sorted_dict(task_type_values),
        "unsupported_task_type_values": sorted_dict(unsupported_values),
        "task_feature_versions": sorted_dict(task_versions),
        "skill_requirement_sources": sorted_dict(requirement_sources),
        "skill_feature_versions": sorted_dict(skill_versions),
        "effort_buckets": sorted_dict(effort_buckets),
    }


def _group_summary(records: Sequence[Mapping[str, Any]], *, key: str, label: str) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        raw_value = record.get(key)
        value = str(raw_value or "").strip() or "unknown"
        groups[value].append(record)

    rows: list[dict[str, Any]] = []
    for value, group in groups.items():
        total = len(group)
        body_missing = sum(_is_blank(record.get("body_text")) for record in group)
        recognized = sum(bool(set(_task_types(record)[0]).intersection(PUBLIC_TASK_TYPES)) for record in group)
        skill_covered = sum(
            any(_valid_requirement(item) for item in _requirements(record)) for record in group
        )
        invalid_difficulty = _difficulty_quality(group)["invalid_count"]
        row = {
            label: value,
            "eligible_count": total,
            "body_text_missing_count": body_missing,
            "recognized_task_type_count": recognized,
            "task_type_coverage_rate": _rate(recognized, total),
            "skill_requirement_covered_count": skill_covered,
            "skill_requirement_coverage_rate": _rate(skill_covered, total),
            "invalid_difficulty_count": invalid_difficulty,
        }
        if label == "repository":
            languages = {
                str(record.get("primary_language") or "").strip() or "unknown"
                for record in group
            }
            row["primary_language"] = sorted(languages)[0] if languages else "unknown"
        rows.append(row)

    if label == "repository":
        rows.sort(
            key=lambda row: (
                2.0 if row["task_type_coverage_rate"] is None else row["task_type_coverage_rate"],
                -row["eligible_count"],
                str(row[label]).casefold(),
            )
        )
    else:
        rows.sort(key=lambda row: str(row[label]).casefold())
    return rows


def _record_sort_key(record: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(record.get("repository") or "").casefold(),
        str(record.get("issue_number") or ""),
        str(record.get("task_candidate_id") or ""),
    )


def _sample(record: Mapping[str, Any], reason: str, **extra: Any) -> dict[str, Any]:
    sample = {
        "task_candidate_id": record.get("task_candidate_id"),
        "repository": record.get("repository"),
        "issue_number": record.get("issue_number"),
        "title": record.get("title"),
        "html_url": record.get("html_url"),
        "reason": reason,
    }
    sample.update(extra)
    return sample


def _anomalies(records: Sequence[Mapping[str, Any]], sample_limit: int) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = {
        "body_text_missing_samples": [],
        "unrecognized_task_type_samples": [],
        "skill_requirement_missing_samples": [],
        "invalid_difficulty_samples": [],
        "invalid_platform_requirement_samples": [],
        "feature_version_mismatch_samples": [],
    }

    for record in sorted(records, key=_record_sort_key):
        values, status = _task_types(record)
        value_set = set(values)
        requirements = _requirements(record)
        valid_requirements = [item for item in requirements if _valid_requirement(item)]
        difficulty = _difficulty_quality([record])
        names = {
            str(item.get("skill_name") or "").strip().casefold()
            for item in requirements
            if not _is_blank(item.get("skill_name"))
        }
        invalid_platforms = sorted(
            name for name in names if name.startswith("platform:") and name not in VALID_PLATFORM_SKILLS
        )
        task_version = str(record.get("task_feature_version") or "").strip()
        requirement_versions = {
            str(item.get("feature_version") or "").strip()
            for item in requirements
            if not _is_blank(item.get("feature_version"))
        }

        if _is_blank(record.get("body_text")):
            buckets["body_text_missing_samples"].append(_sample(record, "body_text_missing"))
        if not value_set.intersection(PUBLIC_TASK_TYPES):
            reason = "task_type_field_missing" if status != "ok" else "unsupported_only"
            if value_set == {"other"}:
                reason = "only_other"
            buckets["unrecognized_task_type_samples"].append(
                _sample(record, reason, task_types=list(values))
            )
        if not valid_requirements:
            buckets["skill_requirement_missing_samples"].append(
                _sample(record, "no_valid_skill_requirement")
            )
        if difficulty["missing_count"] or difficulty["invalid_count"]:
            buckets["invalid_difficulty_samples"].append(
                _sample(
                    record,
                    "difficulty_missing_or_invalid",
                    missing=difficulty["missing_count"] > 0,
                    invalid=difficulty["invalid_count"] > 0,
                )
            )
        if invalid_platforms or names.intersection(PLAIN_PLATFORM_SKILLS):
            buckets["invalid_platform_requirement_samples"].append(
                _sample(
                    record,
                    "invalid_platform_requirement",
                    invalid_platforms=invalid_platforms,
                    plain_platforms=sorted(names.intersection(PLAIN_PLATFORM_SKILLS)),
                )
            )
        if task_version and any(version != task_version for version in requirement_versions):
            buckets["feature_version_mismatch_samples"].append(
                _sample(
                    record,
                    "feature_version_mismatch",
                    task_feature_version=task_version,
                    skill_feature_versions=sorted(requirement_versions),
                )
            )

    return {name: samples[:sample_limit] for name, samples in buckets.items()}


def build_data_quality_report(
    records: Iterable[Mapping[str, Any]],
    *,
    generated_at: str | None = None,
    sample_limit: int = 10,
) -> dict[str, Any]:
    """Build a JSON-serializable data-quality report from ordinary task records.

    Each record may contain a ``requirements`` list.  No database or network
    access occurs in this function.
    """

    if isinstance(sample_limit, bool) or not isinstance(sample_limit, int) or sample_limit < 0:
        raise ValueError("sample_limit must be a non-negative integer")

    normalized: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping):
            raise TypeError("each data-quality record must be a mapping")
        normalized.append(dict(record))

    scopes = _scope_records(normalized)
    quality_by_scope = {
        name: _build_scope_quality(scope_records) for name, scope_records in scopes.items()
    }
    primary_records = scopes[PRIMARY_SCOPE]
    primary_quality = quality_by_scope[PRIMARY_SCOPE]
    task_type_passed = primary_quality["task_type_quality"]["passed"]
    skill_passed = primary_quality["skill_requirement_quality"]["passed"]
    difficulty_rate = primary_quality["difficulty_quality"]["valid_rate"]
    difficulty_passed = difficulty_rate is not None and difficulty_rate == 1.0

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": generated_at or _utc_now(),
        "policy": {
            "primary_scope": PRIMARY_SCOPE,
            "public_task_types": sorted(PUBLIC_TASK_TYPES),
            "task_type_coverage_target": 0.9,
            "skill_requirement_coverage_target": 0.9,
            "difficulty_range": [0, 3],
            "valid_effort_buckets": sorted(VALID_EFFORT_BUCKETS),
            "sample_limit": sample_limit,
        },
        "scope_summary": {
            name: {"total_count": len(scope_records)}
            for name, scope_records in scopes.items()
        },
        "quality_by_scope": quality_by_scope,
        "distributions": _distributions(primary_records),
        "by_repository": _group_summary(
            primary_records, key="repository", label="repository"
        ),
        "by_language": _group_summary(
            primary_records, key="primary_language", label="language"
        ),
        "anomalies": _anomalies(primary_records, sample_limit),
        "acceptance_summary": {
            "scope": PRIMARY_SCOPE,
            "task_type_coverage_passed": task_type_passed,
            "skill_requirement_coverage_passed": skill_passed,
            "difficulty_values_passed": difficulty_passed,
            "overall_passed": task_type_passed and skill_passed and difficulty_passed,
        },
    }


def _format_rate(value: Any) -> str:
    if value is None:
        return "N/A"
    return f"{float(value) * 100:.2f}%"


def _md_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def _sample_markdown(samples: Sequence[Mapping[str, Any]]) -> list[str]:
    if not samples:
        return ["暂无。"]
    lines = ["| 仓库 | Issue | 标题 | 原因 |", "|---|---:|---|---|"]
    for sample in samples:
        repository = _md_cell(sample.get("repository") or "unknown")
        issue_number = _md_cell(sample.get("issue_number") or "")
        title = _md_cell(sample.get("title") or "")
        reason = _md_cell(sample.get("reason") or "")
        url = str(sample.get("html_url") or "").strip()
        issue = f"[#{issue_number}]({url})" if url else f"#{issue_number}"
        lines.append(f"| {repository} | {issue} | {title} | {reason} |")
    return lines


def render_data_quality_markdown(report: Mapping[str, Any]) -> str:
    """Render a report produced by :func:`build_data_quality_report` as Markdown."""

    scopes = report["scope_summary"]
    primary = report["quality_by_scope"][PRIMARY_SCOPE]
    acceptance = report["acceptance_summary"]
    input_quality = primary["input_completeness"]
    task_quality = primary["task_type_quality"]
    skill_quality = primary["skill_requirement_quality"]
    difficulty_quality = primary["difficulty_quality"]

    lines = [
        "# OSS-Mentor 数据质量报告 v0.2",
        "",
        f"- 生成时间：{report.get('generated_at', '')}",
        f"- 主要统计范围：`{PRIMARY_SCOPE}`",
        f"- 报告结构版本：`{report.get('schema_version', '')}`",
        "",
        "## 1. 执行摘要",
        "",
        f"- 任务类型覆盖率：{_format_rate(task_quality['coverage_rate'])}（{'通过' if acceptance['task_type_coverage_passed'] else '未通过'} 90% 目标）",
        f"- 技能要求覆盖率：{_format_rate(skill_quality['coverage_rate'])}（{'通过' if acceptance['skill_requirement_coverage_passed'] else '未通过'} 90% 目标）",
        f"- 难度合法率：{_format_rate(difficulty_quality['valid_rate'])}",
        f"- 总体验收：{'通过' if acceptance['overall_passed'] else '未通过'}",
        "",
        "## 2. 数据范围",
        "",
        "| 范围 | 任务数 |",
        "|---|---:|",
        f"| 全部候选 | {scopes['all_candidates']['total_count']} |",
        f"| 活跃仓库候选 | {scopes['active_candidates']['total_count']} |",
        f"| 当前可推荐 | {scopes['eligible_candidates']['total_count']} |",
        f"| 新人友好可推荐 | {scopes['newcomer_eligible_candidates']['total_count']} |",
        "",
        "## 3. 核心缺失率",
        "",
        "| 指标 | 缺失 | 总数 | 缺失率 |",
        "|---|---:|---:|---:|",
    ]

    for label, metric in (
        ("正文", input_quality["body_text"]),
        ("主要语言", input_quality["primary_language"]),
        ("GitHub 验证", input_quality["github_verification"]),
    ):
        lines.append(
            f"| {label} | {metric['missing_count']} | {metric['total_count']} | {_format_rate(metric['missing_rate'])} |"
        )
    lines.extend(
        [
            f"| 有效任务类型 | {task_quality['total_count'] - task_quality['recognized_count']} | {task_quality['total_count']} | {_format_rate(None if task_quality['coverage_rate'] is None else 1 - task_quality['coverage_rate'])} |",
            f"| 技能要求 | {skill_quality['missing_count']} | {skill_quality['total_count']} | {_format_rate(skill_quality['missing_rate'])} |",
            "",
            "## 4. 任务类型质量",
            "",
            f"公共任务类型：{', '.join(f'`{value}`' for value in report['policy']['public_task_types'])}。",
            "",
            f"- 有效识别：{task_quality['recognized_count']} / {task_quality['total_count']}（{_format_rate(task_quality['coverage_rate'])}）",
            f"- 仅 `other`：{task_quality['other_only_count']}",
            f"- 仅非公共类型：{task_quality['unsupported_only_count']}",
            f"- 公共与非公共类型混合：{task_quality['mixed_supported_unsupported_count']}",
            f"- 字段缺失或不可解析：{task_quality['field_missing_count']}",
            "",
            "### 类型分布",
            "",
            "| 类型 | 任务数 |",
            "|---|---:|",
        ]
    )
    for value, count in report["distributions"]["task_type_values"].items():
        lines.append(f"| `{_md_cell(value)}` | {count} |")

    lines.extend(
        [
            "",
            "## 5. 技能要求质量",
            "",
            f"- 覆盖任务：{skill_quality['covered_count']} / {skill_quality['total_count']}（{_format_rate(skill_quality['coverage_rate'])}）",
            f"- 只有仓库主要语言要求：{skill_quality['only_repository_language_count']}",
            f"- 含平台要求的任务：{skill_quality['platform_requirement_task_count']}",
            f"- 非法平台要求任务：{skill_quality['invalid_platform_requirement_count']}",
            f"- 使用普通平台技能名的任务：{skill_quality['plain_platform_skill_count']}",
            f"- 特征版本不一致任务：{skill_quality['feature_version_mismatch_count']}",
            "",
            "### 技能来源分布",
            "",
            "| 来源 | 记录数 |",
            "|---|---:|",
        ]
    )
    for source, count in report["distributions"]["skill_requirement_sources"].items():
        lines.append(f"| `{_md_cell(source)}` | {count} |")

    lines.extend(
        [
            "",
            "## 6. 难度质量",
            "",
            f"- 完整任务：{difficulty_quality['complete_count']} / {difficulty_quality['total_count']}（{_format_rate(difficulty_quality['complete_rate'])}）",
            f"- 合法任务：{difficulty_quality['valid_count']} / {difficulty_quality['total_count']}（{_format_rate(difficulty_quality['valid_rate'])}）",
            f"- 存在缺失：{difficulty_quality['missing_count']}",
            f"- 存在非法值：{difficulty_quality['invalid_count']}",
            "",
            "| 字段 | 缺失 | 非法 | 合法 |",
            "|---|---:|---:|---:|",
        ]
    )
    for field, metric in difficulty_quality["by_field"].items():
        lines.append(
            f"| `{_md_cell(field)}` | {metric['missing_count']} | {metric['invalid_count']} | {metric['valid_count']} |"
        )

    lines.extend(
        [
            "",
            "## 7. 按仓库分析（问题优先，前 10 项）",
            "",
            "| 仓库 | 语言 | 可推荐任务 | 类型覆盖率 | 技能覆盖率 | 正文缺失 | 难度异常 |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report["by_repository"][:10]:
        lines.append(
            f"| {_md_cell(row['repository'])} | {_md_cell(row['primary_language'])} | {row['eligible_count']} | {_format_rate(row['task_type_coverage_rate'])} | {_format_rate(row['skill_requirement_coverage_rate'])} | {row['body_text_missing_count']} | {row['invalid_difficulty_count']} |"
        )

    lines.extend(
        [
            "",
            "## 8. 按语言分析",
            "",
            "| 语言 | 可推荐任务 | 类型覆盖率 | 技能覆盖率 | 正文缺失 | 难度异常 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report["by_language"]:
        lines.append(
            f"| {_md_cell(row['language'])} | {row['eligible_count']} | {_format_rate(row['task_type_coverage_rate'])} | {_format_rate(row['skill_requirement_coverage_rate'])} | {row['body_text_missing_count']} | {row['invalid_difficulty_count']} |"
        )

    lines.extend(["", "## 9. 异常任务样例", "", "### 正文缺失", ""])
    lines.extend(_sample_markdown(report["anomalies"]["body_text_missing_samples"]))
    lines.extend(["", "### 未识别到公共任务类型", ""])
    lines.extend(_sample_markdown(report["anomalies"]["unrecognized_task_type_samples"]))
    lines.extend(["", "### 无有效技能要求", ""])
    lines.extend(_sample_markdown(report["anomalies"]["skill_requirement_missing_samples"]))
    lines.extend(["", "### 难度缺失或非法", ""])
    lines.extend(_sample_markdown(report["anomalies"]["invalid_difficulty_samples"]))

    lines.extend(
        [
            "",
            "## 10. 结论与下一步",
            "",
            "- B2：优先分析未识别到公共任务类型的真实任务，并改进分类规则与证据链。",
            "- B3：根据难度缺失、非法值和真实误判样例调整难度规则。",
            "- B4：技能覆盖率只代表记录存在，还需继续检查技能词表、工具要求、置信度和证据。",
            "",
        ]
    )
    return "\n".join(lines)
