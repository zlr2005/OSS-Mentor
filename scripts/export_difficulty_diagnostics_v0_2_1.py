#!/usr/bin/env python3
"""Extended read-only diagnostics for difficulty-rules-v0.2.1.

This script REUSES the existing:
    scripts/export_difficulty_diagnostics_v0.2.py

for its core snapshot loading, read-only SQLite handling, base diagnostics,
and baseline-to-after comparison. It adds v0.2.1-specific analysis that the
v0.2 script does not preserve, especially:
- effort.technical_complexity
- effort.validation_burden
- explicit transition matrices
- v0.2.1 strong-evidence trigger counts
- targeted protection/anomaly queues
- static filter-threshold crossing counts

It never calls matching.py and never computes recommendation rankings.
It never writes to either SQLite database.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4
import os

SCHEMA_VERSION = "difficulty_diagnostics_v0.2.1"
EXPECTED_FORMULA_VERSION = "difficulty-rules-v0.2.1"
EFFORT_ORDER = {
    "under_2h": 0,
    "half_day": 1,
    "one_day": 2,
    "multi_day": 3,
}
DIMENSIONS = {
    "code": "estimated_code_difficulty",
    "setup": "estimated_setup_difficulty",
    "project_context": "estimated_project_context_difficulty",
    "collaboration": "estimated_collaboration_difficulty",
}
BASE_SCRIPT_CANDIDATES = (
    "export_difficulty_diagnostics.py",
    "export_difficulty_diagnostics_v0.2.py",
)


class DiagnosticError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fatal(message: str) -> "NoReturn":
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_base_module(repo_root: Path):
    path = next(
        (
            repo_root / "scripts" / name
            for name in BASE_SCRIPT_CANDIDATES
            if (repo_root / "scripts" / name).is_file()
        ),
        None,
    )
    if path is None:
        expected = ", ".join(
            str(repo_root / "scripts" / name)
            for name in BASE_SCRIPT_CANDIDATES
        )
        raise DiagnosticError(
            "required base diagnostics script does not exist. "
            f"Checked: {expected}"
        )
    spec = importlib.util.spec_from_file_location(
        "_oss_mentor_difficulty_diagnostics_v02", path
    )
    if spec is None or spec.loader is None:
        raise DiagnosticError(f"cannot load base diagnostics module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in (
        "load_database_snapshot",
        "build_difficulty_diagnostics",
    ):
        if not hasattr(module, name):
            raise DiagnosticError(
                f"base diagnostics script is missing required function: {name}"
            )
    return module


def json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def json_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return bool(value)


def compact(record: Mapping[str, Any], reason: str | None = None) -> dict[str, Any]:
    item = {
        "task_candidate_id": int(record.get("task_candidate_id") or 0),
        "repository": str(record.get("repository") or ""),
        "issue_number": int(record.get("issue_number") or 0),
        "title": str(record.get("title") or ""),
        "task_types": json_string_list(record.get("task_types_json")),
        "newcomer_label_signal": truthy(record.get("newcomer_label_signal")),
        "difficulty": {
            name: (
                int(record[field]) if record.get(field) is not None else None
            )
            for name, field in DIMENSIONS.items()
        },
        "effort": str(record.get("estimated_effort_bucket") or ""),
    }
    if reason:
        item["trigger_reason"] = reason
    return item


def enrich_record(record: Mapping[str, Any]) -> dict[str, Any]:
    evidence = json_object(record.get("feature_evidence_json"))
    assessment = json_object(evidence.get("difficulty_assessment"))
    information = json_object(assessment.get("information_quality"))
    dimensions = json_object(assessment.get("dimensions"))
    effort = json_object(assessment.get("effort"))
    task_types = json_string_list(record.get("task_types_json"))
    labels = json_string_list(record.get("labels_json"))
    auxiliary = json_object(evidence.get("auxiliary_signals"))
    performance = (
        "performance" in {value.casefold() for value in task_types}
        or truthy(auxiliary.get("performance"))
    )
    return {
        **dict(record),
        "_evidence": evidence,
        "_assessment": assessment,
        "_information": information,
        "_dimensions": dimensions,
        "_effort": effort,
        "_task_types": task_types,
        "_labels": labels,
        "_performance_signal": performance,
    }


def eligible(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        enrich_record(record)
        for record in records
        if str(record.get("candidate_eligibility") or "") == "eligible"
    ]


def level_distribution(
    records: list[dict[str, Any]], field: str
) -> dict[str, Any]:
    counts = Counter(
        int(record[field])
        for record in records
        if record.get(field) is not None
    )
    total = sum(counts.values())
    normalized = {str(level): counts[level] for level in range(4)}
    return {
        "total": total,
        "counts": normalized,
        "rates": {
            level: (count / total if total else None)
            for level, count in normalized.items()
        },
    }


def categorical(values: list[str]) -> dict[str, Any]:
    counter = Counter(values)
    total = sum(counter.values())
    return {
        "total": total,
        "counts": dict(sorted(counter.items())),
        "rates": {
            key: (value / total if total else None)
            for key, value in sorted(counter.items())
        },
    }


def identity(record: Mapping[str, Any]) -> int:
    return int(record.get("task_candidate_id") or 0)


def transition_analysis(
    before_records: list[dict[str, Any]],
    after_records: list[dict[str, Any]],
) -> dict[str, Any]:
    before = {identity(record): enrich_record(record) for record in before_records if str(record.get("candidate_eligibility") or "") == "eligible"}
    after = {identity(record): enrich_record(record) for record in after_records if str(record.get("candidate_eligibility") or "") == "eligible"}
    common = sorted(set(before) & set(after))

    dimensions: dict[str, Any] = {}
    for name, field in DIMENSIONS.items():
        transitions: Counter[str] = Counter()
        changed_records: list[dict[str, Any]] = []
        increased = decreased = changed = 0
        for task_id in common:
            left = before[task_id].get(field)
            right = after[task_id].get(field)
            if left is None or right is None:
                continue
            left_i = int(left)
            right_i = int(right)
            transitions[f"{left_i}->{right_i}"] += 1
            if left_i != right_i:
                changed += 1
                if right_i > left_i:
                    increased += 1
                else:
                    decreased += 1
                row = compact(after[task_id], "difficulty dimension changed")
                row["before"] = left_i
                row["after"] = right_i
                changed_records.append(row)
        dimensions[name] = {
            "before_distribution": level_distribution(list(before.values()), field),
            "after_distribution": level_distribution(list(after.values()), field),
            "changed_count": changed,
            "increased_count": increased,
            "decreased_count": decreased,
            "transition_counts": dict(sorted(transitions.items())),
            "level_1_to_2_count": transitions.get("1->2", 0),
            "level_1_to_3_count": transitions.get("1->3", 0),
            "new_level_3_count": sum(
                count
                for key, count in transitions.items()
                if key.endswith("->3") and not key.startswith("3->")
            ),
            "changed_records": changed_records,
        }

    effort_transitions: Counter[str] = Counter()
    effort_changed: list[dict[str, Any]] = []
    effort_increased = effort_decreased = 0
    for task_id in common:
        left = str(before[task_id].get("estimated_effort_bucket") or "")
        right = str(after[task_id].get("estimated_effort_bucket") or "")
        effort_transitions[f"{left}->{right}"] += 1
        if left != right:
            if left in EFFORT_ORDER and right in EFFORT_ORDER:
                if EFFORT_ORDER[right] > EFFORT_ORDER[left]:
                    effort_increased += 1
                else:
                    effort_decreased += 1
            row = compact(after[task_id], "effort bucket changed")
            row["before"] = left
            row["after"] = right
            effort_changed.append(row)

    def effort_dist(records: list[dict[str, Any]]) -> dict[str, Any]:
        counter = Counter(
            str(record.get("estimated_effort_bucket") or "")
            for record in records
        )
        total = sum(counter.values())
        ordered = {bucket: counter[bucket] for bucket in EFFORT_ORDER}
        extras = {
            key: value
            for key, value in sorted(counter.items())
            if key not in EFFORT_ORDER
        }
        ordered.update(extras)
        return {
            "total": total,
            "counts": ordered,
            "rates": {
                key: (value / total if total else None)
                for key, value in ordered.items()
            },
        }

    return {
        "common_eligible_count": len(common),
        "baseline_only_task_candidate_ids": sorted(set(before) - set(after)),
        "after_only_task_candidate_ids": sorted(set(after) - set(before)),
        "dimensions": dimensions,
        "effort": {
            "before_distribution": effort_dist(list(before.values())),
            "after_distribution": effort_dist(list(after.values())),
            "changed_count": len(effort_changed),
            "increased_count": effort_increased,
            "decreased_count": effort_decreased,
            "transition_counts": dict(sorted(effort_transitions.items())),
            "half_day_to_one_day_count": effort_transitions.get(
                "half_day->one_day", 0
            ),
            "half_day_to_multi_day_count": effort_transitions.get(
                "half_day->multi_day", 0
            ),
            "one_day_to_multi_day_count": effort_transitions.get(
                "one_day->multi_day", 0
            ),
            "changed_records": effort_changed,
        },
    }


def task_type_analysis(
    before_records: list[dict[str, Any]],
    after_records: list[dict[str, Any]],
) -> dict[str, Any]:
    before = {
        identity(record): sorted(
            json_string_list(record.get("task_types_json")), key=str.casefold
        )
        for record in before_records
        if str(record.get("candidate_eligibility") or "") == "eligible"
    }
    after = {
        identity(record): sorted(
            json_string_list(record.get("task_types_json")), key=str.casefold
        )
        for record in after_records
        if str(record.get("candidate_eligibility") or "") == "eligible"
    }
    common = sorted(set(before) & set(after))
    changes = [
        {
            "task_candidate_id": task_id,
            "before": before[task_id],
            "after": after[task_id],
        }
        for task_id in common
        if before[task_id] != after[task_id]
    ]

    def distribution(mapping: dict[int, list[str]]) -> dict[str, Any]:
        individual: Counter[str] = Counter()
        tuples: Counter[str] = Counter()
        for values in mapping.values():
            individual.update(values)
            tuples["|".join(values) if values else "<empty>"] += 1
        return {
            "type_counts": dict(sorted(individual.items())),
            "tuple_counts": dict(sorted(tuples.items())),
        }

    return {
        "change_count": len(changes),
        "changes": changes,
        "before_distribution": distribution(before),
        "after_distribution": distribution(after),
    }


def effort_assessment_analysis(
    after_records: list[dict[str, Any]],
) -> dict[str, Any]:
    records = eligible(after_records)
    formula_versions: list[str] = []
    scopes: list[str] = []
    applicable: list[str] = []
    provisional: list[str] = []
    confidence: list[str] = []
    technical: list[str] = []
    validation: list[str] = []
    actionability: list[str] = []
    information_confidence: list[str] = []
    body_missing_count = 0

    missing_technical: list[dict[str, Any]] = []
    missing_validation: list[dict[str, Any]] = []

    for record in records:
        assessment = record["_assessment"]
        information = record["_information"]
        effort = record["_effort"]
        formula_versions.append(str(assessment.get("formula_version") or "<missing>"))
        actionability.append(str(information.get("actionability") or "<missing>"))
        information_confidence.append(
            str(information.get("confidence") or "<missing>")
        )
        body_missing_count += bool(information.get("body_missing"))
        scopes.append(str(effort.get("scope") or "<missing>"))
        applicable.append(str(bool(effort.get("applicable"))).lower())
        provisional.append(str(bool(effort.get("provisional"))).lower())
        confidence.append(str(effort.get("confidence") or "<missing>"))
        technical_value = str(effort.get("technical_complexity") or "<missing>")
        validation_value = str(effort.get("validation_burden") or "<missing>")
        technical.append(technical_value)
        validation.append(validation_value)
        if technical_value == "<missing>":
            missing_technical.append(compact(record, "technical_complexity missing"))
        if validation_value == "<missing>":
            missing_validation.append(compact(record, "validation_burden missing"))

    return {
        "formula_version_distribution": categorical(formula_versions),
        "information_quality": {
            "actionability_distribution": categorical(actionability),
            "confidence_distribution": categorical(information_confidence),
            "body_missing_count": body_missing_count,
        },
        "effort": {
            "scope_distribution": categorical(scopes),
            "applicable_distribution": categorical(applicable),
            "provisional_distribution": categorical(provisional),
            "confidence_distribution": categorical(confidence),
            "technical_complexity_distribution": categorical(technical),
            "validation_burden_distribution": categorical(validation),
            "technical_complexity_missing": {
                "count": len(missing_technical),
                "records": missing_technical,
            },
            "validation_burden_missing": {
                "count": len(missing_validation),
                "records": missing_validation,
            },
        },
    }


def dimension_evidence(record: Mapping[str, Any], dimension: str) -> list[dict[str, Any]]:
    dimensions = record.get("_dimensions") or {}
    item = dimensions.get(dimension)
    if not isinstance(item, Mapping):
        return []
    evidence = item.get("evidence")
    if not isinstance(evidence, list):
        return []
    return [dict(value) for value in evidence if isinstance(value, Mapping)]


def evidence_analysis(
    before_records: list[dict[str, Any]],
    after_records: list[dict[str, Any]],
) -> dict[str, Any]:
    def counters(records: list[dict[str, Any]]) -> dict[str, Counter[str]]:
        rules: Counter[str] = Counter()
        strong: Counter[str] = Counter()
        reasons: Counter[str] = Counter()
        for raw in records:
            if str(raw.get("candidate_eligibility") or "") != "eligible":
                continue
            record = enrich_record(raw)
            for dimension in DIMENSIONS:
                for item in dimension_evidence(record, dimension):
                    rule = str(item.get("rule_id") or "<missing>")
                    strength = str(item.get("strength") or "<missing>")
                    rules[rule] += 1
                    reasons[str(item.get("reason") or "<missing>")] += 1
                    if strength == "strong":
                        strong[rule] += 1
        return {"rules": rules, "strong": strong, "reasons": reasons}

    before = counters(before_records)
    after = counters(after_records)
    all_strong = sorted(set(before["strong"]) | set(after["strong"]))
    return {
        "after_rule_id_counts": dict(sorted(after["rules"].items())),
        "after_reason_counts": dict(sorted(after["reasons"].items())),
        "after_strong_rule_id_counts": dict(sorted(after["strong"].items())),
        "strong_rule_count_deltas": {
            rule: after["strong"][rule] - before["strong"][rule]
            for rule in all_strong
            if after["strong"][rule] != before["strong"][rule]
        },
    }


def setup_category_counts(after_records: list[dict[str, Any]]) -> dict[str, Any]:
    categories = {
        "reported_environment": ("reported_environment",),
        "platform_required": ("platform_required",),
        "container_or_cluster": ("container_or_cluster",),
        "filesystem_specific": ("filesystem_container",),
        "deployed_profiling": ("deployed_profiling",),
        "gpu": ("gpu_required",),
        "multinode": ("multinode_required",),
        "distributed_validation": ("distributed_validation",),
        "native_toolchain": ("native_toolchain",),
        "service_required": ("service_required",),
    }
    counts: Counter[str] = Counter()
    records_by_category: dict[str, list[dict[str, Any]]] = {
        name: [] for name in categories
    }
    for raw in after_records:
        if str(raw.get("candidate_eligibility") or "") != "eligible":
            continue
        record = enrich_record(raw)
        rules = [
            str(item.get("rule_id") or "")
            for item in dimension_evidence(record, "setup")
        ]
        for category, needles in categories.items():
            if any(any(needle in rule for needle in needles) for rule in rules):
                counts[category] += 1
                records_by_category[category].append(
                    compact(record, f"setup evidence category: {category}")
                )
    return {
        "counts": {name: counts[name] for name in categories},
        "records": records_by_category,
    }


def protection_queues(after_records: list[dict[str, Any]]) -> dict[str, Any]:
    queues: dict[str, list[dict[str, Any]]] = {
        "performance_level3_without_nonperformance_strong_support": [],
        "reported_environment_only_high_setup": [],
        "comment_only_collaboration_above_one": [],
        "documentation_typo_or_wording_complex": [],
        "body_missing_high_information_confidence": [],
        "non_actionable_effort_applicable_true": [],
        "unclear_or_design_pending_certain_effort": [],
        "ordinary_local_low_complexity_multi_day": [],
    }

    doc_wording = re.compile(r"\b(?:typo|wording|readme)\b", re.IGNORECASE)

    for raw in after_records:
        if str(raw.get("candidate_eligibility") or "") != "eligible":
            continue
        record = enrich_record(raw)
        information = record["_information"]
        effort = record["_effort"]

        for dimension in ("code", "project_context"):
            field = DIMENSIONS[dimension]
            if (
                record["_performance_signal"]
                and int(record.get(field) or 0) == 3
            ):
                evidence = dimension_evidence(record, dimension)
                supported = any(
                    str(item.get("strength")) == "strong"
                    and int(item.get("suggested_level") or -1) == 3
                    and "performance_auxiliary"
                    not in str(item.get("rule_id") or "")
                    for item in evidence
                )
                if not supported:
                    queues[
                        "performance_level3_without_nonperformance_strong_support"
                    ].append(
                        compact(
                            record,
                            f"performance signal with {dimension}=3 but no "
                            "non-performance strong level-3 evidence",
                        )
                    )

        setup_evidence = dimension_evidence(record, "setup")
        setup_level = int(record.get("estimated_setup_difficulty") or 0)
        if setup_level >= 2:
            has_reported = any(
                "reported_environment_only" in str(item.get("rule_id") or "")
                for item in setup_evidence
            )
            has_material_required = any(
                str(item.get("strength") or "") in {"medium", "strong"}
                and "reported_environment_only"
                not in str(item.get("rule_id") or "")
                for item in setup_evidence
            )
            if has_reported and not has_material_required:
                queues["reported_environment_only_high_setup"].append(
                    compact(
                        record,
                        "setup>=2 appears supported only by reported environment",
                    )
                )

        collaboration_level = int(
            record.get("estimated_collaboration_difficulty") or 0
        )
        if collaboration_level > 1:
            collab_evidence = dimension_evidence(record, "collaboration")
            material = [
                item
                for item in collab_evidence
                if str(item.get("rule_id") or "")
                not in {
                    "difficulty.collaboration.comment_volume",
                    "difficulty.collaboration.ordinary_review",
                }
                and int(item.get("suggested_level") or 0) >= 2
            ]
            if not material:
                queues["comment_only_collaboration_above_one"].append(
                    compact(
                        record,
                        "collaboration>1 without material design/coordination evidence",
                    )
                )

        if set(value.casefold() for value in record["_task_types"]) == {
            "documentation"
        }:
            text = f"{record.get('title') or ''}\n{record.get('body_text') or ''}"
            if doc_wording.search(text) and (
                int(record.get("estimated_code_difficulty") or 0) >= 2
                or int(
                    record.get("estimated_project_context_difficulty") or 0
                )
                >= 2
                or str(record.get("estimated_effort_bucket") or "")
                == "multi_day"
            ):
                queues["documentation_typo_or_wording_complex"].append(
                    compact(
                        record,
                        "documentation typo/wording task escalated to complex level",
                    )
                )

        if (
            bool(information.get("body_missing"))
            and str(information.get("confidence") or "") == "high"
        ):
            queues["body_missing_high_information_confidence"].append(
                compact(record, "body_missing with high information confidence")
            )

        if (
            str(information.get("actionability") or "") == "non_actionable"
            and effort.get("applicable") is True
        ):
            queues["non_actionable_effort_applicable_true"].append(
                compact(record, "non_actionable effort marked applicable=true")
            )

        if (
            str(information.get("actionability") or "")
            in {"unclear", "design_pending"}
            and effort.get("applicable") is True
            and effort.get("provisional") is False
        ):
            queues["unclear_or_design_pending_certain_effort"].append(
                compact(
                    record,
                    "unclear/design_pending task has applicable non-provisional effort",
                )
            )

        if (
            str(effort.get("scope") or "") == "local"
            and str(effort.get("technical_complexity") or "") == "low"
            and str(effort.get("validation_burden") or "")
            in {"none", "light"}
            and str(effort.get("bucket") or "") == "multi_day"
        ):
            queues["ordinary_local_low_complexity_multi_day"].append(
                compact(
                    record,
                    "local low-complexity task with non-heavy validation became multi_day",
                )
            )

    return {
        name: {"count": len(records), "records": records}
        for name, records in queues.items()
    }


def matching_static_risk(
    before_records: list[dict[str, Any]],
    after_records: list[dict[str, Any]],
    before_skills: Mapping[int, Mapping[str, int]],
    after_skills: Mapping[int, Mapping[str, int]],
) -> dict[str, Any]:
    before = {
        identity(record): record
        for record in before_records
        if str(record.get("candidate_eligibility") or "") == "eligible"
    }
    after = {
        identity(record): record
        for record in after_records
        if str(record.get("candidate_eligibility") or "") == "eligible"
    }
    common = sorted(set(before) & set(after))

    crossings: dict[str, dict[str, Any]] = {}
    for dimension, field in (
        ("code", "estimated_code_difficulty"),
        ("setup", "estimated_setup_difficulty"),
    ):
        threshold_map: dict[str, Any] = {}
        for threshold in range(4):
            records: list[dict[str, Any]] = []
            for task_id in common:
                left = before[task_id].get(field)
                right = after[task_id].get(field)
                if left is None or right is None:
                    continue
                if int(left) <= threshold < int(right):
                    item = compact(
                        after[task_id],
                        f"{dimension} crossed max_{dimension}_difficulty "
                        f"threshold {threshold}",
                    )
                    item["before"] = int(left)
                    item["after"] = int(right)
                    records.append(item)
            threshold_map[str(threshold)] = {
                "count": len(records),
                "records": records,
            }
        crossings[dimension] = threshold_map

    skill_change_records: list[dict[str, Any]] = []
    for task_id in common:
        left = dict(before_skills.get(task_id, {}))
        right = dict(after_skills.get(task_id, {}))
        changed = {
            skill: {"before": left.get(skill), "after": right.get(skill)}
            for skill in sorted(set(left) | set(right), key=str.casefold)
            if left.get(skill) != right.get(skill)
        }
        if changed:
            item = compact(after[task_id], "skill minimum level changed")
            item["skill_minimum_levels"] = changed
            skill_change_records.append(item)

    return {
        "semantic": (
            "potential matching availability impact only; matching.py is not "
            "called and recommendation rankings are not computed"
        ),
        "filter_threshold_crossings": crossings,
        "skill_minimum_level_change_count": len(skill_change_records),
        "skill_minimum_level_change_records": skill_change_records,
    }


def data_integrity(
    before_records: list[dict[str, Any]],
    after_records: list[dict[str, Any]],
) -> dict[str, Any]:
    def counts(records: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "active_candidates": len(records),
            "eligible_candidates": sum(
                str(record.get("candidate_eligibility") or "") == "eligible"
                for record in records
            ),
            "newcomer_eligible_candidates": sum(
                str(record.get("candidate_eligibility") or "") == "eligible"
                and truthy(record.get("newcomer_label_signal"))
                for record in records
            ),
        }

    before_ids = {identity(record) for record in before_records}
    after_ids = {identity(record) for record in after_records}
    return {
        "baseline": counts(before_records),
        "after": counts(after_records),
        "common_active_candidate_count": len(before_ids & after_ids),
        "baseline_only_task_candidate_ids": sorted(before_ids - after_ids),
        "after_only_task_candidate_ids": sorted(after_ids - before_ids),
    }


def validate_after_formula(after_records: list[dict[str, Any]]) -> None:
    counts: Counter[str] = Counter()
    for raw in after_records:
        if str(raw.get("candidate_eligibility") or "") != "eligible":
            continue
        record = enrich_record(raw)
        counts[str(record["_assessment"].get("formula_version") or "<missing>")] += 1
    unexpected = {
        key: value
        for key, value in counts.items()
        if key != EXPECTED_FORMULA_VERSION
    }
    if unexpected:
        raise DiagnosticError(
            "after database eligible records contain unexpected difficulty "
            f"formula versions: {dict(counts)}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export extended read-only v0.2.1 difficulty diagnostics."
    )
    parser.add_argument("--baseline-database", "--baseline-db", required=True)
    parser.add_argument("--after-database", "--after-db", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--generated-at", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    base = load_base_module(repo_root)

    try:
        baseline = base.load_database_snapshot(args.baseline_database)
        after = base.load_database_snapshot(args.after_database)
        validate_after_formula(after["records"])

        base_report = base.build_difficulty_diagnostics(
            after["records"],
            baseline_records=baseline["records"],
            after_skills=after["skills"],
            baseline_skills=baseline["skills"],
            after_database_path=after["database_path"],
            baseline_database_path=baseline["database_path"],
            generated_at=args.generated_at,
        )

        transition = transition_analysis(
            baseline["records"], after["records"]
        )
        types = task_type_analysis(
            baseline["records"], after["records"]
        )
        effort_detail = effort_assessment_analysis(after["records"])
        evidence = evidence_analysis(
            baseline["records"], after["records"]
        )
        protection = protection_queues(after["records"])
        static_risk = matching_static_risk(
            baseline["records"],
            after["records"],
            baseline["skills"],
            after["skills"],
        )

        report = dict(base_report)
        report["schema_version"] = SCHEMA_VERSION
        report["base_diagnostics_schema_version"] = base_report.get(
            "schema_version"
        )
        report["generated_at_v0_2_1_extension"] = (
            args.generated_at or utc_now()
        )
        report["methodological_boundary_v0_2_1"] = {
            "ground_truth_used": False,
            "accuracy_metrics_permitted": False,
            "purpose": [
                "distribution shift",
                "anomaly detection",
                "rule-trigger inspection",
                "engineering regression",
                "potential matching availability impact",
            ],
            "matching_rankings_computed": False,
        }
        report["v0_2_1_extension"] = {
            "data_integrity": data_integrity(
                baseline["records"], after["records"]
            ),
            "task_types": types,
            "transitions": transition,
            "after_assessment_details": effort_detail,
            "setup_evidence_categories": setup_category_counts(
                after["records"]
            ),
            "evidence_triggers": evidence,
            "protection_queues": protection,
            "matching_static_risk": static_risk,
        }

        output_path = Path(args.output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_name(
            f".{output_path.name}.tmp-{uuid4().hex}"
        )
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, output_path)

    except DiagnosticError as exc:
        fatal(str(exc))
    except Exception as exc:
        fatal(f"{type(exc).__name__}: {exc}")

    integrity = report["v0_2_1_extension"]["data_integrity"]
    transitions = report["v0_2_1_extension"]["transitions"]
    task_types = report["v0_2_1_extension"]["task_types"]
    effort = transitions["effort"]
    protection = report["v0_2_1_extension"]["protection_queues"]
    assessment = report["v0_2_1_extension"]["after_assessment_details"]

    print()
    print("B3 full difficulty diagnostics v0.2.1")
    print()
    print(f"Baseline: {baseline['database_path']}")
    print(f"After: {after['database_path']}")
    print(f"Output: {output_path}")
    print()
    print(
        "Active candidates: "
        f"{integrity['after']['active_candidates']}"
    )
    print(
        "Eligible candidates: "
        f"{integrity['after']['eligible_candidates']}"
    )
    print(
        "Newcomer eligible candidates: "
        f"{integrity['after']['newcomer_eligible_candidates']}"
    )
    print(
        f"Task type changes: {task_types['change_count']}"
    )
    print()
    for name in DIMENSIONS:
        item = transitions["dimensions"][name]
        print(
            f"{name}: changed={item['changed_count']} "
            f"up={item['increased_count']} "
            f"down={item['decreased_count']}"
        )
    print(
        "effort: "
        f"changed={effort['changed_count']} "
        f"up={effort['increased_count']} "
        f"down={effort['decreased_count']}"
    )
    print()
    print(
        "Technical complexity: "
        + json.dumps(
            assessment["effort"]["technical_complexity_distribution"][
                "counts"
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    print(
        "Validation burden: "
        + json.dumps(
            assessment["effort"]["validation_burden_distribution"][
                "counts"
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    print()
    print("Protection queues:")
    for name, item in protection.items():
        print(f"  {name}: {item['count']}")
    print()
    print(
        "Matching analysis: potential matching availability impact only; "
        "rankings were not computed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())