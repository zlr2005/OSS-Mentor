"""Offline evaluation for annotated OSS-Mentor recommendation rankings."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean
from typing import Any

from oss_mentor.matching import (
    MATCH_VERSION_V1,
    MATCH_VERSION_V2,
    MatchResult,
    rank_for_profile,
)


ANNOTATION_FIELDS = (
    "repository",
    "issue_number",
    "newcomer_fit",
    "growth_fit",
    "code_difficulty",
    "setup_difficulty",
    "clarity",
    "required_skills",
    "critical_blocker",
    "annotation_reason",
    "annotator",
)
RANKING_EVALUATION_SCHEMA_VERSION = "ranking_evaluation_v0.2"
FIT_THRESHOLD = 2.0


@dataclass(frozen=True, slots=True)
class TaskFitAnnotation:
    repository: str
    issue_number: int
    newcomer_fit: int
    growth_fit: int
    code_difficulty: int
    setup_difficulty: int
    clarity: int
    required_skills: tuple[str, ...]
    critical_blocker: bool
    annotation_reason: str
    annotator: str


def _parse_score(raw: str | None, *, field: str, row_number: int) -> int:
    try:
        value = int(str(raw or "").strip())
    except ValueError as exc:
        raise ValueError(f"row {row_number}: {field} must be an integer from 0 to 3") from exc
    if value < 0 or value > 3:
        raise ValueError(f"row {row_number}: {field} must be from 0 to 3")
    return value


def _parse_bool(raw: str | None, *, field: str, row_number: int) -> bool:
    value = str(raw or "").strip().casefold()
    if value in {"1", "true", "yes", "y"}:
        return True
    if value in {"0", "false", "no", "n", ""}:
        return False
    raise ValueError(f"row {row_number}: {field} must be true/false or 1/0")


def _parse_required_skills(raw: str | None) -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in str(raw or "").replace(";", ",").split(",")
        if item.strip()
    )


def load_task_fit_annotations(path: Path) -> list[TaskFitAnnotation]:
    if not path.is_file():
        raise ValueError(f"annotation file does not exist: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = tuple(reader.fieldnames or ())
        missing = [field for field in ANNOTATION_FIELDS if field not in fields]
        if missing:
            raise ValueError("annotation file is missing fields: " + ", ".join(missing))

        annotations: list[TaskFitAnnotation] = []
        seen: set[tuple[str, int, str]] = set()
        for row_number, row in enumerate(reader, start=2):
            repository = str(row.get("repository") or "").strip()
            if not repository:
                raise ValueError(f"row {row_number}: repository is required")
            try:
                issue_number = int(str(row.get("issue_number") or "").strip())
            except ValueError as exc:
                raise ValueError(f"row {row_number}: issue_number must be an integer") from exc
            annotator = str(row.get("annotator") or "").strip()
            if not annotator:
                raise ValueError(f"row {row_number}: annotator is required")
            key = (repository.casefold(), issue_number, annotator.casefold())
            if key in seen:
                raise ValueError(
                    f"row {row_number}: duplicate annotation for {repository}#{issue_number} by {annotator}"
                )
            seen.add(key)
            annotations.append(
                TaskFitAnnotation(
                    repository=repository,
                    issue_number=issue_number,
                    newcomer_fit=_parse_score(
                        row.get("newcomer_fit"),
                        field="newcomer_fit",
                        row_number=row_number,
                    ),
                    growth_fit=_parse_score(
                        row.get("growth_fit"),
                        field="growth_fit",
                        row_number=row_number,
                    ),
                    code_difficulty=_parse_score(
                        row.get("code_difficulty"),
                        field="code_difficulty",
                        row_number=row_number,
                    ),
                    setup_difficulty=_parse_score(
                        row.get("setup_difficulty"),
                        field="setup_difficulty",
                        row_number=row_number,
                    ),
                    clarity=_parse_score(
                        row.get("clarity"),
                        field="clarity",
                        row_number=row_number,
                    ),
                    required_skills=_parse_required_skills(row.get("required_skills")),
                    critical_blocker=_parse_bool(
                        row.get("critical_blocker"),
                        field="critical_blocker",
                        row_number=row_number,
                    ),
                    annotation_reason=str(row.get("annotation_reason") or "").strip(),
                    annotator=annotator,
                )
            )
    return annotations


def _annotation_groups(
    annotations: list[TaskFitAnnotation],
) -> dict[tuple[str, int], list[TaskFitAnnotation]]:
    grouped: dict[tuple[str, int], list[TaskFitAnnotation]] = {}
    for annotation in annotations:
        grouped.setdefault(
            (annotation.repository.casefold(), annotation.issue_number), []
        ).append(annotation)
    return grouped


def _task_key(result: MatchResult | dict[str, Any]) -> tuple[str, int]:
    if isinstance(result, MatchResult):
        return (result.repository.casefold(), result.issue_number)
    return (str(result["repository"]).casefold(), int(result["issue_number"]))


def _fit_score(
    annotations: dict[tuple[str, int], list[TaskFitAnnotation]],
    key: tuple[str, int],
    track: str,
) -> float | None:
    rows = annotations.get(key)
    if not rows:
        return None
    field = "newcomer_fit" if track == "newcomer" else "growth_fit"
    return mean(getattr(row, field) for row in rows)


def _is_relevant(
    annotations: dict[tuple[str, int], list[TaskFitAnnotation]],
    key: tuple[str, int],
    track: str,
) -> bool | None:
    rows = annotations.get(key)
    if not rows:
        return None
    if any(row.critical_blocker for row in rows):
        return False
    score = _fit_score(annotations, key, track)
    return score is not None and score >= FIT_THRESHOLD


def _precision_at(
    ranking: list[MatchResult],
    annotations: dict[tuple[str, int], list[TaskFitAnnotation]],
    track: str,
    k: int,
) -> float:
    window = ranking[:k]
    if not window:
        return 0.0
    relevant = sum(
        1
        for result in window
        if _is_relevant(annotations, _task_key(result), track) is True
    )
    return round(relevant / len(window), 3)


def _task_types_for_result(
    result: MatchResult, candidates_by_key: dict[tuple[str, int], dict[str, Any]]
) -> tuple[str, ...]:
    candidate = candidates_by_key.get(_task_key(result), {})
    return tuple(str(value) for value in candidate.get("task_types") or ())


def _ranking_metrics(
    ranking: list[MatchResult],
    *,
    annotations: dict[tuple[str, int], list[TaskFitAnnotation]],
    candidates_by_key: dict[tuple[str, int], dict[str, Any]],
    track: str,
) -> dict[str, Any]:
    top10 = ranking[:10]
    annotated_top10 = [
        result for result in top10 if _task_key(result) in annotations
    ]
    critical_skill_mismatches = 0
    platform_mismatches = 0
    for result in annotated_top10:
        for gap in result.skill_gaps:
            if int(gap["gap"]) <= 0 or float(gap["importance"]) < 1.0:
                continue
            skill = str(gap["skill"])
            if skill.casefold().startswith("platform:"):
                platform_mismatches += 1
            else:
                critical_skill_mismatches += 1
    task_types = {
        task_type
        for result in top10
        for task_type in _task_types_for_result(result, candidates_by_key)
    }
    repositories = {result.repository for result in top10}
    denominator = max(len(annotated_top10), 1)
    return {
        "recommendation_count": len(ranking),
        "empty_recommendation_rate": 1.0 if not ranking else 0.0,
        "precision_at_5": _precision_at(ranking, annotations, track, 5),
        "precision_at_10": _precision_at(ranking, annotations, track, 10),
        "critical_skill_mismatch_rate": round(
            critical_skill_mismatches / denominator, 3
        ),
        "platform_mismatch_rate": round(platform_mismatches / denominator, 3),
        "basic_skill_coverage_rate": round(
            mean(result.skill_coverage for result in top10) if top10 else 0.0,
            3,
        ),
        "task_type_diversity": len(task_types),
        "repository_diversity": len(repositories),
    }


def _top10_changes(
    baseline: list[MatchResult],
    candidate: list[MatchResult],
) -> dict[str, Any]:
    baseline_keys = [_task_key(result) for result in baseline[:10]]
    candidate_keys = [_task_key(result) for result in candidate[:10]]
    baseline_set = set(baseline_keys)
    candidate_set = set(candidate_keys)
    moved = []
    for key in sorted(baseline_set.intersection(candidate_set)):
        old_rank = baseline_keys.index(key) + 1
        new_rank = candidate_keys.index(key) + 1
        if old_rank != new_rank:
            moved.append(
                {
                    "repository": key[0],
                    "issue_number": key[1],
                    "from_rank": old_rank,
                    "to_rank": new_rank,
                }
            )
    return {
        "added": [
            {"repository": key[0], "issue_number": key[1]}
            for key in candidate_keys
            if key not in baseline_set
        ],
        "removed": [
            {"repository": key[0], "issue_number": key[1]}
            for key in baseline_keys
            if key not in candidate_set
        ],
        "moved": moved,
    }


def _agreement_summary(
    annotations: dict[tuple[str, int], list[TaskFitAnnotation]],
    track: str,
) -> dict[str, Any]:
    double_annotated = {
        key: rows for key, rows in annotations.items() if len(rows) >= 2
    }
    disagreements = 0
    field = "newcomer_fit" if track == "newcomer" else "growth_fit"
    for rows in double_annotated.values():
        scores = [getattr(row, field) for row in rows]
        if max(scores) - min(scores) > 1:
            disagreements += 1
    return {
        "double_annotated_task_count": len(double_annotated),
        "large_disagreement_count": disagreements,
        "large_disagreement_rate": round(
            disagreements / len(double_annotated), 3
        )
        if double_annotated
        else 0.0,
    }


def build_ranking_evaluation_report(
    *,
    track: str,
    profile: dict[str, Any],
    candidates: list[dict[str, Any]],
    annotations: list[TaskFitAnnotation],
    limit: int = 50,
    selected_match_version: str = MATCH_VERSION_V2,
) -> dict[str, Any]:
    if track not in {"newcomer", "growth"}:
        raise ValueError(f"unsupported track: {track}")
    if selected_match_version not in {MATCH_VERSION_V1, MATCH_VERSION_V2}:
        raise ValueError(f"unsupported match version: {selected_match_version}")

    grouped_annotations = _annotation_groups(annotations)
    candidates_by_key = {_task_key(candidate): candidate for candidate in candidates}
    rankings = {
        MATCH_VERSION_V1: rank_for_profile(
            profile, candidates, limit=limit, match_version=MATCH_VERSION_V1
        ),
        MATCH_VERSION_V2: rank_for_profile(
            profile, candidates, limit=limit, match_version=MATCH_VERSION_V2
        ),
    }
    warnings: list[dict[str, Any]] = []
    for key in sorted(grouped_annotations):
        if key not in candidates_by_key:
            warnings.append(
                {
                    "type": "annotation_without_candidate",
                    "repository": key[0],
                    "issue_number": key[1],
                }
            )
    for version, ranking in rankings.items():
        for result in ranking[:10]:
            key = _task_key(result)
            if key not in grouped_annotations:
                warnings.append(
                    {
                        "type": "recommendation_without_annotation",
                        "match_version": version,
                        "repository": result.repository,
                        "issue_number": result.issue_number,
                    }
                )
    metrics = {
        version: _ranking_metrics(
            ranking,
            annotations=grouped_annotations,
            candidates_by_key=candidates_by_key,
            track=track,
        )
        for version, ranking in rankings.items()
    }
    selected_ranking = rankings[selected_match_version]
    return {
        "schema_version": RANKING_EVALUATION_SCHEMA_VERSION,
        "track": track,
        "profile_key": profile.get("profile_key"),
        "selected_match_version": selected_match_version,
        "annotation_summary": {
            "row_count": len(annotations),
            "task_count": len(grouped_annotations),
            "annotator_count": len({item.annotator for item in annotations}),
            "agreement": _agreement_summary(grouped_annotations, track),
        },
        "candidate_count": len(candidates),
        "metrics_by_version": metrics,
        "top10_changes": _top10_changes(
            rankings[MATCH_VERSION_V1], rankings[MATCH_VERSION_V2]
        ),
        "selected_top10": [
            {
                **asdict(result),
                "skill_gaps": list(result.skill_gaps),
                "reasons": list(result.reasons),
                "annotation_fit": _fit_score(grouped_annotations, _task_key(result), track),
            }
            for result in selected_ranking[:10]
        ],
        "weight_change_rationale": [
            "v0.2 raises the skill-coverage contribution to reduce critical skill misses.",
            "v0.2 keeps explicit platform requirements as hard filters.",
            "v0.2 applies repository-balanced top-k selection to reduce concentration.",
        ],
        "warnings": warnings,
        "limitations": [
            "The report uses manually annotated samples and does not train a model.",
            "Small annotation sets can make diversity and precision metrics unstable.",
            "Feedback events are summarized separately and are not used for automatic reranking.",
        ],
    }


def render_ranking_evaluation_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# OSS-Mentor 推荐算法离线评估 v0.2",
        "",
        "## 概览",
        "",
        f"- 通道：`{report['track']}`",
        f"- 画像：`{report.get('profile_key') or 'transient'}`",
        f"- 当前评估版本：`{report['selected_match_version']}`",
        f"- 标注行数：{report['annotation_summary']['row_count']}",
        f"- 标注任务数：{report['annotation_summary']['task_count']}",
        f"- 候选任务数：{report['candidate_count']}",
        "",
        "## 指标对比",
        "",
        "| 版本 | P@5 | P@10 | 关键技能不匹配率 | 平台不匹配率 | 基础技能覆盖率 | 任务类型多样性 | 仓库多样性 | 空结果率 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for version, metrics in report["metrics_by_version"].items():
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{version}`",
                    f"{metrics['precision_at_5']:.3f}",
                    f"{metrics['precision_at_10']:.3f}",
                    f"{metrics['critical_skill_mismatch_rate']:.3f}",
                    f"{metrics['platform_mismatch_rate']:.3f}",
                    f"{metrics['basic_skill_coverage_rate']:.3f}",
                    str(metrics["task_type_diversity"]),
                    str(metrics["repository_diversity"]),
                    f"{metrics['empty_recommendation_rate']:.3f}",
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Top 10 变化",
            "",
            f"- 新增：{len(report['top10_changes']['added'])}",
            f"- 移出：{len(report['top10_changes']['removed'])}",
            f"- 排名移动：{len(report['top10_changes']['moved'])}",
            "",
            "## 当前 Top 10",
            "",
            "| 排名 | 任务 | 分数 | 标注适配 | 主要原因 |",
            "|---:|---|---:|---:|---|",
        ]
    )
    for index, item in enumerate(report["selected_top10"], start=1):
        fit = item["annotation_fit"]
        lines.append(
            f"| {index} | `{item['repository']}#{item['issue_number']}` | "
            f"{item['match_score']:.2f} | "
            f"{fit:.2f}" if fit is not None else f"| {index} | `{item['repository']}#{item['issue_number']}` | {item['match_score']:.2f} | -"
        )
        if lines[-1].endswith("-"):
            lines[-1] += f" | {', '.join(item['reasons'])} |"
        else:
            lines[-1] += f" | {', '.join(item['reasons'])} |"
    lines.extend(["", "## 权重变化依据", ""])
    lines.extend(f"- {item}" for item in report["weight_change_rationale"])
    if report["warnings"]:
        lines.extend(["", "## 警告", ""])
        lines.extend(
            f"- `{item['type']}`: {item.get('repository')}#{item.get('issue_number')}"
            for item in report["warnings"]
        )
    lines.extend(["", "## 限制", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines) + "\n"
