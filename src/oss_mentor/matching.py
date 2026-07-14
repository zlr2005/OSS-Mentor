"""Explainable developer-to-task matching for newcomer and growth tracks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


MATCH_VERSION = "developer-task-match-v0.1"


@dataclass(frozen=True, slots=True)
class MatchResult:
    task_candidate_id: int
    repository: str
    issue_number: int
    title: str
    html_url: str
    track: str
    match_score: float
    skill_coverage: float
    maximum_skill_gap: int
    skill_gaps: tuple[dict[str, Any], ...]
    reasons: tuple[str, ...]
    match_version: str = MATCH_VERSION


def _platform_level(profile: dict[str, Any], skill_name: str) -> int | None:
    if not skill_name.casefold().startswith("platform:"):
        return None
    platform = skill_name.split(":", maxsplit=1)[1].casefold()
    return 1 if platform in set(profile["operating_systems"]) else 0


def match_candidate(profile: dict[str, Any], task: dict[str, Any]) -> MatchResult | None:
    effective_track = "newcomer" if profile["service_track"] == "newcomer" else "growth"
    if effective_track == "newcomer" and not bool(task["newcomer_label_signal"]):
        return None
    if int(task["estimated_code_difficulty"]) > int(profile["max_code_difficulty"]):
        return None
    if int(task["estimated_setup_difficulty"]) > int(profile["max_setup_difficulty"]):
        return None

    gaps: list[dict[str, Any]] = []
    weighted_covered = 0.0
    total_importance = 0.0
    critical_mismatch = False
    for requirement in task["requirements"]:
        name = str(requirement["skill_name"])
        needed = int(requirement["minimum_level"])
        importance = float(requirement["importance"])
        platform_level = _platform_level(profile, name)
        actual = platform_level if platform_level is not None else int(
            profile["skills"].get(name.casefold(), 0)
        )
        gap = max(needed - actual, 0)
        total_importance += importance
        weighted_covered += importance * (1.0 if gap == 0 else max(0.0, 1 - gap / 3))
        gaps.append(
            {
                "skill": name,
                "required_level": needed,
                "developer_level": actual,
                "gap": gap,
                "importance": importance,
            }
        )
        if importance >= 1.0 and gap > 0:
            if platform_level is not None or effective_track == "newcomer" or gap > 1:
                critical_mismatch = True
    if critical_mismatch:
        return None

    coverage = weighted_covered / total_importance if total_importance else 1.0
    maximum_gap = max((item["gap"] for item in gaps), default=0)
    preferred_languages = {value.casefold() for value in profile["preferred_languages"]}
    language_match = not task.get("primary_language") or str(
        task["primary_language"]
    ).casefold() in preferred_languages
    preferred_types = {value.casefold() for value in profile["preferred_task_types"]}
    type_overlap = bool(
        preferred_types.intersection(value.casefold() for value in task["task_types"])
    )
    preference_score = (5 if language_match else 0) + (5 if type_overlap else 0)
    reasons = [f"skill_coverage={coverage:.2f}"]
    if language_match:
        reasons.append("preferred_language")
    if type_overlap:
        reasons.append("preferred_task_type")

    if effective_track == "newcomer":
        base = float(task["newcomer_score"] or 0)
        score = base * 0.60 + coverage * 30 + preference_score
        reasons.append("newcomer_label_required")
    else:
        base = float(task["growth_value_score"] or 0)
        desired = int(profile["desired_skill_stretch"])
        stretch_score = max(0.0, 1.0 - abs(maximum_gap - desired) / 2.0) * 20
        score = base * 0.50 + coverage * 20 + stretch_score + preference_score
        reasons.append(f"stretch_target={desired}")
    return MatchResult(
        task_candidate_id=int(task["task_candidate_id"]),
        repository=str(task["repository"]),
        issue_number=int(task["issue_number"]),
        title=str(task["title"]),
        html_url=str(task["html_url"]),
        track=effective_track,
        match_score=round(min(score, 100.0), 2),
        skill_coverage=round(coverage, 3),
        maximum_skill_gap=maximum_gap,
        skill_gaps=tuple(gaps),
        reasons=tuple(reasons),
    )


def rank_for_profile(
    profile: dict[str, Any], tasks: list[dict[str, Any]], *, limit: int = 20
) -> list[MatchResult]:
    results = [result for task in tasks if (result := match_candidate(profile, task))]
    return sorted(results, key=lambda item: (-item.match_score, item.repository, item.issue_number))[
        :limit
    ]
