"""Recommendation application service for the v0.5 shared contracts.

The HTTP layer owned by member D can consume this module without knowing the
matching engine's legacy dictionary and 0-100 score shapes.
"""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from oss_mentor.contracts import (
    AVAILABILITY_AVAILABLE,
    FEEDBACK_STATE_NOT_SUITABLE,
    REASON_CODE_LANGUAGE_MATCH,
    REASON_CODE_ACTIVE_REPOSITORY,
    REASON_CODE_CONTRIBUTING_GUIDE,
    REASON_CODE_DIVERSITY_RERANK,
    REASON_CODE_FRESH_ISSUE,
    REASON_CODE_GROWTH_VALUE,
    REASON_CODE_ISSUE_CLARITY,
    REASON_CODE_NEGATIVE_FEEDBACK,
    REASON_CODE_NEWCOMER_SIGNAL,
    REASON_CODE_SKILL_MATCH,
    REASON_CODE_SKILL_STRETCH,
    REASON_CODE_TASK_TYPE_MATCH,
    DeveloperProfileV2,
    Difficulty,
    Reason,
    RecommendationBatchV3,
    RecommendationItemV3,
)
from oss_mentor.matching import MATCH_VERSION_V3, MatchResult, match_candidate
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
    run_id: str | None = None
    warnings: tuple[str, ...] = ()


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
    del profile
    labels = {
        REASON_CODE_LANGUAGE_MATCH: "符合偏好语言",
        REASON_CODE_TASK_TYPE_MATCH: "符合任务类型偏好",
        REASON_CODE_SKILL_MATCH: "技能覆盖良好",
        REASON_CODE_SKILL_STRETCH: "符合成长跨度",
        REASON_CODE_NEWCOMER_SIGNAL: "适合新贡献者",
        REASON_CODE_ACTIVE_REPOSITORY: "仓库保持活跃",
        REASON_CODE_FRESH_ISSUE: "任务仍然新鲜",
        REASON_CODE_CONTRIBUTING_GUIDE: "有贡献指南",
        REASON_CODE_ISSUE_CLARITY: "任务描述清晰",
        REASON_CODE_GROWTH_VALUE: "具备成长价值",
    }
    task_feature_version = str(task.get("task_feature_version") or match.match_version)
    reasons = [
        Reason(
            code=str(component["code"]),
            label=labels[str(component["code"])],
            evidence=str(component["evidence"]),
            score_delta=round(float(component["score_delta"]) / 100.0, 4),
            feature_version=task_feature_version,
        )
        for component in match.score_components
        if float(component["score_delta"]) > 0 and str(component["code"]) in labels
    ]

    if feedback_state == FEEDBACK_STATE_NOT_SUITABLE:
        reasons.append(Reason(
            code=REASON_CODE_NEGATIVE_FEEDBACK,
            label="已根据负反馈降权",
            evidence="当前用户曾将此任务标记为不适合",
            score_delta=-NEGATIVE_FEEDBACK_PENALTY,
            feature_version="recommendation-feedback-v0.3",
        ))
    return tuple(reasons)


def recommendation_item_from_match(
    match: MatchResult,
    task: dict[str, Any],
    profile: DeveloperProfileV2,
    *,
    feedback_state: str | None = None,
    diversity_reason: Reason | None = None,
    additional_warnings: tuple[str, ...] = (),
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
        reasons=(*_structured_reasons(match, task, profile, feedback_state), *(
            (diversity_reason,) if diversity_reason is not None else ()
        )),
        warnings=(*_warnings(task), *additional_warnings),
        availability=str(task.get("candidate_availability", AVAILABILITY_AVAILABLE)),
        verified_at=task.get("github_verified_at") or task.get("verified_at"),
        feedback_state=feedback_state,
    )


class RecommendationService:
    """Consume v0.5 profiles and return v0.5 recommendation items."""

    def __init__(
        self,
        candidate_store: CandidateStore,
        profile_service=None,
        auth_service=None,
        recommendation_store=None,
    ):
        self.candidate_store = candidate_store
        self.profile_service = profile_service
        self.auth_service = auth_service
        self.recommendation_store = recommendation_store

    @staticmethod
    def _hash(value: Any) -> str:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _task_types(task: dict[str, Any]) -> set[str]:
        return {str(value).casefold() for value in task.get("task_types", ())}

    def _diversify(
        self,
        matches: list[MatchResult],
        task_by_id: dict[int, dict[str, Any]],
        *,
        limit: int,
        raw_score_by_id: dict[int, float] | None = None,
    ) -> tuple[list[MatchResult], list[dict[str, Any]], tuple[str, ...]]:
        raw_score_by_id = raw_score_by_id or {
            item.task_candidate_id: item.match_score for item in matches
        }
        raw_order = sorted(
            matches,
            key=lambda item: (
                -raw_score_by_id[item.task_candidate_id],
                item.repository,
                item.issue_number,
            ),
        )
        raw_rank = {
            item.task_candidate_id: index + 1
            for index, item in enumerate(raw_order)
        }
        pool = sorted(
            matches,
            key=lambda item: (-item.match_score, item.repository, item.issue_number),
        )
        selected: list[MatchResult] = []
        repository_counts: dict[str, int] = {}
        covered_types: set[str] = set()
        warnings: list[str] = []

        while pool and len(selected) < limit:
            eligible = [item for item in pool if repository_counts.get(item.repository, 0) < 3]
            if not eligible:
                warnings.append("diversity_repository_cap_relaxed")
                eligible = list(pool)
            chosen = min(
                eligible,
                key=lambda item: (
                    -(item.match_score
                      + (5.0 if self._task_types(task_by_id[item.task_candidate_id]) - covered_types else 0.0)
                      - repository_counts.get(item.repository, 0) * 2.0),
                    item.repository,
                    item.issue_number,
                ),
            )
            pool.remove(chosen)
            selected.append(chosen)
            repository_counts[chosen.repository] = repository_counts.get(chosen.repository, 0) + 1
            covered_types.update(self._task_types(task_by_id[chosen.task_candidate_id]))

        available_types = set().union(*(
            self._task_types(task_by_id[item.task_candidate_id]) for item in raw_order
        )) if raw_order else set()
        selected_types = set().union(*(
            self._task_types(task_by_id[item.task_candidate_id]) for item in selected
        )) if selected else set()
        if len(selected) > 1 and len(available_types) > 1 and len(selected_types) < 2:
            replacement = next(
                (item for item in raw_order if item not in selected and
                 self._task_types(task_by_id[item.task_candidate_id]) - selected_types),
                None,
            )
            if replacement is not None:
                selected[-1] = replacement
            else:
                warnings.append("diversity_task_type_relaxed")

        rankings = [
            {
                "task_candidate_id": item.task_candidate_id,
                "raw_score": raw_score_by_id[item.task_candidate_id],
                "final_score": item.match_score,
                "raw_rank": raw_rank[item.task_candidate_id],
                "final_rank": final_rank,
                "diversity_reranked": raw_rank[item.task_candidate_id] != final_rank,
            }
            for final_rank, item in enumerate(selected, 1)
        ]
        return selected, rankings, tuple(dict.fromkeys(warnings))

    def recommend_batch(
        self,
        *,
        profile: DeveloperProfileV2,
        limit: int = 10,
        feedback_context: str | None = None,
        excluded_candidate_ids: tuple[int, ...] = (),
    ) -> RecommendationBatchV3:
        if not isinstance(profile, DeveloperProfileV2):
            raise TypeError("profile must be DeveloperProfileV2")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")

        excluded_ids = set(excluded_candidate_ids)
        tasks = [
            task for task in self.candidate_store.matchable_candidates()
            if task.get("candidate_availability", AVAILABILITY_AVAILABLE) == AVAILABILITY_AVAILABLE
            and int(task["task_candidate_id"]) not in excluded_ids
        ]
        task_by_id = {int(task["task_candidate_id"]): task for task in tasks}
        matching_profile = profile_for_matching(profile)
        matches = [
            result for task in tasks
            if (result := match_candidate(
                matching_profile, task, match_version=MATCH_VERSION_V3
            )) is not None
        ]
        feedback_states = (
            self.candidate_store.feedback_states(
                feedback_context, [match.task_candidate_id for match in matches]
            ) if feedback_context else {}
        )
        raw_score_by_id = {
            match.task_candidate_id: match.match_score for match in matches
        }
        adjusted = [
            replace(
                match,
                match_score=round(max(
                    0.0,
                    match.match_score - (
                        NEGATIVE_FEEDBACK_PENALTY * 100
                        if feedback_states.get(match.task_candidate_id)
                        == FEEDBACK_STATE_NOT_SUITABLE else 0.0
                    ),
                ), 2),
            ) for match in matches
        ]
        selected, rankings, batch_warnings = self._diversify(
            adjusted,
            task_by_id,
            limit=limit,
            raw_score_by_id=raw_score_by_id,
        )
        ranking_by_id = {item["task_candidate_id"]: item for item in rankings}
        items = tuple(
            recommendation_item_from_match(
                match,
                task_by_id[match.task_candidate_id],
                profile,
                feedback_state=feedback_states.get(match.task_candidate_id),
                diversity_reason=(
                    Reason(
                        code=REASON_CODE_DIVERSITY_RERANK,
                        label="多样性重排",
                        evidence=(
                            f"原始第 {ranking_by_id[match.task_candidate_id]['raw_rank']} 名，"
                            f"重排后第 {ranking_by_id[match.task_candidate_id]['final_rank']} 名"
                        ),
                        score_delta=0.0,
                        feature_version=MATCH_VERSION_V3,
                    ) if ranking_by_id[match.task_candidate_id]["diversity_reranked"] else None
                ),
                additional_warnings=batch_warnings,
            )
            for match in selected
        )
        profile_snapshot = {
            "profile_key": profile.profile_key,
            "service_track": profile.service_track,
            "preferred_languages": profile.preferred_languages,
            "operating_systems": profile.operating_systems,
            "preferred_task_types": profile.preferred_task_types,
            "max_code_difficulty": profile.max_code_difficulty,
            "max_setup_difficulty": profile.max_setup_difficulty,
            "desired_skill_stretch": profile.desired_skill_stretch,
            "skills": profile.skills,
            "profile_version": profile.profile_version,
        }
        candidate_snapshot = [
            {
                "task_candidate_id": task["task_candidate_id"],
                "repository": task.get("repository"),
                "issue_number": task.get("issue_number"),
                "primary_language": task.get("primary_language"),
                "task_types": task.get("task_types", ()),
                "operating_systems": task.get("operating_systems", ()),
                "estimated_code_difficulty": task.get("estimated_code_difficulty"),
                "estimated_setup_difficulty": task.get("estimated_setup_difficulty"),
                "requirements": task.get("requirements", ()),
                "newcomer_label_signal": task.get("newcomer_label_signal"),
                "newcomer_score": task.get("newcomer_score"),
                "growth_value_score": task.get("growth_value_score"),
                "text_clarity_score": task.get("text_clarity_score"),
                "maintenance_status": task.get("maintenance_status"),
                "has_contributing_guide": task.get("has_contributing_guide"),
                "last_activity_at": task.get("last_activity_at"),
                "created_at": task.get("created_at"),
                "task_feature_version": task.get("task_feature_version"),
                "candidate_availability": task.get("candidate_availability"),
                "github_verified_at": task.get("github_verified_at"),
                "source_fetched_at": task.get("source_fetched_at"),
            }
            for task in sorted(tasks, key=lambda value: int(value["task_candidate_id"]))
        ]
        batch = RecommendationBatchV3(
            run_id=str(uuid4()),
            feedback_context=feedback_context,
            service_track=profile.service_track,
            match_version=MATCH_VERSION_V3,
            profile_hash=self._hash(profile_snapshot),
            candidate_hash=self._hash(candidate_snapshot),
            items=items,
            rankings=tuple(rankings),
            warnings=batch_warnings,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        if self.recommendation_store is not None:
            self.recommendation_store.save_recommendation_batch(batch)
        return batch

    def recommend(
        self,
        *,
        profile: DeveloperProfileV2,
        limit: int = 10,
        feedback_context: str | None = None,
        excluded_candidate_ids: tuple[int, ...] = (),
    ) -> RecommendationBatchV3:
        """Return the shared batch contract required by the v0.5 service boundary."""
        return self.recommend_batch(
            profile=profile,
            limit=limit,
            feedback_context=feedback_context,
            excluded_candidate_ids=excluded_candidate_ids,
        )

    def recommendation_detail(
        self,
        *,
        profile: DeveloperProfileV2,
        task_candidate_id: int,
        feedback_context: str | None = None,
    ) -> RecommendationItemV3:
        """Return one eligible matching candidate without diversity reordering."""
        if not isinstance(profile, DeveloperProfileV2):
            raise TypeError("profile must be DeveloperProfileV2")
        task = next(
            (
                item for item in self.candidate_store.matchable_candidates()
                if int(item["task_candidate_id"]) == int(task_candidate_id)
            ),
            None,
        )
        if task is None or task.get(
            "candidate_availability", AVAILABILITY_AVAILABLE
        ) != AVAILABILITY_AVAILABLE:
            raise KeyError("recommendable task candidate was not found")
        match = match_candidate(
            profile_for_matching(profile), task, match_version=MATCH_VERSION_V3
        )
        if match is None:
            raise KeyError("task candidate does not match the profile")
        feedback_state = None
        if feedback_context:
            feedback_state = self.candidate_store.feedback_states(
                feedback_context, [match.task_candidate_id]
            ).get(match.task_candidate_id)
        if feedback_state == FEEDBACK_STATE_NOT_SUITABLE:
            match = replace(
                match,
                match_score=round(max(
                    0.0, match.match_score - NEGATIVE_FEEDBACK_PENALTY * 100
                ), 2),
            )
        return recommendation_item_from_match(
            match, task, profile, feedback_state=feedback_state
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
        batch = self.recommend_batch(
            profile=profile,
            limit=limit,
            feedback_context=feedback_context,
        )
        return AuthenticatedRecommendations(
            profile=profile,
            feedback_context=feedback_context,
            items=batch.items,
            run_id=batch.run_id,
            warnings=batch.warnings,
        )
