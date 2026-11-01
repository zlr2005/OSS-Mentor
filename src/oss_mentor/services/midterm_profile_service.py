"""Platform integration for the midterm profile assessment workflow.

The profiling package owns assessment scoring and confirmation rules.  This
module owns the platform boundary: session-safe identity injection, persistence
orchestration, request idempotency, and conversion to the frozen public
contracts.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from oss_mentor.contracts_midterm import (
    ASSESSMENT_PREVIEW_SCHEMA_VERSION,
    CONFIRMATION_PLAN_SCHEMA_VERSION,
    AssessmentConfirmation,
    AssessmentSubmission,
    ConfirmationPlan,
    EnhancedProfileFeatures,
    IdempotencyConflictError,
    ProfilePreview,
    StalePlanError,
)
from oss_mentor.profiling.assessment import (
    build_assessment_preview,
    load_questionnaire,
)
from oss_mentor.profiling.confirmation import (
    build_confirmation_plan,
    build_feature_state,
    confirm_assessment_plan,
)
from oss_mentor.services.profile_service import ProfileService
from oss_mentor.storage.base import MidtermProfileStore
from oss_mentor.storage.profiles import SQLiteMidtermProfileStore


class SQLiteMidtermProfileApiStore(SQLiteMidtermProfileStore):
    """SQLite adapter with the API-only submission replay lookup.

    The frozen shared storage protocol intentionally does not add this method.
    The HTTP service needs to recover the server timestamp and submission id for
    a retried preview request without trusting a browser timestamp.
    """

    def find_assessment_submission_by_idempotency_key(
        self,
        *,
        user_id: int,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM profile_assessment_submission
                WHERE user_id = ? AND idempotency_key = ?
                """,
                (user_id, idempotency_key),
            ).fetchone()
        if row is None:
            return None
        return {
            "submission_id": str(row["submission_id"]),
            "questionnaire_id": str(row["questionnaire_id"]),
            "questionnaire_version": str(row["questionnaire_version"]),
            "submitted_at": str(row["submitted_at"]),
            "answers": json.loads(row["answers_json"]),
            "created_at": str(row["created_at"]),
        }


class MidtermProfileRequiredError(Exception):
    """The authenticated user has not created the platform profile yet."""


class MidtermProfileNotConfirmedError(Exception):
    """The user has no confirmed midterm feature snapshot to export."""


def verified_developer_key(github_login: Any) -> str | None:
    """Return the normalized identity from an authenticated GitHub session.

    This helper deliberately accepts only the value already resolved by the
    session.  Browser input must never be passed here directly.
    """

    if not isinstance(github_login, str):
        return None
    normalized = github_login.strip().casefold()
    return normalized or None


class MidtermProfileService:
    """Coordinate B-owned assessment rules with D-owned API/storage wiring."""

    def __init__(
        self,
        *,
        store: MidtermProfileStore,
        profile_service: ProfileService,
        questionnaire_path: str | Path,
    ) -> None:
        self.store = store
        self.profile_service = profile_service
        self.questionnaire_path = Path(questionnaire_path)
        self.questionnaire = load_questionnaire(self.questionnaire_path)
        self.questionnaire_definition_sha256 = hashlib.sha256(
            self.questionnaire_path.read_bytes()
        ).hexdigest()
        self._initialized = False

    def initialize(self) -> None:
        """Register the configured questionnaire as an immutable version."""

        if self._initialized:
            return
        self.store.put_questionnaire(
            questionnaire_id=self.questionnaire["questionnaire_id"],
            questionnaire_version=self.questionnaire["questionnaire_version"],
            schema_version=self.questionnaire["schema_version"],
            feature_schema_version=self.questionnaire["feature_schema_version"],
            definition=self.questionnaire,
            definition_sha256=self.questionnaire_definition_sha256,
        )
        self._initialized = True

    def questionnaire_payload(self) -> dict[str, Any]:
        return copy.deepcopy(self.questionnaire)

    def create_preview(
        self,
        *,
        user_id: int,
        questionnaire_version: str,
        answers: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Create a persisted submission, preview and confirmation plan.

        The browser supplies answers only.  All owner/profile/version values are
        resolved by the service.  A retry recovers the original server
        timestamp and submission id before rebuilding the deterministic preview
        and plan.
        """

        profile = self.profile_service.profile_for_user(user_id)
        if profile is None:
            raise MidtermProfileRequiredError(
                "create a profile before submitting an assessment"
            )
        profile_key = str(profile["profile_key"])
        key = self._required_text(idempotency_key, "idempotency_key")
        version = self._required_text(questionnaire_version, "questionnaire_version")
        if version != self.questionnaire["questionnaire_version"]:
            raise ValueError("unknown questionnaire_version")
        if not isinstance(answers, Mapping):
            raise ValueError("answers must be an object")

        preview_id = self._stable_id(
            "preview",
            user_id=user_id,
            idempotency_key=key,
        )
        plan_id = self._stable_id(
            "plan",
            user_id=user_id,
            idempotency_key=key,
        )

        with self.store.transaction() as transaction:
            lookup = getattr(
                transaction,
                "find_assessment_submission_by_idempotency_key",
                None,
            )
            existing_submission = (
                lookup(user_id=user_id, idempotency_key=key)
                if callable(lookup)
                else None
            )
            if existing_submission is not None:
                if (
                    existing_submission["questionnaire_version"] != version
                    or existing_submission["answers"] != dict(answers)
                ):
                    raise IdempotencyConflictError(
                        "submission idempotency key was reused with another payload"
                    )
                submitted_at = existing_submission["submitted_at"]
                submitted_answers = existing_submission["answers"]
                existing_preview = transaction.get_profile_preview(
                    user_id=user_id,
                    preview_id=preview_id,
                )
                existing_plan = transaction.get_confirmation_plan(
                    user_id=user_id,
                    plan_id=plan_id,
                )
                if existing_preview is not None and existing_plan is not None:
                    return {
                        "submission_id": existing_submission["submission_id"],
                        "preview": existing_preview.to_dict(),
                        "plan": existing_plan.to_dict(),
                    }
            else:
                submitted_at = self._now()
                submitted_answers = dict(answers)

            private_preview = build_assessment_preview(
                self.questionnaire,
                {
                    "questionnaire_version": version,
                    "submitted_at": submitted_at,
                    "answers": submitted_answers,
                },
            )

            # Preview persistence matches submissions by their normalized
            # answers. Store exactly the preview answers so optional-question
            # normalization cannot break the durable relation.
            normalized_answers = dict(private_preview["answers"])
            submission = AssessmentSubmission.from_dict(
                {
                    "questionnaire_id": self.questionnaire["questionnaire_id"],
                    "questionnaire_version": version,
                    "submitted_at": submitted_at,
                    "answers": normalized_answers,
                }
            )
            current = transaction.get_current_feature_state(user_id=user_id)
            current_payload = (
                current.to_dict()
                if current is not None
                else build_feature_state(
                    private_preview,
                    profile_key=profile_key,
                    profile_version="profile:v0",
                    updated_at=submitted_at,
                )
            )
            preview = ProfilePreview.from_private(
                private_preview,
                preview_id=preview_id,
                profile_key=profile_key,
                base_profile_version=str(current_payload["profile_version"]),
            )
            plan_payload = build_confirmation_plan(private_preview, current_payload)
            plan = ConfirmationPlan.from_private(
                plan_payload,
                preview_id=preview.preview_id,
                created_at=submitted_at,
            )
            # B's plan digest is content-based and does not include the preview
            # id. Different previews in the same second can therefore receive
            # the same digest. Scope the persisted identity to the server-owned
            # idempotency key so two legitimate plans cannot collide.
            plan = replace(
                plan,
                plan_id=plan_id,
            )
            submission_id = transaction.create_assessment_submission(
                user_id=user_id,
                submission=submission,
                idempotency_key=key,
            )
            transaction.create_profile_preview(
                user_id=user_id,
                preview=preview,
                input_sha256=self._digest(
                    {
                        "questionnaire_version": version,
                        "answers": normalized_answers,
                        "idempotency_key": key,
                    }
                ),
            )
            transaction.create_confirmation_plan(user_id=user_id, plan=plan)

        return {
            "submission_id": submission_id,
            "preview": preview.to_dict(),
            "plan": plan.to_dict(),
        }

    def confirm(
        self,
        *,
        user_id: int,
        plan_id: str,
        decisions: Mapping[str, str],
        idempotency_key: str,
    ) -> AssessmentConfirmation:
        """Apply explicit decisions and publish one immutable profile version."""

        if self.profile_service.profile_for_user(user_id) is None:
            raise MidtermProfileRequiredError(
                "create a profile before confirming an assessment"
            )
        plan_key = self._required_text(plan_id, "plan_id")
        key = self._required_text(idempotency_key, "idempotency_key")
        if not isinstance(decisions, Mapping) or not decisions:
            raise ValueError("decisions must be a non-empty object")
        normalized_decisions = {}
        for suggestion_id, decision in decisions.items():
            normalized_decisions[
                self._required_text(suggestion_id, "suggestion_id")
            ] = self._required_text(decision, "decision")

        with self.store.transaction() as transaction:
            existing = transaction.find_confirmation_by_idempotency_key(
                user_id=user_id,
                idempotency_key=key,
            )
            if existing is not None:
                existing_decisions = {
                    item.suggestion_id: item.decision for item in existing.decisions
                }
                if (
                    existing.plan_id != plan_key
                    or existing_decisions != normalized_decisions
                ):
                    raise IdempotencyConflictError(
                        "confirmation idempotency key was reused with another payload"
                    )
                return existing

            plan = transaction.get_confirmation_plan(
                user_id=user_id,
                plan_id=plan_key,
            )
            if plan is None:
                raise KeyError("confirmation plan was not found")
            if plan.status != "open":
                raise IdempotencyConflictError("confirmation plan is no longer open")

            current = transaction.get_current_feature_state(user_id=user_id)
            if current is None:
                preview = transaction.get_profile_preview(
                    user_id=user_id,
                    preview_id=plan.preview_id,
                )
                if preview is None:
                    raise KeyError("assessment preview was not found")
                private_preview = self._private_preview_payload(preview)
                current_payload = build_feature_state(
                    private_preview,
                    profile_key=preview.profile_key,
                    profile_version=plan.base_profile_version,
                    updated_at=private_preview["submitted_at"],
                )
            else:
                current_payload = current.to_dict()

            if str(current_payload["profile_version"]) != plan.base_profile_version:
                raise StalePlanError(
                    "confirmation plan is based on a stale profile version"
                )

            plan_payload = {
                **plan.to_dict(),
                "schema_version": CONFIRMATION_PLAN_SCHEMA_VERSION,
            }
            result_payload = confirm_assessment_plan(
                plan_payload,
                current_payload,
                normalized_decisions,
                confirmed_at=self._now(),
            )
            confirmation = AssessmentConfirmation.from_private(result_payload)
            return transaction.commit_assessment_confirmation(
                user_id=user_id,
                confirmation=confirmation,
                decisions=confirmation.decisions,
                feature_state=confirmation.profile_state,
                idempotency_key=key,
            )

    def enhanced_features(
        self,
        *,
        user_id: int,
        profile_version: str | None = None,
        developer_key: str | None = None,
    ) -> EnhancedProfileFeatures:
        if self.profile_service.profile_for_user(user_id) is None:
            raise MidtermProfileRequiredError(
                "create a profile before exporting enhanced features"
            )
        features = self.store.get_enhanced_profile_features(
            user_id=user_id,
            profile_version=profile_version,
            developer_key=developer_key,
        )
        if features is None:
            raise MidtermProfileNotConfirmedError(
                "no confirmed midterm profile version was found"
            )
        return features

    @staticmethod
    def _required_text(value: Any, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _digest(payload: Mapping[str, Any]) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _stable_id(
        cls,
        namespace: str,
        *,
        user_id: int,
        idempotency_key: str,
    ) -> str:
        digest = cls._digest(
            {
                "namespace": namespace,
                "user_id": user_id,
                "idempotency_key": idempotency_key,
            }
        )
        return f"{namespace}:{digest[:20]}"

    @staticmethod
    def _now() -> str:
        return (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )

    @staticmethod
    def _private_preview_payload(preview: ProfilePreview) -> dict[str, Any]:
        payload = preview.to_dict()
        return {
            "schema_version": ASSESSMENT_PREVIEW_SCHEMA_VERSION,
            "questionnaire_id": payload["questionnaire_id"],
            "questionnaire_version": payload["questionnaire_version"],
            "feature_schema_version": payload["feature_schema_version"],
            "submitted_at": payload["submitted_at"],
            "answers": dict(payload["answers"]),
            "features": list(payload["features"]),
            "current_interests": list(payload["current_interests"]),
            "interpretation": payload["interpretation"],
            "saved": False,
        }


__all__ = [
    "MidtermProfileNotConfirmedError",
    "MidtermProfileRequiredError",
    "MidtermProfileService",
    "SQLiteMidtermProfileApiStore",
    "verified_developer_key",
]
