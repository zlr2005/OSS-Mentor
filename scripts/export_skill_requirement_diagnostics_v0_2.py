"""Read-only B4 v0.2 SkillRequirement diagnostics.

The database is opened through SQLite URI ``mode=ro`` with ``PRAGMA query_only``.
This script does not run migrations, feature extraction, matching/ranking, or
network requests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

DIAGNOSTIC_SCHEMA_VERSION = "skill_requirement_diagnostics_v0.2"
PRODUCTION_TOOLS = ("pytest", "Jest", "Docker", "Maven", "Gradle")
PRODUCTION_TOOL_KEYS = {value.casefold(): value for value in PRODUCTION_TOOLS}
HOLDBACK_SKILLS = ("CI", "npm", "pnpm", "Yarn", "CMake", "CUDA", "ROCm", "GitHub Actions")
HOLDBACK_KEYS = {value.casefold(): value for value in HOLDBACK_SKILLS}
PLATFORM_SKILLS = {"platform:linux", "platform:windows", "platform:macos"}
REQUIRED_EVIDENCE_SUMMARY_FIELDS = {
    "category",
    "role",
    "decision",
    "matching_facing",
    "minimum_level",
    "importance",
    "requirement_source",
    "evidence",
}
REQUIRED_EVIDENCE_ITEM_FIELDS = {
    "source",
    "rule_id",
    "matched_value",
    "normalized_value",
    "strength",
    "reason",
}


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


def _json_object(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 6)


def _percentile_nearest_rank(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _counter(counter: Counter[Any]) -> dict[str, int]:
    return {str(key): int(value) for key, value in sorted(counter.items(), key=lambda x: str(x[0]))}


def _requirement_category(requirement: Mapping[str, Any], primary_language: str) -> str:
    skill = str(requirement.get("skill_name") or "").strip().casefold()
    source = str(requirement.get("requirement_source") or "")
    if source == "repository_primary_language" or (
        primary_language and skill == primary_language.casefold()
    ):
        return "programming_language"
    if source == "inferred_task_type":
        return "task_type"
    if skill.startswith("platform:"):
        return "platform"
    if source == "inferred_tool_requirement" or skill in PRODUCTION_TOOL_KEYS:
        return "tool"
    return "other"


def _candidate_summary(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "task_candidate_id": int(record["task_candidate_id"]),
        "repository": str(record["repository"]),
        "issue_number": int(record["issue_number"]),
        "html_url": str(record.get("html_url") or ""),
        "title": str(record.get("title") or ""),
        "task_types": list(record.get("task_types") or []),
        "estimated_code_difficulty": record.get("estimated_code_difficulty"),
    }


def build_diagnostics(database_path: str | Path) -> dict[str, Any]:
    database = Path(database_path).expanduser().resolve()
    before_hash = file_sha256(database)
    connection = connect_readonly(database)
    try:
        candidate_rows = connection.execute(
            """
            SELECT
                tc.task_candidate_id,
                r.full_name AS repository,
                COALESCE(r.primary_language, '') AS primary_language,
                COALESCE(r.is_archived, 0) AS is_archived,
                COALESCE(r.is_disabled, 0) AS is_disabled,
                tc.issue_number,
                COALESCE(tc.html_url, '') AS html_url,
                COALESCE(tc.title, '') AS title,
                COALESCE(tc.body_text, '') AS body_text,
                tc.labels_json,
                tc.task_types_json,
                COALESCE(tc.candidate_eligibility, '') AS candidate_eligibility,
                COALESCE(tc.newcomer_label_signal, 0) AS newcomer_label_signal,
                tc.estimated_code_difficulty,
                tc.estimated_setup_difficulty,
                tc.estimated_project_context_difficulty,
                tc.estimated_collaboration_difficulty,
                tc.estimated_effort_bucket,
                COALESCE(tc.feature_evidence_json, '{}') AS feature_evidence_json,
                COALESCE(tc.task_feature_version, '') AS task_feature_version
            FROM task_candidate AS tc
            JOIN repository AS r USING(repository_id)
            ORDER BY tc.task_candidate_id
            """
        ).fetchall()
        requirement_rows = connection.execute(
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
    finally:
        connection.close()
    after_hash = file_sha256(database)
    if before_hash != after_hash:
        raise RuntimeError("database changed while read-only diagnostics were running")

    candidates: list[dict[str, Any]] = []
    for row in candidate_rows:
        item = dict(row)
        item["is_archived"] = bool(item["is_archived"])
        item["is_disabled"] = bool(item["is_disabled"])
        item["newcomer_label_signal"] = bool(item["newcomer_label_signal"])
        item["labels"] = _json_list(item["labels_json"])
        item["task_types"] = _json_list(item["task_types_json"])
        item["feature_evidence"] = _json_object(item["feature_evidence_json"])
        candidates.append(item)
    requirements = [dict(row) for row in requirement_rows]

    active = [r for r in candidates if not r["is_archived"] and not r["is_disabled"]]
    eligible = [r for r in active if r["candidate_eligibility"] == "eligible"]
    newcomer = [r for r in eligible if r["newcomer_label_signal"]]
    eligible_ids = {int(r["task_candidate_id"]) for r in eligible}
    by_id = {int(r["task_candidate_id"]): r for r in candidates}
    req_by_task: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for req in requirements:
        req_by_task[int(req["task_candidate_id"])].append(req)
    eligible_requirements = [r for r in requirements if int(r["task_candidate_id"]) in eligible_ids]

    counts_per_task = [len(req_by_task.get(int(r["task_candidate_id"]), [])) for r in eligible]
    covered = sum(value > 0 for value in counts_per_task)
    requirement_count_distribution = Counter(counts_per_task)

    coarse_only = 0
    coarse_only_rows: list[dict[str, Any]] = []
    for record in eligible:
        task_id = int(record["task_candidate_id"])
        categories = {
            _requirement_category(req, str(record["primary_language"]))
            for req in req_by_task.get(task_id, [])
        }
        if categories <= {"programming_language", "task_type", "platform"}:
            coarse_only += 1
            coarse_only_rows.append(_candidate_summary(record))

    tool_stats: dict[str, Any] = {}
    tool_explosion: list[dict[str, Any]] = []
    tool_hard_anomalies: list[dict[str, Any]] = []
    production_tool_count_by_task: Counter[int] = Counter()
    for key, canonical in PRODUCTION_TOOL_KEYS.items():
        rows = [r for r in eligible_requirements if str(r["skill_name"]).casefold() == key]
        task_ids = {int(r["task_candidate_id"]) for r in rows}
        for task_id in task_ids:
            production_tool_count_by_task[task_id] += 1
        repo_counter = Counter(str(by_id[task_id]["repository"]) for task_id in task_ids)
        tool_stats[canonical] = {
            "requirement_count": len(rows),
            "task_count": len(task_ids),
            "minimum_level_distribution": _counter(Counter(int(r["minimum_level"]) for r in rows)),
            "importance_distribution": _counter(Counter(f"{float(r['importance']):.3g}" for r in rows)),
            "requirement_source_distribution": _counter(Counter(str(r["requirement_source"]) for r in rows)),
            "repository_distribution": _counter(repo_counter),
        }
        for req in rows:
            if float(req["importance"]) >= 1.0:
                tool_hard_anomalies.append({"requirement": req, "task": _candidate_summary(by_id[int(req["task_candidate_id"])])})
    for task_id, count in sorted(production_tool_count_by_task.items()):
        if count >= 3:
            tool_explosion.append({
                **_candidate_summary(by_id[task_id]),
                "production_tool_requirement_count": count,
                "tools": sorted(
                    str(r["skill_name"])
                    for r in req_by_task[task_id]
                    if str(r["skill_name"]).casefold() in PRODUCTION_TOOL_KEYS
                ),
            })

    holdback_rows = [
        {"requirement": r, "task": _candidate_summary(by_id[int(r["task_candidate_id"])])}
        for r in eligible_requirements
        if str(r["skill_name"]).casefold() in HOLDBACK_KEYS
    ]

    platform_rows = [
        r for r in eligible_requirements if str(r["skill_name"]).casefold() in PLATFORM_SKILLS
    ]
    platform_task_ids = {int(r["task_candidate_id"]) for r in platform_rows}
    platform_hard_queue = [
        {"requirement": r, "task": _candidate_summary(by_id[int(r["task_candidate_id"])])}
        for r in platform_rows
        if float(r["importance"]) >= 1.0
    ]

    language_downgrade_queue: list[dict[str, Any]] = []
    language_downgrade_violations: list[dict[str, Any]] = []
    for req in eligible_requirements:
        if str(req["requirement_source"]) != "repository_primary_language":
            continue
        if not math.isclose(float(req["importance"]), 0.3, rel_tol=0, abs_tol=1e-9):
            continue
        task = by_id[int(req["task_candidate_id"])]
        row = {"requirement": req, "task": _candidate_summary(task)}
        language_downgrade_queue.append(row)
        if not (
            task["task_types"] == ["documentation"]
            and int(task["estimated_code_difficulty"] or 0) == 0
        ):
            language_downgrade_violations.append(row)

    duplicate_groups: list[dict[str, Any]] = []
    duplicate_counter: Counter[tuple[int, str]] = Counter(
        (int(r["task_candidate_id"]), str(r["skill_name"]).casefold()) for r in requirements
    )
    for (task_id, normalized), count in sorted(duplicate_counter.items()):
        if count > 1:
            duplicate_groups.append({
                "task_candidate_id": task_id,
                "normalized_skill_name": normalized,
                "count": count,
            })

    evidence_anomalies: list[dict[str, Any]] = []
    evidence_source_counter: Counter[str] = Counter()
    evidence_strength_counter: Counter[str] = Counter()
    evidence_rule_counter: Counter[str] = Counter()
    rejected_rule_counter: Counter[str] = Counter()
    rejected_reason_counter: Counter[str] = Counter()
    platform_suspicious_queue: list[dict[str, Any]] = []

    for record in eligible:
        task_id = int(record["task_candidate_id"])
        skill_root = record["feature_evidence"].get("skill_requirement_evidence")
        if not isinstance(skill_root, dict):
            evidence_anomalies.append({"task_candidate_id": task_id, "reason": "missing_skill_requirement_evidence_root"})
            continue
        if not isinstance(skill_root.get("rules_version"), str) or not skill_root.get("rules_version"):
            evidence_anomalies.append({"task_candidate_id": task_id, "reason": "missing_rules_version"})
        skills = skill_root.get("skills")
        rejected = skill_root.get("rejected")
        if not isinstance(skills, dict):
            evidence_anomalies.append({"task_candidate_id": task_id, "reason": "skills_not_object"})
            skills = {}
        if not isinstance(rejected, list):
            evidence_anomalies.append({"task_candidate_id": task_id, "reason": "rejected_not_list"})
            rejected = []

        for rejected_item in rejected:
            if not isinstance(rejected_item, dict):
                continue
            rejected_rule_counter[str(rejected_item.get("rule_id") or "")] += 1
            rejected_reason_counter[str(rejected_item.get("reason") or "")] += 1

        suspicious_platform_context = any(
            isinstance(item, dict)
            and str(item.get("rule_id") or "") in {
                "skill.platform.body.reporter_environment",
                "skill.platform.body.reproduction_environment",
            }
            for item in rejected
        )
        if suspicious_platform_context and any(
            str(req["skill_name"]).casefold().startswith("platform:")
            for req in req_by_task.get(task_id, [])
        ):
            platform_suspicious_queue.append({
                **_candidate_summary(record),
                "platform_requirements": [
                    dict(req)
                    for req in req_by_task.get(task_id, [])
                    if str(req["skill_name"]).casefold().startswith("platform:")
                ],
            })

        for req in req_by_task.get(task_id, []):
            skill_name = str(req["skill_name"])
            summary = skills.get(skill_name)
            if not isinstance(summary, dict):
                evidence_anomalies.append({
                    "task_candidate_id": task_id,
                    "skill_name": skill_name,
                    "reason": "requirement_missing_included_evidence_summary",
                })
                continue
            missing_summary = sorted(REQUIRED_EVIDENCE_SUMMARY_FIELDS.difference(summary))
            if missing_summary:
                evidence_anomalies.append({
                    "task_candidate_id": task_id,
                    "skill_name": skill_name,
                    "reason": "missing_summary_fields",
                    "fields": missing_summary,
                })
            if int(summary.get("minimum_level") or -1) != int(req["minimum_level"]):
                evidence_anomalies.append({"task_candidate_id": task_id, "skill_name": skill_name, "reason": "minimum_level_mismatch"})
            try:
                evidence_importance = float(summary.get("importance"))
            except (TypeError, ValueError):
                evidence_importance = float("nan")
            if not math.isclose(evidence_importance, float(req["importance"]), rel_tol=0, abs_tol=1e-9):
                evidence_anomalies.append({"task_candidate_id": task_id, "skill_name": skill_name, "reason": "importance_mismatch"})
            if str(summary.get("requirement_source") or "") != str(req["requirement_source"]):
                evidence_anomalies.append({"task_candidate_id": task_id, "skill_name": skill_name, "reason": "requirement_source_mismatch"})
            evidence_items = summary.get("evidence")
            if not isinstance(evidence_items, list) or not evidence_items:
                evidence_anomalies.append({"task_candidate_id": task_id, "skill_name": skill_name, "reason": "missing_evidence_items"})
                continue
            for item in evidence_items:
                if not isinstance(item, dict):
                    evidence_anomalies.append({"task_candidate_id": task_id, "skill_name": skill_name, "reason": "evidence_item_not_object"})
                    continue
                missing_item = sorted(REQUIRED_EVIDENCE_ITEM_FIELDS.difference(item))
                if missing_item:
                    evidence_anomalies.append({"task_candidate_id": task_id, "skill_name": skill_name, "reason": "missing_evidence_item_fields", "fields": missing_item})
                evidence_source_counter[str(item.get("source") or "")] += 1
                evidence_strength_counter[str(item.get("strength") or "")] += 1
                evidence_rule_counter[str(item.get("rule_id") or "")] += 1

    platform_importance = Counter(f"{float(r['importance']):.3g}" for r in platform_rows)
    platform_skills = Counter(str(r["skill_name"]).casefold() for r in platform_rows)
    task_versions = Counter(str(r["task_feature_version"]) for r in eligible)
    req_versions = Counter(str(r["feature_version"]) for r in eligible_requirements)

    return {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "generated_at": utc_now(),
        "methodological_boundary": {
            "ground_truth_used": False,
            "accuracy_metrics_permitted": False,
            "ranking_or_topk_evaluated": False,
            "purpose": "B4 v0.2 task-side SkillRequirement formal diagnostics",
        },
        "database": {
            "path": str(database),
            "sha256_before": before_hash,
            "sha256_after": after_hash,
            "unchanged": before_hash == after_hash,
            "sqlite_access_mode": "uri_mode_ro_and_query_only",
        },
        "scope": {
            "total_candidate_count": len(candidates),
            "active_candidate_count": len(active),
            "eligible_candidate_count": len(eligible),
            "newcomer_eligible_count": len(newcomer),
            "task_feature_version_distribution": _counter(task_versions),
            "eligible_requirement_feature_version_distribution": _counter(req_versions),
        },
        "skill_coverage": {
            "eligible_task_count": len(eligible),
            "covered_task_count": covered,
            "no_skill_task_count": len(eligible) - covered,
            "coverage_rate": _rate(covered, len(eligible)),
        },
        "requirement_volume": {
            "eligible_requirement_count": len(eligible_requirements),
            "mean_per_task": round(statistics.fmean(counts_per_task), 4) if counts_per_task else None,
            "median_per_task": statistics.median(counts_per_task) if counts_per_task else None,
            "p90_per_task": _percentile_nearest_rank(counts_per_task, 0.90),
            "max_per_task": max(counts_per_task) if counts_per_task else None,
            "count_distribution": _counter(requirement_count_distribution),
        },
        "coarse_representation": {
            "coarse_only_task_count": coarse_only,
            "coarse_only_rate": _rate(coarse_only, len(eligible)),
            "coarse_only_tasks": coarse_only_rows,
        },
        "production_tool_requirements": tool_stats,
        "platform_v0_2": {
            "task_count": len(platform_task_ids),
            "requirement_count": len(platform_rows),
            "skill_distribution": _counter(platform_skills),
            "importance_distribution": _counter(platform_importance),
            "hard_requirement_count": len(platform_hard_queue),
            "hard_requirement_queue": platform_hard_queue,
            "suspicious_context_queue": platform_suspicious_queue,
        },
        "language_policy": {
            "downgrade_count": len(language_downgrade_queue),
            "downgrade_queue": language_downgrade_queue,
            "downgrade_violation_count": len(language_downgrade_violations),
            "downgrade_violations": language_downgrade_violations,
        },
        "evidence_contract": {
            "anomaly_count": len(evidence_anomalies),
            "anomalies": evidence_anomalies,
            "included_evidence_source_distribution": _counter(evidence_source_counter),
            "included_evidence_strength_distribution": _counter(evidence_strength_counter),
            "included_rule_id_distribution": _counter(evidence_rule_counter),
            "rejected_rule_id_distribution": _counter(rejected_rule_counter),
            "rejected_reason_distribution": _counter(rejected_reason_counter),
        },
        "anomaly_queues": {
            "tool_explosion_count": len(tool_explosion),
            "tool_explosion": tool_explosion,
            "tool_importance_1_count": len(tool_hard_anomalies),
            "tool_importance_1": tool_hard_anomalies,
            "holdback_leakage_count": len(holdback_rows),
            "holdback_leakage": holdback_rows,
            "duplicate_casefold_skill_group_count": len(duplicate_groups),
            "duplicate_casefold_skill_groups": duplicate_groups,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    diagnostics = build_diagnostics(args.database)
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(diagnostics, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "event": "skill_requirement_diagnostics_v0_2_generated",
        "output": str(output),
        "scope": diagnostics["scope"],
        "skill_coverage": diagnostics["skill_coverage"],
        "requirement_volume": diagnostics["requirement_volume"],
        "coarse_only_task_count": diagnostics["coarse_representation"]["coarse_only_task_count"],
        "anomaly_counts": {
            key: value
            for key, value in diagnostics["anomaly_queues"].items()
            if key.endswith("_count")
        },
        "evidence_anomaly_count": diagnostics["evidence_contract"]["anomaly_count"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
