"""Read-only baseline-vs-B4 comparison for SkillRequirement v0.2 validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COMPARISON_SCHEMA_VERSION = "skill_requirement_comparison_v0.1"
DIFFICULTY_FIELDS = (
    "estimated_code_difficulty",
    "estimated_setup_difficulty",
    "estimated_project_context_difficulty",
    "estimated_collaboration_difficulty",
    "estimated_effort_bucket",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_sha256(path: str | Path) -> str:
    resolved = Path(path).expanduser().resolve()
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {path}")
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _json_list(raw: Any) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def _snapshot(path: Path) -> dict[str, Any]:
    before = file_sha256(path)
    connection = connect_readonly(path)
    try:
        candidates = [
            dict(row)
            for row in connection.execute(
                """
                SELECT
                    tc.task_candidate_id,
                    r.full_name AS repository,
                    COALESCE(r.primary_language, '') AS primary_language,
                    COALESCE(r.is_archived, 0) AS is_archived,
                    COALESCE(r.is_disabled, 0) AS is_disabled,
                    tc.issue_number,
                    tc.title,
                    tc.body_text,
                    tc.labels_json,
                    tc.state,
                    tc.assignment_state,
                    tc.is_locked,
                    tc.has_linked_open_pr,
                    tc.comment_count,
                    tc.candidate_eligibility,
                    tc.ineligibility_reasons_json,
                    tc.warnings_json,
                    COALESCE(tc.newcomer_label_signal, 0) AS newcomer_label_signal,
                    tc.task_types_json,
                    tc.estimated_code_difficulty,
                    tc.estimated_setup_difficulty,
                    tc.estimated_project_context_difficulty,
                    tc.estimated_collaboration_difficulty,
                    tc.estimated_effort_bucket,
                    COALESCE(tc.task_feature_version, '') AS task_feature_version
                FROM task_candidate AS tc
                JOIN repository AS r USING(repository_id)
                ORDER BY tc.task_candidate_id
                """
            ).fetchall()
        ]
        requirements = [
            dict(row)
            for row in connection.execute(
                """
                SELECT
                    task_candidate_id,
                    skill_name,
                    minimum_level,
                    importance,
                    requirement_source,
                    feature_version
                FROM task_skill_requirement
                ORDER BY task_candidate_id, LOWER(skill_name), skill_name
                """
            ).fetchall()
        ]
    finally:
        connection.close()
    after = file_sha256(path)
    if before != after:
        raise RuntimeError(f"database changed during read-only comparison: {path}")
    for row in candidates:
        row["is_archived"] = bool(row["is_archived"])
        row["is_disabled"] = bool(row["is_disabled"])
        row["newcomer_label_signal"] = bool(row["newcomer_label_signal"])
        row["task_types"] = _json_list(row["task_types_json"])
    return {
        "path": str(path),
        "sha256": before,
        "candidates": candidates,
        "requirements": requirements,
    }


def _active(row: dict[str, Any]) -> bool:
    return not row["is_archived"] and not row["is_disabled"]


def _scope(snapshot: dict[str, Any]) -> dict[str, Any]:
    rows = snapshot["candidates"]
    active = [r for r in rows if _active(r)]
    eligible = [r for r in active if r["candidate_eligibility"] == "eligible"]
    newcomer = [r for r in eligible if r["newcomer_label_signal"]]
    return {
        "total_candidate_count": len(rows),
        "active_candidate_count": len(active),
        "eligible_candidate_count": len(eligible),
        "newcomer_eligible_count": len(newcomer),
    }


def _identity(row: dict[str, Any]) -> tuple[str, int]:
    return (str(row["repository"]).casefold(), int(row["issue_number"]))


def _requirement_key(row: dict[str, Any]) -> tuple[str, int, float, str]:
    return (
        str(row["skill_name"]),
        int(row["minimum_level"]),
        float(row["importance"]),
        str(row["requirement_source"]),
    )


def compare_databases(
    baseline_path: str | Path,
    candidate_path: str | Path,
    *,
    expected_baseline_sha256: str,
) -> dict[str, Any]:
    baseline_path = Path(baseline_path).expanduser().resolve()
    candidate_path = Path(candidate_path).expanduser().resolve()
    expected = expected_baseline_sha256.strip().upper()
    if baseline_path == candidate_path:
        raise RuntimeError("baseline and candidate database paths must differ")

    baseline = _snapshot(baseline_path)
    candidate = _snapshot(candidate_path)
    if baseline["sha256"] != expected:
        raise RuntimeError(
            "STOP: baseline SHA256 mismatch during comparison: "
            f"expected {expected}, got {baseline['sha256']}"
        )

    base_by_identity = {_identity(row): row for row in baseline["candidates"]}
    cand_by_identity = {_identity(row): row for row in candidate["candidates"]}
    base_ids = set(base_by_identity)
    cand_ids = set(cand_by_identity)
    missing = sorted(base_ids - cand_ids)
    added = sorted(cand_ids - base_ids)

    eligibility_changes: list[dict[str, Any]] = []
    task_type_changes: list[dict[str, Any]] = []
    difficulty_changes: dict[str, list[dict[str, Any]]] = {field: [] for field in DIFFICULTY_FIELDS}
    primary_language_changes: list[dict[str, Any]] = []
    raw_input_changes: list[dict[str, Any]] = []
    raw_fields = (
        "title",
        "body_text",
        "labels_json",
        "state",
        "assignment_state",
        "is_locked",
        "has_linked_open_pr",
        "comment_count",
        "ineligibility_reasons_json",
        "warnings_json",
        "newcomer_label_signal",
    )

    for identity in sorted(base_ids & cand_ids):
        before = base_by_identity[identity]
        after = cand_by_identity[identity]
        common = {"repository": before["repository"], "issue_number": before["issue_number"]}
        if before["candidate_eligibility"] != after["candidate_eligibility"]:
            eligibility_changes.append({**common, "before": before["candidate_eligibility"], "after": after["candidate_eligibility"]})
        if before["task_types"] != after["task_types"]:
            task_type_changes.append({**common, "before": before["task_types"], "after": after["task_types"]})
        if before["primary_language"] != after["primary_language"]:
            primary_language_changes.append({**common, "before": before["primary_language"], "after": after["primary_language"]})
        changed_raw = [field for field in raw_fields if before[field] != after[field]]
        if changed_raw:
            raw_input_changes.append({**common, "fields": changed_raw})
        for field in DIFFICULTY_FIELDS:
            if before[field] != after[field]:
                difficulty_changes[field].append({**common, "before": before[field], "after": after[field]})

    base_taskid_to_identity = {
        int(row["task_candidate_id"]): _identity(row) for row in baseline["candidates"]
    }
    cand_taskid_to_identity = {
        int(row["task_candidate_id"]): _identity(row) for row in candidate["candidates"]
    }
    base_req: dict[tuple[str, int], list[tuple[str, int, float, str]]] = defaultdict(list)
    cand_req: dict[tuple[str, int], list[tuple[str, int, float, str]]] = defaultdict(list)
    for row in baseline["requirements"]:
        identity = base_taskid_to_identity.get(int(row["task_candidate_id"]))
        if identity is not None:
            base_req[identity].append(_requirement_key(row))
    for row in candidate["requirements"]:
        identity = cand_taskid_to_identity.get(int(row["task_candidate_id"]))
        if identity is not None:
            cand_req[identity].append(_requirement_key(row))

    requirement_changes: list[dict[str, Any]] = []
    added_requirement_counter: Counter[str] = Counter()
    removed_requirement_counter: Counter[str] = Counter()
    for identity in sorted(base_ids & cand_ids):
        before_set = set(base_req.get(identity, []))
        after_set = set(cand_req.get(identity, []))
        if before_set == after_set:
            continue
        added_rows = sorted(after_set - before_set, key=lambda value: value[0].casefold())
        removed_rows = sorted(before_set - after_set, key=lambda value: value[0].casefold())
        for row in added_rows:
            added_requirement_counter[row[0]] += 1
        for row in removed_rows:
            removed_requirement_counter[row[0]] += 1
        requirement_changes.append({
            "repository": identity[0],
            "issue_number": identity[1],
            "added": [
                {"skill_name": r[0], "minimum_level": r[1], "importance": r[2], "requirement_source": r[3]}
                for r in added_rows
            ],
            "removed": [
                {"skill_name": r[0], "minimum_level": r[1], "importance": r[2], "requirement_source": r[3]}
                for r in removed_rows
            ],
        })

    baseline_versions = Counter(str(r["task_feature_version"]) for r in baseline["candidates"])
    candidate_versions = Counter(str(r["task_feature_version"]) for r in candidate["candidates"])
    difficulty_change_counts = {field: len(rows) for field, rows in difficulty_changes.items()}

    hard_invariants = {
        "baseline_sha256_matches_expected": baseline["sha256"] == expected,
        "candidate_identity_set_unchanged": not missing and not added,
        "scope_counts_unchanged": _scope(baseline) == _scope(candidate),
        "candidate_eligibility_changes_zero": len(eligibility_changes) == 0,
        "raw_input_changes_zero": len(raw_input_changes) == 0,
        "primary_language_changes_zero": len(primary_language_changes) == 0,
        "task_type_changes_zero": len(task_type_changes) == 0,
        "all_difficulty_changes_zero": all(value == 0 for value in difficulty_change_counts.values()),
    }

    return {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "generated_at": utc_now(),
        "methodological_boundary": {
            "ground_truth_used": False,
            "ranking_or_topk_evaluated": False,
            "purpose": "controlled baseline-vs-B4 task-side feature comparison",
        },
        "baseline": {
            "path": baseline["path"],
            "sha256": baseline["sha256"],
            "expected_sha256": expected,
            "scope": _scope(baseline),
            "task_feature_version_distribution": dict(sorted(baseline_versions.items())),
        },
        "candidate": {
            "path": candidate["path"],
            "sha256": candidate["sha256"],
            "scope": _scope(candidate),
            "task_feature_version_distribution": dict(sorted(candidate_versions.items())),
        },
        "hard_invariants": hard_invariants,
        "hard_invariant_pass": all(hard_invariants.values()),
        "candidate_identity": {
            "missing_from_candidate_count": len(missing),
            "missing_from_candidate": [{"repository": r, "issue_number": n} for r, n in missing],
            "added_in_candidate_count": len(added),
            "added_in_candidate": [{"repository": r, "issue_number": n} for r, n in added],
        },
        "eligibility": {
            "change_count": len(eligibility_changes),
            "changes": eligibility_changes,
        },
        "raw_input": {
            "change_count": len(raw_input_changes),
            "changes": raw_input_changes,
            "primary_language_change_count": len(primary_language_changes),
            "primary_language_changes": primary_language_changes,
        },
        "task_type_regression": {
            "change_count": len(task_type_changes),
            "changes": task_type_changes,
        },
        "difficulty_regression": {
            "change_counts": difficulty_change_counts,
            "changes": difficulty_changes,
        },
        "skill_requirement_changes": {
            "changed_task_count": len(requirement_changes),
            "added_skill_name_distribution": dict(sorted(added_requirement_counter.items(), key=lambda x: x[0].casefold())),
            "removed_skill_name_distribution": dict(sorted(removed_requirement_counter.items(), key=lambda x: x[0].casefold())),
            "tasks": requirement_changes,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--expected-baseline-sha256", required=True)
    parser.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = compare_databases(
        args.baseline,
        args.candidate,
        expected_baseline_sha256=args.expected_baseline_sha256,
    )
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "event": "skill_requirement_comparison_generated",
        "output": str(output),
        "hard_invariant_pass": report["hard_invariant_pass"],
        "task_type_change_count": report["task_type_regression"]["change_count"],
        "difficulty_change_counts": report["difficulty_regression"]["change_counts"],
        "skill_requirement_changed_task_count": report["skill_requirement_changes"]["changed_task_count"],
    }, ensure_ascii=False, sort_keys=True))
    return 0 if report["hard_invariant_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
