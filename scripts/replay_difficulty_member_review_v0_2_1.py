#!/usr/bin/env python3
"""Reproduce the B3 difficulty-rules-v0.2.1 replay on the fixed 25-case snapshot.

Run from the OSS-Mentor repository root in PowerShell:

    $env:PYTHONPATH="src"
    python scripts/replay_difficulty_member_review_v0_2_1.py

This script:
- reads only data/annotations/difficulty_member_review_v0.2.json as the
  fixed 25-case calibration/review snapshot;
- calls the current project's extract_task_features() implementation;
- does not access GitHub or the network;
- does not modify source code, tests, databases, matching, or review data;
- writes data/reports/difficulty_member_review_replay_v0.2.1.json.

The resulting agreement statistics are calibration/consistency signals against
project-member review (AI-assisted). They are not accuracy estimates and the
reviewed subset is not representative of all eligible tasks.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from oss_mentor.task_features import (
    DIFFICULTY_FORMULA_VERSION,
    TASK_FEATURE_VERSION,
    extract_task_features,
)


EXPECTED_FORMULA_VERSION = "difficulty-rules-v0.2.1"
EXPECTED_SELECTED_COUNT = 25
EXPECTED_SCOREABLE_COUNT = 18
EXPECTED_INSUFFICIENT_COUNT = 7

EFFORT_ORDER = {
    "under_2h": 0,
    "half_day": 1,
    "one_day": 2,
    "multi_day": 3,
}

DIMENSIONS = (
    ("code", "revised_code", "estimated_code_difficulty"),
    ("setup", "revised_setup", "estimated_setup_difficulty"),
    (
        "project_context",
        "revised_project_context",
        "estimated_project_context_difficulty",
    ),
    (
        "collaboration",
        "revised_collaboration",
        "estimated_collaboration_difficulty",
    ),
)

# These are identities only, not expected/correct answers.
POSITIVE_CASE_IDENTITIES = (
    ("wagtail/wagtail", 14318),
    ("pytorch/ao", 988),
    ("pytorch/ao", 1224),
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = (
    PROJECT_ROOT / "data" / "annotations" / "difficulty_member_review_v0.2.json"
)
OUTPUT_PATH = (
    PROJECT_ROOT
    / "data"
    / "reports"
    / "difficulty_member_review_replay_v0.2.1.json"
)


class ReplayError(RuntimeError):
    """Fatal reproducibility/contract error."""


def _fatal(message: str) -> "NoReturn":
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def _require_mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReplayError(f"{path} must be an object")
    return value


def _require_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ReplayError(f"{path} must be an array")
    return value


def _require_keys(mapping: dict[str, Any], keys: Iterable[str], path: str) -> None:
    missing = [key for key in keys if key not in mapping]
    if missing:
        raise ReplayError(f"{path} is missing required fields: {', '.join(missing)}")


def _identity(record: dict[str, Any]) -> str:
    return (
        f"{record.get('repository', '<missing>')} "
        f"#{record.get('issue_number', '<missing>')} "
        f"(task_candidate_id={record.get('task_candidate_id', '<missing>')})"
    )


def _normalize_task_types(values: Any, path: str) -> tuple[str, ...]:
    items = _require_list(values, path)
    return tuple(str(item) for item in items)


def _prediction_from_snapshot(
    prediction: dict[str, Any],
    path: str,
) -> dict[str, Any]:
    _require_keys(
        prediction,
        ("code", "setup", "project_context", "collaboration", "effort"),
        path,
    )
    effort = str(prediction["effort"])
    if effort not in EFFORT_ORDER:
        raise ReplayError(f"{path}.effort has unsupported bucket: {effort}")
    return {
        "code": int(prediction["code"]),
        "setup": int(prediction["setup"]),
        "project_context": int(prediction["project_context"]),
        "collaboration": int(prediction["collaboration"]),
        "effort": effort,
        "task_feature_version": prediction.get("task_feature_version"),
    }


def _member_review_view(
    member_review: dict[str, Any],
    path: str,
) -> dict[str, Any]:
    _require_keys(
        member_review,
        (
            "review_status",
            "decision",
            "revised_code",
            "revised_setup",
            "revised_project_context",
            "revised_collaboration",
            "revised_effort",
            "reviewer",
            "review_comment",
            "review_date",
        ),
        path,
    )
    decision = str(member_review["decision"])
    if decision not in {
        "old_rules_more_reasonable",
        "new_rules_more_reasonable",
        "both_unreasonable",
        "insufficient_information",
    }:
        raise ReplayError(f"{path}.decision has unsupported value: {decision}")

    return {
        "review_status": member_review["review_status"],
        "decision": decision,
        "revised_code": member_review["revised_code"],
        "revised_setup": member_review["revised_setup"],
        "revised_project_context": member_review["revised_project_context"],
        "revised_collaboration": member_review["revised_collaboration"],
        "revised_effort": member_review["revised_effort"],
        "reviewer": member_review["reviewer"],
        "review_comment": member_review["review_comment"],
        "review_date": member_review["review_date"],
    }


def _is_scoreable(member_review: dict[str, Any]) -> bool:
    if member_review["decision"] == "insufficient_information":
        return False
    required = (
        "revised_code",
        "revised_setup",
        "revised_project_context",
        "revised_collaboration",
        "revised_effort",
    )
    return all(member_review.get(key) is not None for key in required)


def _direction(old: Any, new: Any, *, effort: bool = False) -> str:
    if effort:
        old_value = EFFORT_ORDER[str(old)]
        new_value = EFFORT_ORDER[str(new)]
    else:
        old_value = int(old)
        new_value = int(new)
    if new_value > old_value:
        return "up"
    if new_value < old_value:
        return "down"
    return "same"


def _dimension_statistics(
    records: list[dict[str, Any]],
    prediction_key: str,
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    total_abs = 0
    total_exact = 0
    total_cells = 0

    for dimension, revised_key, _feature_attr in DIMENSIONS:
        abs_differences: list[int] = []
        higher = 0
        lower = 0
        exact = 0

        for record in records:
            prediction = int(record[prediction_key][dimension])
            member = int(record["member_review"][revised_key])
            delta = prediction - member
            difference = abs(delta)
            abs_differences.append(difference)
            if delta > 0:
                higher += 1
            elif delta < 0:
                lower += 1
            else:
                exact += 1

        count = len(abs_differences)
        total_abs += sum(abs_differences)
        total_exact += exact
        total_cells += count
        output[dimension] = {
            "comparable_count": count,
            "exact_count": exact,
            "exact_rate": exact / count if count else None,
            "mean_absolute_difference": (
                sum(abs_differences) / count if count else None
            ),
            "prediction_higher_than_member_count": higher,
            "prediction_lower_than_member_count": lower,
            "absolute_difference_two_or_more_count": sum(
                difference >= 2 for difference in abs_differences
            ),
        }

    output["overall_dimensions"] = {
        "comparable_cells": total_cells,
        "exact_count": total_exact,
        "exact_rate": total_exact / total_cells if total_cells else None,
        "mean_absolute_difference": total_abs / total_cells if total_cells else None,
    }
    return output


def _effort_statistics(
    records: list[dict[str, Any]],
    prediction_key: str,
) -> dict[str, Any]:
    differences: list[int] = []
    higher = 0
    lower = 0
    exact = 0

    for record in records:
        predicted_bucket = str(record[prediction_key]["effort"])
        member_bucket = str(record["member_review"]["revised_effort"])
        if predicted_bucket not in EFFORT_ORDER:
            raise ReplayError(
                f"{record['repository']} #{record['issue_number']}: "
                f"unsupported predicted effort bucket {predicted_bucket}"
            )
        if member_bucket not in EFFORT_ORDER:
            raise ReplayError(
                f"{record['repository']} #{record['issue_number']}: "
                f"unsupported member effort bucket {member_bucket}"
            )

        delta = EFFORT_ORDER[predicted_bucket] - EFFORT_ORDER[member_bucket]
        differences.append(abs(delta))
        if delta > 0:
            higher += 1
        elif delta < 0:
            lower += 1
        else:
            exact += 1

    count = len(differences)
    return {
        "comparable_count": count,
        "exact_count": exact,
        "exact_rate": exact / count if count else None,
        "mean_absolute_bucket_difference": (
            sum(differences) / count if count else None
        ),
        "prediction_higher_than_member_count": higher,
        "prediction_lower_than_member_count": lower,
        "absolute_difference_two_or_more_count": sum(
            difference >= 2 for difference in differences
        ),
    }


def _replay_one(record: dict[str, Any], index: int) -> dict[str, Any]:
    path = f"records[{index}]"
    _require_keys(
        record,
        (
            "task_candidate_id",
            "repository",
            "issue_number",
            "title",
            "task_evidence",
            "old_rule_prediction",
            "new_rule_prediction",
            "member_review",
        ),
        path,
    )

    task_evidence = _require_mapping(record["task_evidence"], f"{path}.task_evidence")
    _require_keys(
        task_evidence,
        ("labels", "task_types", "body_excerpt", "comment_count"),
        f"{path}.task_evidence",
    )

    labels_raw = _require_list(task_evidence["labels"], f"{path}.task_evidence.labels")
    labels = [str(label) for label in labels_raw]
    expected_task_types = _normalize_task_types(
        task_evidence["task_types"],
        f"{path}.task_evidence.task_types",
    )
    body_excerpt = task_evidence["body_excerpt"]
    if body_excerpt is None:
        body_text = ""
    elif isinstance(body_excerpt, str):
        body_text = body_excerpt
    else:
        raise ReplayError(f"{path}.task_evidence.body_excerpt must be text or null")

    try:
        comment_count = int(task_evidence["comment_count"])
    except (TypeError, ValueError) as exc:
        raise ReplayError(
            f"{path}.task_evidence.comment_count must be an integer-compatible value"
        ) from exc

    # Only fields consumed by the real extraction pipeline are needed for replay.
    # Identity fields are included for traceability; extract_task_features ignores them.
    replay_input = {
        "task_candidate_id": record["task_candidate_id"],
        "repository": record["repository"],
        "issue_number": record["issue_number"],
        "title": str(record["title"]),
        "body_text": body_text,
        "labels": labels,
        "comment_count": comment_count,
    }

    try:
        features = extract_task_features(replay_input)
    except Exception as exc:  # fail the whole replay; partial output is misleading
        raise ReplayError(
            f"{_identity(record)} failed in extract_task_features(): "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    actual_task_types = tuple(str(value) for value in features.task_types)
    task_type_match = actual_task_types == expected_task_types

    assessment = _require_mapping(
        features.feature_evidence.get("difficulty_assessment"),
        f"{path}.replay.feature_evidence.difficulty_assessment",
    )
    _require_keys(
        assessment,
        ("formula_version", "information_quality", "dimensions", "effort"),
        f"{path}.replay.difficulty_assessment",
    )
    information_quality = _require_mapping(
        assessment["information_quality"],
        f"{path}.replay.difficulty_assessment.information_quality",
    )
    dimensions = _require_mapping(
        assessment["dimensions"],
        f"{path}.replay.difficulty_assessment.dimensions",
    )
    effort_evidence = _require_mapping(
        assessment["effort"],
        f"{path}.replay.difficulty_assessment.effort",
    )
    _require_keys(
        effort_evidence,
        ("bucket", "applicable", "provisional", "confidence", "evidence"),
        f"{path}.replay.difficulty_assessment.effort",
    )

    v0_2_prediction = _prediction_from_snapshot(
        _require_mapping(record["new_rule_prediction"], f"{path}.new_rule_prediction"),
        f"{path}.new_rule_prediction",
    )
    legacy_old_prediction = _prediction_from_snapshot(
        _require_mapping(record["old_rule_prediction"], f"{path}.old_rule_prediction"),
        f"{path}.old_rule_prediction",
    )
    member_review = _member_review_view(
        _require_mapping(record["member_review"], f"{path}.member_review"),
        f"{path}.member_review",
    )

    v0_2_1_prediction = {
        "code": int(features.estimated_code_difficulty),
        "setup": int(features.estimated_setup_difficulty),
        "project_context": int(features.estimated_project_context_difficulty),
        "collaboration": int(features.estimated_collaboration_difficulty),
        "effort": str(features.estimated_effort_bucket),
        "task_feature_version": str(features.task_feature_version),
        "difficulty_formula_version": str(assessment["formula_version"]),
    }

    if v0_2_1_prediction["effort"] not in EFFORT_ORDER:
        raise ReplayError(
            f"{_identity(record)} produced unsupported effort bucket "
            f"{v0_2_1_prediction['effort']}"
        )

    actual_direction = {
        "code": _direction(
            v0_2_prediction["code"], v0_2_1_prediction["code"]
        ),
        "setup": _direction(
            v0_2_prediction["setup"], v0_2_1_prediction["setup"]
        ),
        "project_context": _direction(
            v0_2_prediction["project_context"],
            v0_2_1_prediction["project_context"],
        ),
        "collaboration": _direction(
            v0_2_prediction["collaboration"],
            v0_2_1_prediction["collaboration"],
        ),
        "effort": _direction(
            v0_2_prediction["effort"],
            v0_2_1_prediction["effort"],
            effort=True,
        ),
    }

    return {
        "task_candidate_id": record["task_candidate_id"],
        "repository": str(record["repository"]),
        "issue_number": record["issue_number"],
        "title": str(record["title"]),
        "snapshot": {
            "labels": labels,
            "task_types": list(expected_task_types),
            "comment_count": comment_count,
            "body_excerpt": body_text,
        },
        # Stored for provenance only. This predates difficulty-rules-v0.2.
        "legacy_old_rule_prediction": legacy_old_prediction,
        # In the review artifact, new_rule_prediction is the fixed v0.2 prediction.
        "v0_2_prediction": v0_2_prediction,
        "v0_2_1_replay": v0_2_1_prediction,
        "member_review": member_review,
        "difficulty_assessment": {
            "formula_version": assessment["formula_version"],
            "information_quality": information_quality,
            "dimensions": dimensions,
            "effort": effort_evidence,
            "effort_applicable": bool(effort_evidence["applicable"]),
            "effort_provisional": bool(effort_evidence["provisional"]),
            "effort_confidence": str(effort_evidence["confidence"]),
        },
        "task_type_match": task_type_match,
        "expected_task_types": list(expected_task_types),
        "actual_task_types": list(actual_task_types),
        "actual_direction_from_v0_2": actual_direction,
    }


def _insufficient_check(record: dict[str, Any]) -> dict[str, Any]:
    assessment = record["difficulty_assessment"]
    quality = assessment["information_quality"]
    effort = assessment["effort"]
    actionability = str(quality.get("actionability"))
    body_missing = bool(quality.get("body_missing"))
    confidence = str(quality.get("confidence"))
    applicable = bool(effort.get("applicable"))
    provisional = bool(effort.get("provisional"))
    effort_confidence = str(effort.get("confidence"))

    checks: dict[str, Any] = {
        "body_missing_confidence_low": (
            confidence == "low" if body_missing else None
        ),
        "non_actionable_contract_ok": (
            (not applicable and provisional)
            if actionability == "non_actionable"
            else None
        ),
        # For unclear/design_pending cases this is an observation only.
        # Whether an implementation boundary is truly absent remains a manual check.
        "unclear_or_design_pending_provisional": (
            provisional
            if actionability in {"unclear", "design_pending"}
            else None
        ),
        "manual_boundary_review_required": actionability
        in {"unclear", "design_pending"},
    }

    return {
        "repository": record["repository"],
        "issue_number": record["issue_number"],
        "information_confidence": confidence,
        "body_missing": body_missing,
        "actionability": actionability,
        "effort_applicable": applicable,
        "effort_provisional": provisional,
        "effort_confidence": effort_confidence,
        "checks": checks,
    }


def _positive_case_views(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_identity = {
        (record["repository"], int(record["issue_number"])): record
        for record in records
    }
    output: list[dict[str, Any]] = []
    for identity in POSITIVE_CASE_IDENTITIES:
        if identity not in by_identity:
            raise ReplayError(
                f"positive-case identity missing from fixed snapshot: "
                f"{identity[0]} #{identity[1]}"
            )
        record = by_identity[identity]
        output.append(
            {
                "repository": record["repository"],
                "issue_number": record["issue_number"],
                "v0_2_prediction": record["v0_2_prediction"],
                "v0_2_1_replay": record["v0_2_1_replay"],
                "member_review": record["member_review"],
                "actual_direction_from_v0_2": record[
                    "actual_direction_from_v0_2"
                ],
            }
        )
    return output


def main() -> int:
    if DIFFICULTY_FORMULA_VERSION != EXPECTED_FORMULA_VERSION:
        _fatal(
            "DIFFICULTY_FORMULA_VERSION mismatch: "
            f"expected {EXPECTED_FORMULA_VERSION}, "
            f"got {DIFFICULTY_FORMULA_VERSION}"
        )

    if not INPUT_PATH.is_file():
        _fatal(f"input file does not exist: {INPUT_PATH}")

    try:
        with INPUT_PATH.open("r", encoding="utf-8") as handle:
            source = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        _fatal(f"failed to read input JSON: {type(exc).__name__}: {exc}")

    try:
        source = _require_mapping(source, "root")
        _require_keys(source, ("record_count", "records"), "root")
        records_raw = _require_list(source["records"], "root.records")

        declared_count = int(source["record_count"])
        if declared_count != EXPECTED_SELECTED_COUNT:
            raise ReplayError(
                f"root.record_count must be {EXPECTED_SELECTED_COUNT}, "
                f"got {declared_count}"
            )
        if len(records_raw) != EXPECTED_SELECTED_COUNT:
            raise ReplayError(
                f"root.records must contain {EXPECTED_SELECTED_COUNT} records, "
                f"got {len(records_raw)}"
            )

        replayed: list[dict[str, Any]] = []
        task_type_mismatches: list[dict[str, Any]] = []

        for index, raw_record in enumerate(records_raw):
            record = _require_mapping(raw_record, f"records[{index}]")
            replay_record = _replay_one(record, index)
            replayed.append(replay_record)
            if not replay_record["task_type_match"]:
                task_type_mismatches.append(
                    {
                        "repository": replay_record["repository"],
                        "issue_number": replay_record["issue_number"],
                        "task_candidate_id": replay_record["task_candidate_id"],
                        "expected_task_types": replay_record[
                            "expected_task_types"
                        ],
                        "actual_task_types": replay_record["actual_task_types"],
                    }
                )

        scoreable = [
            record
            for record in replayed
            if _is_scoreable(record["member_review"])
        ]
        insufficient = [
            record
            for record in replayed
            if record["member_review"]["decision"] == "insufficient_information"
        ]

        if len(scoreable) != EXPECTED_SCOREABLE_COUNT:
            raise ReplayError(
                f"scoreable_count must be {EXPECTED_SCOREABLE_COUNT}, "
                f"got {len(scoreable)}"
            )
        if len(insufficient) != EXPECTED_INSUFFICIENT_COUNT:
            raise ReplayError(
                f"insufficient_information_count must be "
                f"{EXPECTED_INSUFFICIENT_COUNT}, got {len(insufficient)}"
            )

        # Scoreable records must have complete revised values.
        for record in scoreable:
            review = record["member_review"]
            missing_values = [
                key
                for key in (
                    "revised_code",
                    "revised_setup",
                    "revised_project_context",
                    "revised_collaboration",
                    "revised_effort",
                )
                if review.get(key) is None
            ]
            if missing_values:
                raise ReplayError(
                    f"{record['repository']} #{record['issue_number']}: "
                    f"scoreable review is missing values: {missing_values}"
                )
            if str(review["revised_effort"]) not in EFFORT_ORDER:
                raise ReplayError(
                    f"{record['repository']} #{record['issue_number']}: "
                    f"unsupported revised effort {review['revised_effort']}"
                )

        v0_2_dimensions = _dimension_statistics(scoreable, "v0_2_prediction")
        v0_2_effort = _effort_statistics(scoreable, "v0_2_prediction")
        v0_2_1_dimensions = _dimension_statistics(scoreable, "v0_2_1_replay")
        v0_2_1_effort = _effort_statistics(scoreable, "v0_2_1_replay")

        insufficient_checks = [
            _insufficient_check(record) for record in insufficient
        ]

        output = {
            "schema_version": "difficulty_member_review_replay_v0.2.1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "methodological_status": {
                "review_type": "project-member review with AI-assisted explanation",
                "not_independent_blind_annotation": True,
                "not_gold_standard": True,
                "not_representative_of_all_608": True,
                "accuracy_claim_permitted": False,
                "interpretation": (
                    "Statistics describe consistency with project-member "
                    "reviewed judgments on a deliberately selected high-risk "
                    "subset. They are calibration signals, not accuracy estimates."
                ),
            },
            "runtime": {
                "task_feature_version": TASK_FEATURE_VERSION,
                "difficulty_formula_version": DIFFICULTY_FORMULA_VERSION,
                "input_path": str(INPUT_PATH.relative_to(PROJECT_ROOT)),
                "output_path": str(OUTPUT_PATH.relative_to(PROJECT_ROOT)),
                "network_access_used": False,
            },
            "counts": {
                "selected_records": len(replayed),
                "successfully_replayed": len(replayed),
                "scoreable_records": len(scoreable),
                "insufficient_information_records": len(insufficient),
                "task_type_match_count": len(replayed)
                - len(task_type_mismatches),
                "task_type_mismatch_count": len(task_type_mismatches),
            },
            "task_type_mismatches": task_type_mismatches,
            "consistency_with_member_review": {
                "v0_2": {
                    "dimensions": {
                        key: value
                        for key, value in v0_2_dimensions.items()
                        if key != "overall_dimensions"
                    },
                    "overall_dimensions": v0_2_dimensions[
                        "overall_dimensions"
                    ],
                    "effort": v0_2_effort,
                },
                "v0_2_1": {
                    "dimensions": {
                        key: value
                        for key, value in v0_2_1_dimensions.items()
                        if key != "overall_dimensions"
                    },
                    "overall_dimensions": v0_2_1_dimensions[
                        "overall_dimensions"
                    ],
                    "effort": v0_2_1_effort,
                },
            },
            "insufficient_information_checks": insufficient_checks,
            "positive_case_views": _positive_case_views(replayed),
            "scoreable_actual_directions_from_v0_2": [
                {
                    "repository": record["repository"],
                    "issue_number": record["issue_number"],
                    "actual_direction": record["actual_direction_from_v0_2"],
                }
                for record in scoreable
            ],
            "records": replayed,
        }

        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            serialized = json.dumps(
                output,
                ensure_ascii=False,
                indent=2,
                sort_keys=False,
            )
        except (TypeError, ValueError) as exc:
            raise ReplayError(
                f"output JSON serialization failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        try:
            OUTPUT_PATH.write_text(serialized + "\n", encoding="utf-8")
        except OSError as exc:
            raise ReplayError(
                f"failed to write output JSON: {type(exc).__name__}: {exc}"
            ) from exc

    except ReplayError as exc:
        _fatal(str(exc))

    if task_type_mismatches:
        print(
            f"WARNING: task type mismatches: "
            f"{len(task_type_mismatches)}/{len(replayed)}"
        )
        for item in task_type_mismatches:
            print(
                "  - "
                f"{item['repository']} #{item['issue_number']}: "
                f"expected={item['expected_task_types']} "
                f"actual={item['actual_task_types']}"
            )

    v02_overall = output["consistency_with_member_review"]["v0_2"][
        "overall_dimensions"
    ]
    v021_overall = output["consistency_with_member_review"]["v0_2_1"][
        "overall_dimensions"
    ]
    v02_effort = output["consistency_with_member_review"]["v0_2"]["effort"]
    v021_effort = output["consistency_with_member_review"]["v0_2_1"]["effort"]

    print()
    print("B3 difficulty replay v0.2.1")
    print()
    print("Formula:")
    print(DIFFICULTY_FORMULA_VERSION)
    print()
    print("Task feature version:")
    print(TASK_FEATURE_VERSION)
    print()
    print("Selected records:")
    print(len(replayed))
    print()
    print("Successfully replayed:")
    print(len(replayed))
    print()
    print("Scoreable:")
    print(len(scoreable))
    print()
    print("Insufficient information:")
    print(len(insufficient))
    print()
    print("Task type matches:")
    print(f"{len(replayed) - len(task_type_mismatches)}/{len(replayed)}")
    print()
    print("v0.2 overall consistency:")
    print(
        f"exact = {v02_overall['exact_count']}/"
        f"{v02_overall['comparable_cells']} "
        f"({v02_overall['exact_rate']:.1%})"
    )
    print(f"MAD = {v02_overall['mean_absolute_difference']:.6f}")
    print()
    print("v0.2.1 overall consistency:")
    print(
        f"exact = {v021_overall['exact_count']}/"
        f"{v021_overall['comparable_cells']} "
        f"({v021_overall['exact_rate']:.1%})"
    )
    print(f"MAD = {v021_overall['mean_absolute_difference']:.6f}")
    print()
    print("v0.2 effort consistency:")
    print(
        f"exact = {v02_effort['exact_count']}/"
        f"{v02_effort['comparable_count']} "
        f"({v02_effort['exact_rate']:.1%})"
    )
    print(
        f"MAD = "
        f"{v02_effort['mean_absolute_bucket_difference']:.6f}"
    )
    print()
    print("v0.2.1 effort consistency:")
    print(
        f"exact = {v021_effort['exact_count']}/"
        f"{v021_effort['comparable_count']} "
        f"({v021_effort['exact_rate']:.1%})"
    )
    print(
        f"MAD = "
        f"{v021_effort['mean_absolute_bucket_difference']:.6f}"
    )
    print()
    print("Output:")
    print(str(OUTPUT_PATH.relative_to(PROJECT_ROOT)))
    print()
    print(
        "Note: these are consistency/calibration statistics, "
        "not accuracy estimates."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())