"""Explainable developer-to-task matching for newcomer and growth tracks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


MATCH_VERSION_V1 = "developer-task-match-v0.1"
MATCH_VERSION_V2 = "developer-task-match-v0.2"
MATCH_VERSION_V3 = "developer-task-match-v0.3"
MATCH_VERSION = MATCH_VERSION_V1
SUPPORTED_MATCH_VERSIONS = (MATCH_VERSION_V1, MATCH_VERSION_V2, MATCH_VERSION_V3)


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
    score_components: tuple[dict[str, Any], ...] = ()
    match_version: str = MATCH_VERSION


def _platform_level(profile: dict[str, Any], skill_name: str) -> int | None:
    if not skill_name.casefold().startswith("platform:"):
        return None
    platform = skill_name.split(":", maxsplit=1)[1].casefold()
    return 1 if platform in set(profile["operating_systems"]) else 0


def _freshness(task: dict[str, Any]) -> float:
    """Return deterministic issue freshness at the candidate verification time."""
    activity = task.get("last_activity_at") or task.get("created_at")
    verified = task.get("github_verified_at") or task.get("source_fetched_at")
    if not activity or not verified:
        return 0.0
    try:
        activity_at = datetime.fromisoformat(str(activity).replace("Z", "+00:00"))
        verified_at = datetime.fromisoformat(str(verified).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    age_days = max((verified_at - activity_at).total_seconds() / 86400.0, 0.0)
    if age_days <= 30:
        return 1.0
    if age_days <= 90:
        return 0.7
    if age_days <= 180:
        return 0.4
    return 0.1


def _repository_activity(task: dict[str, Any]) -> float:
    status = str(task.get("maintenance_status") or "unknown").casefold()
    if status == "active":
        return 1.0
    if status == "unknown":
        return 0.5
    return 0.0


def _component(code: str, value: float, evidence: str) -> dict[str, Any]:
    return {"code": code, "score_delta": round(value, 4), "evidence": evidence}


def match_candidate(
    profile: dict[str, Any],
    task: dict[str, Any],
    *,
    match_version: str = MATCH_VERSION,
) -> MatchResult | None:
    if match_version not in SUPPORTED_MATCH_VERSIONS:
        raise ValueError(f"unsupported match version: {match_version}")
    effective_track = "newcomer" if profile["service_track"] == "newcomer" else "growth"
    if (
        match_version == MATCH_VERSION_V3
        and task.get("candidate_availability", "available") != "available"
    ):
        return None
    if effective_track == "newcomer" and not bool(task["newcomer_label_signal"]):
        return None
    if int(task["estimated_code_difficulty"]) > int(profile["max_code_difficulty"]):
        return None
    if int(task["estimated_setup_difficulty"]) > int(profile["max_setup_difficulty"]):
        return None
    preferred_languages = {
        value.casefold() for value in profile["preferred_languages"]
    }
    task_language = str(task.get("primary_language") or "").casefold()
    if preferred_languages and task_language not in preferred_languages:
        return None
    preferred_types = {
        value.casefold() for value in profile["preferred_task_types"]
    }
    task_types = {value.casefold() for value in task["task_types"]}
    if preferred_types and not preferred_types.intersection(task_types):
        return None
    supported_operating_systems = {
        str(value).casefold() for value in task.get("operating_systems", ())
    }
    profile_operating_systems = {
        str(value).casefold() for value in profile["operating_systems"]
    }
    if (
        match_version == MATCH_VERSION_V3
        and supported_operating_systems
        and profile_operating_systems
        and not supported_operating_systems.intersection(profile_operating_systems)
    ):
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
            is_primary_language = name.casefold() in preferred_languages
            if (
                platform_level is not None
                or effective_track == "newcomer"
                or gap > 1
                or (
                    match_version in {MATCH_VERSION_V2, MATCH_VERSION_V3}
                    and is_primary_language
                )
            ):
                critical_mismatch = True
    if critical_mismatch:
        return None

    coverage = weighted_covered / total_importance if total_importance else 1.0
    maximum_gap = max((item["gap"] for item in gaps), default=0)
    language_match = not task.get("primary_language") or task_language in preferred_languages
    type_overlap = bool(preferred_types.intersection(task_types))
    preference_score = (5 if language_match else 0) + (5 if type_overlap else 0)
    reasons = [f"skill_coverage={coverage:.2f}"]
    if language_match:
        reasons.append("preferred_language")
    if type_overlap:
        reasons.append("preferred_task_type")

    score_components: list[dict[str, Any]] = []
    if match_version == MATCH_VERSION_V3:
        language_points = 8.0 if language_match else 0.0
        type_points = 8.0 if type_overlap else 0.0
        clarity_ratio = min(max(float(task.get("text_clarity_score") or 0), 0.0), 100.0) / 100.0
        activity_ratio = _repository_activity(task)
        freshness_ratio = _freshness(task)
        contributing = bool(
            task.get("has_contributing_guide")
            or task.get("contributing_guide_available")
        )
        score_components.extend((
            _component("language_match", language_points, f"primary_language={task.get('primary_language') or 'unknown'}"),
            _component("task_type_match", type_points, f"overlap={','.join(sorted(preferred_types.intersection(task_types)))}"),
        ))
        if effective_track == "newcomer":
            score_components.extend((
                _component("skill_match", coverage * 30.0, f"skill_coverage={coverage:.3f}"),
                _component("fresh_issue", freshness_ratio * 8.0, f"freshness_ratio={freshness_ratio:.2f}"),
                _component("active_repository", activity_ratio * 8.0, f"maintenance_status={task.get('maintenance_status') or 'unknown'}"),
                _component("newcomer_signal", 18.0, "newcomer_label_signal=true"),
                _component("contributing_guide_available", 8.0 if contributing else 0.0, f"has_contributing_guide={str(contributing).lower()}"),
                _component("issue_clarity", clarity_ratio * 12.0, f"text_clarity_score={clarity_ratio * 100:.2f}"),
            ))
        else:
            desired = int(profile["desired_skill_stretch"])
            stretch_ratio = max(0.0, 1.0 - abs(maximum_gap - desired) / 2.0)
            growth_ratio = min(max(float(task.get("growth_value_score") or 0), 0.0), 100.0) / 100.0
            score_components.extend((
                _component("skill_match", coverage * 20.0, f"skill_coverage={coverage:.3f}"),
                _component("skill_stretch", stretch_ratio * 20.0, f"maximum_gap={maximum_gap};target={desired}"),
                _component("fresh_issue", freshness_ratio * 8.0, f"freshness_ratio={freshness_ratio:.2f}"),
                _component("active_repository", activity_ratio * 8.0, f"maintenance_status={task.get('maintenance_status') or 'unknown'}"),
                _component("contributing_guide_available", 8.0 if contributing else 0.0, f"has_contributing_guide={str(contributing).lower()}"),
                _component("issue_clarity", clarity_ratio * 10.0, f"text_clarity_score={clarity_ratio * 100:.2f}"),
                _component("growth_value", growth_ratio * 10.0, f"growth_value_score={growth_ratio * 100:.2f}"),
            ))
        score = sum(float(item["score_delta"]) for item in score_components)
        reasons.extend(item["code"] for item in score_components if item["score_delta"] > 0)
    elif effective_track == "newcomer":
        base = float(task["newcomer_score"] or 0)
        if match_version == MATCH_VERSION_V2:
            clarity = float(task.get("text_clarity_score") or 0)
            score = base * 0.52 + coverage * 34 + preference_score * 1.2 + clarity * 0.08
        else:
            score = base * 0.60 + coverage * 30 + preference_score
        reasons.append("newcomer_label_required")
    else:
        base = float(task["growth_value_score"] or 0)
        desired = int(profile["desired_skill_stretch"])
        stretch_score = max(0.0, 1.0 - abs(maximum_gap - desired) / 2.0) * 20
        if match_version == MATCH_VERSION_V2:
            clarity = float(task.get("text_clarity_score") or 0)
            score = (
                base * 0.42
                + coverage * 26
                + stretch_score
                + preference_score * 1.2
                + clarity * 0.06
            )
        else:
            score = base * 0.50 + coverage * 20 + stretch_score + preference_score
        reasons.append(f"stretch_target={desired}")
    if match_version == MATCH_VERSION_V2:
        reasons.append("v0.2_weighting")
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
        score_components=tuple(score_components),
        match_version=match_version,
    )


def rank_for_profile(
    profile: dict[str, Any],
    tasks: list[dict[str, Any]],
    *,
    limit: int = 20,
    match_version: str = MATCH_VERSION,
) -> list[MatchResult]:
    tasks_by_id = {int(task["task_candidate_id"]): task for task in tasks}
    results = [
        result
        for task in tasks
        if (result := match_candidate(profile, task, match_version=match_version))
    ]
    ranked = sorted(
        results, key=lambda item: (-item.match_score, item.repository, item.issue_number)
    )
    if match_version == MATCH_VERSION_V1:
        return ranked[:limit]

    selected: list[MatchResult] = []
    repository_counts: dict[str, int] = {}
    covered_task_types: set[str] = set()
    pool = list(ranked)
    repository_cap = 3 if match_version == MATCH_VERSION_V3 else limit
    while pool and len(selected) < limit:
        eligible_indexes = [
            index for index, item in enumerate(pool)
            if repository_counts.get(item.repository, 0) < repository_cap
        ]
        if not eligible_indexes:
            break
        best_index = min(
            eligible_indexes,
            key=lambda index: (
                repository_counts.get(pool[index].repository, 0)
                if match_version == MATCH_VERSION_V2 else 0,
                -(
                    pool[index].match_score
                    + (5.0 if match_version == MATCH_VERSION_V3 and (
                        {str(value).casefold() for value in tasks_by_id[pool[index].task_candidate_id].get("task_types", ())}
                        - covered_task_types
                    ) else 0.0)
                    - (repository_counts.get(pool[index].repository, 0) * 2.0
                       if match_version == MATCH_VERSION_V3 else 0.0)
                ),
                pool[index].repository,
                pool[index].issue_number,
            ),
        )
        chosen = pool.pop(best_index)
        selected.append(chosen)
        repository_counts[chosen.repository] = (
            repository_counts.get(chosen.repository, 0) + 1
        )
        covered_task_types.update(
            str(value).casefold()
            for value in tasks_by_id[chosen.task_candidate_id].get("task_types", ())
        )
    return selected


def recommendation_availability(
    profile: dict[str, Any],
    tasks: list[dict[str, Any]],
    *,
    languages: tuple[str, ...],
    task_types: tuple[str, ...],
    operating_systems: tuple[str, ...],
) -> dict[str, Any]:
    """Count matches for the current selection and each selectable alternative."""

    def count(candidate_profile: dict[str, Any]) -> int:
        return sum(
            match_candidate(candidate_profile, task) is not None for task in tasks
        )

    def profile_for_language(
        language: str, *, task_type: str | None = None
    ) -> dict[str, Any]:
        """Mirror the UI's behavior when a user selects a new language."""

        skills = dict(profile.get("skills") or {})
        skill_key = language.casefold()
        skills[skill_key] = max(int(skills.get(skill_key, 0)), 1)
        candidate_profile = {
            **profile,
            "preferred_languages": [language],
            "skills": skills,
        }
        if task_type is not None:
            candidate_profile["preferred_task_types"] = [task_type]
        return candidate_profile

    language_counts = {
        language: count(profile_for_language(language))
        for language in languages
    }
    task_type_counts = {
        task_type: count({**profile, "preferred_task_types": [task_type]})
        for task_type in task_types
    }
    os_counts = {
        operating_system: count(
            {**profile, "operating_systems": [operating_system]}
        )
        for operating_system in operating_systems
    }
    combinations = {
        language: {
            task_type: count(profile_for_language(language, task_type=task_type))
            for task_type in task_types
        }
        for language in languages
    }
    return {
        "current_selection_count": count(profile),
        "language_counts": language_counts,
        "task_type_counts": task_type_counts,
        "operating_system_counts": os_counts,
        "language_task_type_counts": combinations,
    }
