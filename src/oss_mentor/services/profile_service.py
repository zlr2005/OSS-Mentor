"""Business workflow for manual and GitHub-assisted developer profiles."""

from __future__ import annotations

from typing import Any

from oss_mentor.developer_profiles import (
    apply_profile_suggestion,
    build_github_profile_import,
    build_profile_merge_preview,
)
from oss_mentor.storage.profiles import ProfileStorage


class ProfileService:
    """Coordinate profile import, preview, and explicit user decisions."""

    def __init__(
        self,
        storage: ProfileStorage,
    ) -> None:
        self.storage = storage

    def save_manual_profile(
        self,
        profile: dict[str, Any],
        *,
        user_key: str | None = None,
    ) -> dict[str, Any]:
        self.storage.save_profile(
            profile,
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

        pending = (
            self.storage.list_suggestions(
                profile_key=profile_key,
                status="pending",
            )
        )

        suggestion = next(
            (
                item
                for item in pending
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
                "unknown pending profile suggestion"
            )

        resolved = apply_profile_suggestion(
            current_profile,
            suggestion,
            decision=decision,
        )

        if decision == "accept":
            updated_profile = resolved[
                "profile"
            ]

            self.storage.save_profile(
                updated_profile,
                user_key=current_profile.get(
                    "user_key"
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

        return {
            "profile": profile,
            "suggestion": {
                **suggestion,
                "status": (
                    "accepted"
                    if decision == "accept"
                    else "rejected"
                ),
            },
        }

    def delete_profile(
        self,
        profile_key: str,
    ) -> bool:
        return self.storage.delete_profile(
            profile_key
        )