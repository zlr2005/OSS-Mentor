"""Recommendation application service for the v0.5 shared contracts.

The HTTP layer owned by member D can consume this module without knowing the
matching engine's legacy dictionary and 0-100 score shapes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

from oss_mentor.contracts import (
    AVAILABILITY_AVAILABLE,
    FEEDBACK_STATE_NOT_SUITABLE,
    REASON_CODE_LANGUAGE_MATCH,
    REASON_CODE_NEGATIVE_FEEDBACK,
    REASON_CODE_NEWCOMER_SIGNAL,
    REASON_CODE_SKILL_MATCH,
    REASON_CODE_SKILL_STRETCH,
    REASON_CODE_TASK_TYPE_MATCH,
    DeveloperProfileV2,
    Difficulty,
    Reason,
    RecommendationItemV3,
)
from oss_mentor.matching import MATCH_VERSION_V2, MatchResult, match_candidate
from oss_mentor.storage.base import CandidateStore

NEGATIVE_FEEDBACK_PENALTY = 0.25


class AuthenticationRequired(ValueError):
    """The supplied session does not identify an active user."""


class ProfileRequired(ValueError):
    """The authenticated user does not yet have a developer profile."""


@dataclass(frozen=True, slots=True)
class AuthenticatedRecommendations:
    """Transport-neutral result for D's authenticated recommendation route."""

    profile: DeveloperProfileV2
    feedback_context: str
    items: tuple[RecommendationItemV3, ...]


def profile_for_matching(profile: DeveloperProfileV2) -> dict[str, Any]:
    """Adapt the shared profile contract to the legacy matching engine."""
    if not isinstance(profile, DeveloperProfileV2):
        raise TypeError("profile must be DeveloperProfileV2")
    return {
        "profile_key": profile.profile_key,
        "display_name": profile.display_name,
        "service_track": profile.service_track,
        "preferred_languages": list(profile.preferred_languages),
        "operating_systems": [value.casefold() for value in profile.operating_systems],
        "preferred_task_types": list(profile.preferred_task_types),
        "max_code_difficulty": profile.max_code_difficulty,
        "max_setup_difficulty": profile.max_setup_difficulty,
        "desired_skill_stretch": profile.desired_skill_stretch,
        "skills": dict(profile.skills),
    }


def feedback_context_for_user(user_id: int, profile_key: str) -> str:
    """Bind feedback to both the authenticated owner and current profile."""
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
        raise ValueError("user_id must be a positive integer")
    if not profile_key or len(profile_key) > 80:
        raise ValueError("profile_key must contain between 1 and 80 characters")
    return f"user:{user_id}:profile:{profile_key}"


def _warnings(task: dict[str, Any]) -> tuple[str, ...]:
    value = task.get("warnings")
    if value is None:
        value = task.get("warnings_json", "[]")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = []
    return tuple(str(item) for item in value) if isinstance(value, list) else ()


def _structured_reasons(
    match: MatchResult,
    task: dict[str, Any],
    profile: DeveloperProfileV2,
    feedback_state: str | None,
) -> tuple[Reason, ...]:
    reasons: list[Reason] = []
    language = str(task.get("primary_language") or "").strip()
    preferred_languages = {value.casefold() for value in profile.preferred_languages}
    if language and language.casefold() in preferred_languages:
        reasons.append(Reason(
            code=REASON_CODE_LANGUAGE_MATCH,
            label="符合偏好语言",
            evidence=f"仓库主要语言为 {language}",
            score_delta=0.06,
        ))
    task_types = {str(value).casefold() for value in task.get("task_types", ())}
    overlap = sorted(
        task_types.intersection(value.casefold() for value in profile.preferred_task_types)
    )
    if overlap:
        reasons.append(Reason(
            code=REASON_CODE_TASK_TYPE_MATCH,
            label="符合任务类型偏好",
            evidence=f"匹配任务类型：{', '.join(overlap)}",
            score_delta=0.06,
        ))

    matched = [str(gap["skill"]) for gap in match.skill_gaps if int(gap["gap"]) == 0]
    coverage_weight = 0.34 if match.track == "newcomer" else 0.26
    reasons.append(Reason(
        code=REASON_CODE_SKILL_MATCH,
        label="技能匹配",
        evidence=(
            f"技能覆盖率 {match.skill_coverage:.0%}"
            + (f"，已满足：{', '.join(matched)}" if matched else "")
        ),
        score_delta=round(match.skill_coverage * coverage_weight, 4),
    ))

    if match.track == "newcomer":
        reasons.append(Reason(
            code=REASON_CODE_NEWCOMER_SIGNAL,
            label="适合新贡献者",
            evidence="任务具有新人友好标签信号",
            score_delta=round(float(task.get("newcomer_score") or 0) * 0.0052, 4),
        ))
    else:
        desired = profile.desired_skill_stretch
        stretch_delta = max(0.0, 1.0 - abs(match.maximum_skill_gap - desired) / 2.0) * 0.2
        reasons.append(Reason(
            code=REASON_CODE_SKILL_STRETCH,
            label="符合成长跨度",
            evidence=f"最大技能差距 {match.maximum_skill_gap}，目标跨度 {desired}",
            score_delta=round(stretch_delta, 4),
        ))

    if feedback_state == FEEDBACK_STATE_NOT_SUITABLE:
        reasons.append(Reason(
            code=REASON_CODE_NEGATIVE_FEEDBACK,
            label="已根据负反馈降权",
            evidence="当前用户曾将此任务标记为不适合",
            score_delta=-NEGATIVE_FEEDBACK_PENALTY,
        ))
    return tuple(reasons)


def recommendation_item_from_match(
    match: MatchResult,
    task: dict[str, Any],
    profile: DeveloperProfileV2,
    *,
    feedback_state: str | None = None,
) -> RecommendationItemV3:
    """Map a 0-100 MatchResult into the public 0-1 v0.5 contract."""
    gaps = match.skill_gaps
    return RecommendationItemV3(
        task_candidate_id=match.task_candidate_id,
        repository_full_name=match.repository,
        issue_number=match.issue_number,
        title=match.title,
        html_url=match.html_url,
        service_track=match.track,
        score=round(match.match_score / 100.0, 4),
        difficulty=Difficulty(
            code=int(task["estimated_code_difficulty"]),
            setup=int(task["estimated_setup_difficulty"]),
        ),
        matched_skills=tuple(str(item["skill"]) for item in gaps if int(item["gap"]) == 0),
        missing_skills=tuple(str(item["skill"]) for item in gaps if int(item["gap"]) > 0),
        reasons=_structured_reasons(match, task, profile, feedback_state),
        warnings=_warnings(task),
        availability=str(task.get("candidate_availability", AVAILABILITY_AVAILABLE)),
        verified_at=task.get("github_verified_at") or task.get("verified_at"),
        feedback_state=feedback_state,
    )


class RecommendationService:
    """Consume v0.5 profiles and return v0.5 recommendation items."""

    def __init__(self, candidate_store: CandidateStore, profile_service=None, auth_service=None):
        self.candidate_store = candidate_store
        self.profile_service = profile_service
        self.auth_service = auth_service

    def recommend(
        self,
        *,
        profile: DeveloperProfileV2,
        limit: int = 10,
        feedback_context: str | None = None,
    ) -> tuple[RecommendationItemV3, ...]:
        if not isinstance(profile, DeveloperProfileV2):
            raise TypeError("profile must be DeveloperProfileV2")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")

        tasks = [
            task for task in self.candidate_store.matchable_candidates()
            if task.get("candidate_availability", AVAILABILITY_AVAILABLE) == AVAILABILITY_AVAILABLE
        ]
        task_by_id = {int(task["task_candidate_id"]): task for task in tasks}
        matching_profile = profile_for_matching(profile)
        matches = [
            result for task in tasks
            if (result := match_candidate(
                matching_profile, task, match_version=MATCH_VERSION_V2
            )) is not None
        ]
        feedback_states = (
            self.candidate_store.feedback_states(
                feedback_context, [match.task_candidate_id for match in matches]
            )
            if feedback_context else {}
        )
        adjusted = [
            replace(
                match,
                match_score=round(max(
                    0.0,
                    match.match_score
                    - (NEGATIVE_FEEDBACK_PENALTY * 100
                       if feedback_states.get(match.task_candidate_id)
                       == FEEDBACK_STATE_NOT_SUITABLE else 0.0),
                ), 2),
            )
            for match in matches
        ]
        adjusted.sort(key=lambda item: (-item.match_score, item.repository, item.issue_number))
        return tuple(
            recommendation_item_from_match(
                match,
                task_by_id[match.task_candidate_id],
                profile,
                feedback_state=feedback_states.get(match.task_candidate_id),
            )
            for match in adjusted[:limit]
        )

    def recommend_for_session(
        self,
        *,
        session_id: str | None,
        limit: int = 10,
    ) -> AuthenticatedRecommendations:
        """Resolve session -> user -> current profile before recommendation."""
        if self.auth_service is None or self.profile_service is None:
            raise RuntimeError("auth_service and profile_service are required")
        user = self.auth_service.current_user(session_id)
        if user is None:
            raise AuthenticationRequired("login is required")
        profile = self.profile_service.profile_for_user_contract(int(user["user_id"]))
        if profile is None:
            raise ProfileRequired("create a profile before requesting recommendations")
        feedback_context = feedback_context_for_user(int(user["user_id"]), profile.profile_key)
        return AuthenticatedRecommendations(
            profile=profile,
            feedback_context=feedback_context,
            items=self.recommend(
                profile=profile,
                limit=limit,
                feedback_context=feedback_context,
            ),
        )
