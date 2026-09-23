"""Business workflow for manual and GitHub-assisted developer profiles."""

from __future__ import annotations

from typing import Any
from datetime import datetime, timezone
from uuid import uuid4

from oss_mentor.contracts import DeveloperProfileV2
from oss_mentor.developer_profiles import (
    apply_profile_suggestion,
    build_github_profile_import,
    build_profile_merge_preview,
    custom_profile_for_matching,
    LANGUAGE_DISPLAY_NAMES,
)
from oss_mentor.storage.base import ProfileStore


def as_developer_profile_v2(
    profile: dict[str, Any],
) -> DeveloperProfileV2:
    """Convert the persisted profile shape into C's shared contract."""
    profile_source = str(profile.get("profile_source", "user"))
    if profile_source == "user_input":
        profile_source = "user"
    return DeveloperProfileV2(
        profile_key=str(profile["profile_key"]),
        display_name=str(profile["display_name"]),
        service_track=str(profile["service_track"]),
        preferred_languages=tuple(
            str(item)
            for item in profile["preferred_languages"]
        ),
        operating_systems=tuple(
            str(item)
            for item in profile["operating_systems"]
        ),
        preferred_task_types=tuple(
            str(item)
            for item in profile["preferred_task_types"]
        ),
        max_code_difficulty=int(
            profile["max_code_difficulty"]
        ),
        max_setup_difficulty=int(
            profile["max_setup_difficulty"]
        ),
        desired_skill_stretch=int(
            profile["desired_skill_stretch"]
        ),
        skills={
            str(name): int(level)
            for name, level in profile.get(
                "skills",
                {},
            ).items()
        },
        profile_source=profile_source,
    )


class ProfileService:
    """Coordinate profile import, preview, and explicit user decisions."""

    def __init__(
        self,
        storage: ProfileStore,
    ) -> None:
        self.storage = storage

    def update_owned_profile(self, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate an untrusted PUT, assign provenance and preserve unchanged metadata."""
        if not isinstance(payload, dict):
            raise ValueError("profile must be an object")
        locks = payload.get("locks", {})
        if not isinstance(locks, dict) or any(type(value) is not bool for value in locks.values()):
            raise ValueError("locks must map profile fields to booleans")
        editable = {key: value for key, value in payload.items() if key != "locks"}
        if not isinstance(editable.get("service_track"), str):
            raise ValueError("service_track must be newcomer or growth")
        validated = custom_profile_for_matching(editable)
        # Match validation lowercases skill keys. Persist conventional language spelling,
        # so imported language evidence addresses the same skill as manual input.
        validated["skills"] = {
            LANGUAGE_DISPLAY_NAMES.get(name, name): level for name, level in validated["skills"].items()
        }
        with self.storage.transaction() as storage:
            current = storage.profile_for_user(user_id)
            validated["profile_key"] = current["profile_key"] if current else str(uuid4())
            fields = set(validated) - {"skills", "profile_key", "profile_source"}
            fields.update("skills." + name for name in validated["skills"])
            if set(locks) - fields:
                raise ValueError("locks contains an unknown profile field")
            metadata = {}
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            for field in fields:
                old_value = (current or {}).get(field)
                new_value = validated.get(field)
                if field.startswith("skills."):
                    name = field.removeprefix("skills.")
                    old_value = (current or {}).get("skills", {}).get(name)
                    new_value = validated["skills"][name]
                prior = (current or {}).get("field_metadata", {}).get(field, {})
                if current and old_value == new_value and prior:
                    metadata[field] = dict(prior)
                else:
                    metadata[field] = {"source": "user_input", "locked": prior.get("locked", False),
                                       "observed_at": now}
                if field in locks:
                    metadata[field]["locked"] = locks[field]
            validated["field_metadata"] = metadata
            if current and current.get("consent_version"):
                validated["consent_version"] = current["consent_version"]
            return ProfileService(storage).save_manual_profile(validated, user_id=user_id)

    def save_manual_profile(
        self,
        profile: dict[str, Any],
        *,
        user_id: int | None = None,
        user_key: str | None = None,
    ) -> dict[str, Any]:
        self.storage.save_profile(
            profile,
            user_id=user_id,
            user_key=user_key,
        )

        saved = self.storage.load_profile(
            str(profile["profile_key"])
        )

        if saved is None:
            raise RuntimeError(
                "profile was saved but could not be reloaded"
            )

        return saved

    def profile(
        self,
        profile_key: str,
    ) -> dict[str, Any] | None:
        return self.storage.load_profile(
            profile_key
        )

    def profile_for_user(
        self,
        user_id: int,
    ) -> dict[str, Any] | None:
        """Return the profile owned by the authenticated platform user."""
        return self.storage.profile_for_user(
            user_id
        )

    def profile_contract(
        self,
        profile_key: str,
    ) -> DeveloperProfileV2 | None:
        profile = self.profile(profile_key)
        return (
            as_developer_profile_v2(profile)
            if profile is not None
            else None
        )

    def import_github_profile(
        self,
        *,
        profile_key: str,
        github_payload: dict[str, Any],
    ) -> dict[str, Any]:
        current_profile = (
            self.storage.load_profile(
                profile_key
            )
        )

        if current_profile is None:
            raise KeyError(
                f"unknown profile: {profile_key}"
            )

        github_import = (
            build_github_profile_import(
                github_payload
            )
        )

        preview = (
            build_profile_merge_preview(
                current_profile,
                github_import,
            )
        )

        persisted = (
            self.storage.save_github_import(
                profile_key=profile_key,
                github_import=github_import,
                merge_preview=preview,
            )
        )

        return {
            "profile_key": profile_key,
            "github_import": github_import,
            "merge_preview": preview,
            "persistence": persisted,
            "suggestions":
                self.storage.list_suggestions(
                    profile_key=profile_key,
                    status="pending",
                ),
        }

    def pending_suggestions(
        self,
        *,
        profile_key: str,
    ) -> list[dict[str, Any]]:
        profile = self.storage.load_profile(
            profile_key
        )

        if profile is None:
            raise KeyError(
                f"unknown profile: {profile_key}"
            )

        return self.storage.list_suggestions(
            profile_key=profile_key,
            status="pending",
        )

    def accept_suggestion(
        self,
        *,
        profile_key: str,
        suggestion_id: int,
    ) -> dict[str, Any]:
        return self._resolve_suggestion(
            profile_key=profile_key,
            suggestion_id=suggestion_id,
            decision="accept",
        )

    def reject_suggestion(
        self,
        *,
        profile_key: str,
        suggestion_id: int,
    ) -> dict[str, Any]:
        return self._resolve_suggestion(
            profile_key=profile_key,
            suggestion_id=suggestion_id,
            decision="reject",
        )

    def _resolve_suggestion(
        self, *, profile_key: str, suggestion_id: int, decision: str,
    ) -> dict[str, Any]:
        with self.storage.transaction() as storage:
            return ProfileService(storage)._resolve_in_transaction(
                profile_key=profile_key, suggestion_id=suggestion_id, decision=decision
            )

    def _resolve_in_transaction(
        self,
        *,
        profile_key: str,
        suggestion_id: int,
        decision: str,
    ) -> dict[str, Any]:
        current_profile = (
            self.storage.load_profile(
                profile_key
            )
        )

        if current_profile is None:
            raise KeyError(
                f"unknown profile: {profile_key}"
            )

        suggestions = (
            self.storage.list_suggestions(
                profile_key=profile_key,
            )
        )

        suggestion = next(
            (
                item
                for item in suggestions
                if int(
                    item[
                        "profile_field_suggestion_id"
                    ]
                )
                == int(suggestion_id)
            ),
            None,
        )

        if suggestion is None:
            raise KeyError(
                "unknown profile suggestion"
            )
        if suggestion["status"] != "pending":
            raise ValueError("state_conflict: suggestion is already resolved")

        domain_suggestion = {
            **suggestion,
            "field": suggestion[
                "field_name"
            ],
            "source": suggestion[
                "suggestion_source"
            ],
        }

        resolved = apply_profile_suggestion(
            current_profile,
            domain_suggestion,
            decision=decision,
        )

        if decision == "accept":
            updated_profile = resolved[
                "profile"
            ]

            self.storage.save_profile(
                updated_profile,
                user_id=current_profile.get(
                    "user_id"
                ),
            )

            self.storage.mark_suggestion_status(
                suggestion_id,
                status="accepted",
            )

        elif decision == "reject":
            self.storage.mark_suggestion_status(
                suggestion_id,
                status="rejected",
            )

        else:
            raise ValueError(
                "decision must be accept or reject"
            )

        profile = self.storage.load_profile(
            profile_key
        )

        if profile is None:
            raise RuntimeError(
                "resolved profile could not be reloaded"
            )

        suggestions = self.storage.list_suggestions(profile_key=profile_key)
        return {
            "profile": profile,
            "suggestion": next(
                item for item in suggestions
                if item["profile_field_suggestion_id"] == int(suggestion_id)
            ),
            "suggestions": suggestions,
        }

    def delete_profile(
        self,
        profile_key: str,
    ) -> bool:
        return self.storage.delete_profile(
            profile_key
        )
