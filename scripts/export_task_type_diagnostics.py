"""Export read-only task-type diagnostics from the OSS-Mentor SQLite database.

This script is intentionally outside the production CLI. It reads the existing
candidate database, classifies task-type diagnostic cases, and writes a JSON
artifact under ``data/reports``. It never updates the database.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from oss_mentor.developer_profiles import ALLOWED_TASK_TYPES


DIAGNOSTIC_SCHEMA_VERSION = "task_type_diagnostics_v0.1"
DEFAULT_DATABASE_PATH = Path("data/oss_mentor.sqlite3")
DEFAULT_OUTPUT_PATH = Path("data/reports/task_type_diagnostics_v0.1.json")
PUBLIC_TASK_TYPES = frozenset(
    str(task_type).strip().casefold()
    for task_type in ALLOWED_TASK_TYPES
    if str(task_type).strip()
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_json(value: Any) -> tuple[Any, str]:
    """Return ``(parsed, status)`` where status is ok, missing, or invalid."""

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


def _parse_object(value: Any) -> tuple[dict[str, Any] | None, str]:
    parsed, status = _safe_json(value)
    if status != "ok":
        return None, status
    if not isinstance(parsed, dict):
        return None, "invalid"
    return parsed, "ok"


def _truthy_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return False


def _record_sort_key(record: Mapping[str, Any]) -> tuple[str, int, int]:
    repository = str(record.get("repository") or "").casefold()
    try:
        issue_number = int(record.get("issue_number") or 0)
    except (TypeError, ValueError):
        issue_number = 0
    try:
        task_candidate_id = int(record.get("task_candidate_id") or 0)
    except (TypeError, ValueError):
        task_candidate_id = 0
    return repository, issue_number, task_candidate_id


def _readonly_uri(database_path: Path) -> str:
    resolved = database_path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {resolved}")
    return f"{resolved.as_uri()}?mode=ro"


def load_eligible_records(database_path: str | Path) -> list[dict[str, Any]]:
    """Load active, eligible task candidates through a read-only SQLite URI."""

    path = Path(database_path)
    connection = sqlite3.connect(_readonly_uri(path), uri=True)
    connection.row_factory = sqlite3.Row

    try:
        connection.execute("PRAGMA query_only = ON")
        rows = connection.execute(
            """
            SELECT
                tc.task_candidate_id,
                r.full_name AS repository,
                r.primary_language,
                tc.issue_number,
                tc.html_url,
                tc.title,
                tc.body_text,
                tc.labels_json,
                tc.task_types_json,
                tc.newcomer_label_signal,
                tc.feature_evidence_json
            FROM task_candidate AS tc
            JOIN repository AS r
              ON r.repository_id = tc.repository_id
            WHERE COALESCE(r.is_archived, 0) = 0
              AND COALESCE(r.is_disabled, 0) = 0
              AND tc.candidate_eligibility = 'eligible'
            ORDER BY
                LOWER(r.full_name),
                tc.issue_number,
                tc.task_candidate_id
            """
        ).fetchall()
    finally:
        connection.close()

    return [dict(row) for row in rows]


def _normalized_record(
    raw_record: Mapping[str, Any],
    *,
    diagnostic_reason: str,
) -> dict[str, Any]:
    labels, labels_status = _parse_string_list(raw_record.get("labels_json"))
    task_types_original, task_types_status = _parse_string_list(
        raw_record.get("task_types_json")
    )
    task_types = sorted(
        {value.casefold() for value in task_types_original},
        key=str.casefold,
    )
    evidence, evidence_status = _parse_object(
        raw_record.get("feature_evidence_json")
    )

    return {
        "task_candidate_id": int(raw_record.get("task_candidate_id") or 0),
        "repository": str(raw_record.get("repository") or ""),
        "primary_language": str(raw_record.get("primary_language") or ""),
        "issue_number": int(raw_record.get("issue_number") or 0),
        "html_url": str(raw_record.get("html_url") or ""),
        "title": str(raw_record.get("title") or ""),
        "body_text": str(raw_record.get("body_text") or ""),
        "labels": labels,
        "labels_status": labels_status,
        "task_types": task_types,
        "task_types_status": task_types_status,
        "newcomer_label_signal": _truthy_flag(
            raw_record.get("newcomer_label_signal")
        ),
        "feature_evidence": evidence,
        "feature_evidence_status": evidence_status,
        "diagnostic_reason": diagnostic_reason,
    }


def _primary_unrecognized_reason(
    task_types: set[str],
    task_types_status: str,
) -> str | None:
    if task_types_status == "invalid":
        return "invalid_task_types_json"
    if task_types_status == "missing":
        return "missing_task_types"
    if task_types == {"other"}:
        return "only_other"
    if task_types and not task_types.intersection(PUBLIC_TASK_TYPES):
        return "unsupported_only"
    return None


def _empty_group(repository: str = "", language: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {
        "eligible_count": 0,
        "unrecognized_count": 0,
        "only_other_count": 0,
        "unsupported_only_count": 0,
        "unsupported_only_excluding_other_count": 0,
        "missing_task_types_count": 0,
        "invalid_task_types_json_count": 0,
        "mixed_supported_unsupported_count": 0,
        "performance_count": 0,
    }
    if repository:
        result["repository"] = repository
    if language:
        result["primary_language"] = language
    return result


def _increment_group(
    group: dict[str, Any],
    *,
    unrecognized_reason: str | None,
    unsupported_only: bool,
    mixed: bool,
    has_performance: bool,
) -> None:
    group["eligible_count"] += 1
    if unrecognized_reason is not None:
        group["unrecognized_count"] += 1
        if unrecognized_reason == "only_other":
            group["only_other_count"] += 1
        elif unrecognized_reason == "unsupported_only":
            group["unsupported_only_excluding_other_count"] += 1
        elif unrecognized_reason == "missing_task_types":
            group["missing_task_types_count"] += 1
        elif unrecognized_reason == "invalid_task_types_json":
            group["invalid_task_types_json_count"] += 1
    if unsupported_only:
        group["unsupported_only_count"] += 1
    if mixed:
        group["mixed_supported_unsupported_count"] += 1
    if has_performance:
        group["performance_count"] += 1


def build_task_type_diagnostics(
    records: Iterable[Mapping[str, Any]],
    *,
    database_path: str | Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic task-type diagnostic report from ordinary records."""

    raw_records: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping):
            raise TypeError("each diagnostic record must be a mapping")
        raw_records.append(dict(record))
    raw_records.sort(key=_record_sort_key)

    unrecognized_records: list[dict[str, Any]] = []
    mixed_records: list[dict[str, Any]] = []
    performance_records: list[dict[str, Any]] = []

    repository_groups: dict[str, dict[str, Any]] = {}
    language_groups: dict[str, dict[str, Any]] = {}
    current_type_counts: Counter[str] = Counter()

    only_other_count = 0
    unsupported_only_count = 0
    unsupported_only_excluding_other_count = 0
    missing_task_types_count = 0
    invalid_task_types_json_count = 0
    mixed_count = 0
    performance_count = 0

    for raw_record in raw_records:
        task_types_values, task_types_status = _parse_string_list(
            raw_record.get("task_types_json")
        )
        task_types = {value.casefold() for value in task_types_values}
        supported = task_types.intersection(PUBLIC_TASK_TYPES)
        unsupported = task_types.difference(PUBLIC_TASK_TYPES)

        unrecognized_reason = _primary_unrecognized_reason(
            task_types,
            task_types_status,
        )
        unsupported_only = bool(task_types) and not supported
        mixed = bool(supported and unsupported)
        has_performance = "performance" in task_types

        if task_types_status == "invalid":
            current_type_counts["<invalid>"] += 1
        elif task_types_status == "missing":
            current_type_counts["<missing>"] += 1
        else:
            for task_type in sorted(task_types):
                current_type_counts[task_type] += 1

        if unrecognized_reason is not None:
            unrecognized_records.append(
                _normalized_record(
                    raw_record,
                    diagnostic_reason=unrecognized_reason,
                )
            )
            if unrecognized_reason == "only_other":
                only_other_count += 1
            elif unrecognized_reason == "unsupported_only":
                unsupported_only_excluding_other_count += 1
            elif unrecognized_reason == "missing_task_types":
                missing_task_types_count += 1
            elif unrecognized_reason == "invalid_task_types_json":
                invalid_task_types_json_count += 1

        if unsupported_only:
            unsupported_only_count += 1

        if mixed:
            mixed_count += 1
            mixed_records.append(
                _normalized_record(
                    raw_record,
                    diagnostic_reason="mixed_supported_unsupported",
                )
            )

        if has_performance:
            performance_count += 1
            performance_records.append(
                _normalized_record(
                    raw_record,
                    diagnostic_reason="contains_performance",
                )
            )

        repository = str(raw_record.get("repository") or "<unknown>")
        language = str(
            raw_record.get("primary_language") or "<unknown>"
        ).strip() or "<unknown>"

        repository_group = repository_groups.setdefault(
            repository,
            _empty_group(repository=repository),
        )
        language_group = language_groups.setdefault(
            language,
            _empty_group(language=language),
        )
        for group in (repository_group, language_group):
            _increment_group(
                group,
                unrecognized_reason=unrecognized_reason,
                unsupported_only=unsupported_only,
                mixed=mixed,
                has_performance=has_performance,
            )

    for record_list in (
        unrecognized_records,
        mixed_records,
        performance_records,
    ):
        record_list.sort(key=_record_sort_key)

    by_repository = sorted(
        repository_groups.values(),
        key=lambda item: (
            -int(item["unrecognized_count"]),
            -int(item["eligible_count"]),
            str(item["repository"]).casefold(),
        ),
    )
    by_language = sorted(
        language_groups.values(),
        key=lambda item: str(item["primary_language"]).casefold(),
    )
    by_current_type = [
        {
            "task_type": task_type,
            "task_count": count,
            "is_public": task_type in PUBLIC_TASK_TYPES,
        }
        for task_type, count in sorted(
            current_type_counts.items(),
            key=lambda item: item[0].casefold(),
        )
    ]

    database_value = (
        str(Path(database_path).expanduser().resolve())
        if database_path is not None
        else None
    )

    return {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "generated_at": generated_at or _utc_now(),
        "database_path": database_value,
        "scope": {
            "repository_is_archived": False,
            "repository_is_disabled": False,
            "candidate_eligibility": "eligible",
            "eligible_task_count": len(raw_records),
        },
        "public_task_types": sorted(PUBLIC_TASK_TYPES),
        "summary": {
            "unrecognized_count": len(unrecognized_records),
            "only_other_count": only_other_count,
            # Inclusive count, matching data_quality.py semantics.
            "unsupported_only_count": unsupported_only_count,
            # Disjoint remainder after exact ["other"].
            "unsupported_only_excluding_other_count": (
                unsupported_only_excluding_other_count
            ),
            "missing_task_types_count": missing_task_types_count,
            "invalid_task_types_json_count": invalid_task_types_json_count,
            "mixed_supported_unsupported_count": mixed_count,
            "performance_count": performance_count,
        },
        "by_repository": by_repository,
        "by_language": by_language,
        "by_current_type": by_current_type,
        "records": {
            "unrecognized": unrecognized_records,
            "mixed_supported_unsupported": mixed_records,
            "performance": performance_records,
        },
    }


def write_diagnostics_report(
    report: Mapping[str, Any],
    output_path: str | Path,
) -> Path:
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export read-only diagnostics for OSS-Mentor task-type coverage."
        )
    )
    parser.add_argument(
        "--database",
        default=str(DEFAULT_DATABASE_PATH),
        help=(
            "SQLite database path "
            f"(default: {DEFAULT_DATABASE_PATH.as_posix()})."
        ),
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help=(
            "JSON output path "
            f"(default: {DEFAULT_OUTPUT_PATH.as_posix()})."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_path = Path(args.database).expanduser().resolve()
    records = load_eligible_records(database_path)
    report = build_task_type_diagnostics(
        records,
        database_path=database_path,
    )
    output_path = write_diagnostics_report(report, args.output)

    print(
        json.dumps(
            {
                "event": "task_type_diagnostics_exported",
                "database_path": str(database_path),
                "output_path": str(output_path),
                "public_task_types": report["public_task_types"],
                "scope": report["scope"],
                "summary": report["summary"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())