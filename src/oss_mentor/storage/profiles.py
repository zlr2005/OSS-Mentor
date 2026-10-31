"""SQLite persistence for developer profiles and GitHub profile evidence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager, nullcontext
from copy import copy
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from oss_mentor.contracts import DeveloperProfileV2
from oss_mentor.contracts_midterm import (
    AssessmentConfirmation,
    AssessmentSubmission,
    ConfirmationDecision,
    ConfirmationPlan,
    ConfirmationSuggestion,
    EnhancedProfileFeatures,
    IdempotencyConflictError,
    ProfileFeature,
    ProfileFeatureState,
    ProfileFeatureStateRow,
    ProfilePreview,
    StalePlanError,
)
from oss_mentor.developer_profiles import (
    GITHUB_PROFILE_IMPORT_VERSION,
    PROFILE_MERGE_VERSION,
)
from oss_mentor.sqlite_store import SQLiteCandidateStore
from oss_mentor.storage.base import ProfileStore


_PROFILE_SOURCES = {
    "default",
    "github_weak_inference",
    "github_explicit_evidence",
    "user_input",
    "user_confirmed",
}

_ROOT_PROFILE_SOURCES = {
    "demo",
    "user_input",
    "import",
}

_GITHUB_SOURCES = {
    "github_weak_inference",
    "github_explicit_evidence",
}

_SUGGESTION_STATUSES = {
    "pending",
    "accepted",
    "rejected",
}


def _json_dump(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _json_sha256(value: Any) -> str:
    return hashlib.sha256(_json_dump(value).encode("utf-8")).hexdigest()


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _json_load(
    value: str | None,
    *,
    default: Any,
) -> Any:
    if value is None:
        return default

    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _string_list(
    value: Any,
    *,
    field: str,
) -> list[str]:
    if value is None:
        return []

    if not isinstance(value, (list, tuple)):
        raise ValueError(
            f"{field} must be an array"
        )

    return [
        str(item)
        for item in value
    ]


def _profile_import_key(
    github_import: dict[str, Any],
) -> str:
    canonical = _json_dump(
        github_import
    ).encode("utf-8")

    digest = hashlib.sha256(
        canonical
    ).hexdigest()

    return f"github-profile:{digest}"


ProfileStorage = ProfileStore  # Compatibility import for B's existing callers.


class SQLiteProfileStorage(
    SQLiteCandidateStore
):
    """SQLite implementation of the profile persistence contract."""

    def connect(self):
        connection = getattr(self, "_transaction_connection", None)
        if connection is not None:
            # Nested operations borrow the connection; only transaction() commits.
            return nullcontext(connection)
        return super().connect()

    @contextmanager
    def transaction(self):
        """Serialize read/modify/write decisions, without sharing connections across threads."""
        if getattr(self, "_transaction_connection", None) is not None:
            yield self
            return
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            scoped = copy(self)
            scoped._transaction_connection = connection
            yield scoped

    def initialize(self) -> None:
        if getattr(self, "_transaction_connection", None) is not None:
            raise RuntimeError("initialize storage before opening a transaction")
        super().initialize()

    def upsert_profile(self, profile: DeveloperProfileV2) -> int:
        """Support the shared dataclass contract as well as the legacy model."""
        self.initialize()
        payload = asdict(profile)
        if payload.get("profile_source") == "user":
            payload["profile_source"] = "user_input"
        existing = self.load_profile(profile.profile_key)
        if existing:
            payload["field_metadata"] = existing["field_metadata"]
            payload.setdefault("consent_version", existing.get("consent_version"))
        return self.save_profile(
            payload, user_id=existing.get("user_id") if existing else None
        )

    def _profile_id(
        self,
        connection: sqlite3.Connection,
        *,
        profile_key: str,
    ) -> int:
        row = connection.execute(
            """
            SELECT developer_profile_id
            FROM developer_profile
            WHERE profile_key = ?
            """,
            (profile_key,),
        ).fetchone()

        if row is None:
            raise KeyError(
                f"unknown profile: {profile_key}"
            )

        return int(
            row["developer_profile_id"]
        )

    def save_profile(
        self,
        profile: dict[str, Any],
        *,
        user_id: int | None = None,
        user_key: str | None = None,
    ) -> int:
        if not isinstance(
            profile,
            dict,
        ):
            raise ValueError(
                "profile must be an object"
            )

        profile_key = str(
            profile.get("profile_key")
            or ""
        ).strip()

        if not profile_key:
            raise ValueError(
                "profile_key is required"
            )

        display_name = str(
            profile.get("display_name")
            or ""
        ).strip()

        if not display_name:
            raise ValueError(
                "display_name is required"
            )

        service_track = str(
            profile.get("service_track")
            or ""
        ).strip()

        profile_source = str(
            profile.get("profile_source")
            or "user_input"
        ).strip()

        if (
            profile_source
            not in _ROOT_PROFILE_SOURCES
        ):
            raise ValueError(
                "unsupported profile_source: "
                f"{profile_source}"
            )

        preferred_languages = _string_list(
            profile.get(
                "preferred_languages"
            ),
            field="preferred_languages",
        )

        operating_systems = _string_list(
            profile.get(
                "operating_systems"
            ),
            field="operating_systems",
        )

        preferred_task_types = _string_list(
            profile.get(
                "preferred_task_types"
            ),
            field="preferred_task_types",
        )

        skills = profile.get(
            "skills",
            {},
        )

        if not isinstance(
            skills,
            dict,
        ):
            raise ValueError(
                "profile.skills must be an object"
            )

        field_metadata = profile.get(
            "field_metadata",
            {},
        )

        if not isinstance(
            field_metadata,
            dict,
        ):
            raise ValueError(
                "profile.field_metadata "
                "must be an object"
            )

        now = self._now()

        with self.connect() as connection:
            if user_id is not None:
                if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
                    raise ValueError("user_id must be a positive integer")
                if user_key is not None:
                    raise ValueError("user_key is for legacy local profiles only")
                owner = connection.execute(
                    "SELECT user_id FROM oss_user WHERE user_id = ? AND deleted_at IS NULL",
                    (user_id,),
                ).fetchone()
                if owner is None:
                    raise ValueError("unknown or deleted platform user")
                bound = connection.execute(
                    """SELECT b.user_id, p.profile_key FROM profile_user_binding b
                       JOIN developer_profile p USING (developer_profile_id)
                       WHERE b.user_id = ? OR p.profile_key = ?""",
                    (user_id, profile_key),
                ).fetchall()
                if any(row["user_id"] != user_id or row["profile_key"] != profile_key
                       for row in bound):
                    raise ValueError("profile ownership conflict")
            connection.execute(
                """
                INSERT INTO developer_profile (
                    profile_key,
                    display_name,
                    service_track,
                    preferred_languages_json,
                    operating_systems_json,
                    preferred_task_types_json,
                    max_code_difficulty,
                    max_setup_difficulty,
                    desired_skill_stretch,
                    profile_source,
                    consent_version,
                    created_at,
                    updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(profile_key)
                DO UPDATE SET
                    display_name = excluded.display_name,
                    service_track = excluded.service_track,
                    preferred_languages_json =
                        excluded.preferred_languages_json,
                    operating_systems_json =
                        excluded.operating_systems_json,
                    preferred_task_types_json =
                        excluded.preferred_task_types_json,
                    max_code_difficulty =
                        excluded.max_code_difficulty,
                    max_setup_difficulty =
                        excluded.max_setup_difficulty,
                    desired_skill_stretch =
                        excluded.desired_skill_stretch,
                    profile_source =
                        excluded.profile_source,
                    consent_version =
                        excluded.consent_version,
                    updated_at =
                        excluded.updated_at
                """,
                (
                    profile_key,
                    display_name,
                    service_track,
                    _json_dump(
                        preferred_languages
                    ),
                    _json_dump(
                        operating_systems
                    ),
                    _json_dump(
                        preferred_task_types
                    ),
                    int(
                        profile.get(
                            "max_code_difficulty",
                            0,
                        )
                    ),
                    int(
                        profile.get(
                            "max_setup_difficulty",
                            0,
                        )
                    ),
                    int(
                        profile.get(
                            "desired_skill_stretch",
                            0,
                        )
                    ),
                    profile_source,
                    profile.get(
                        "consent_version"
                    ),
                    now,
                    now,
                ),
            )

            profile_id = self._profile_id(
                connection,
                profile_key=profile_key,
            )

            connection.execute(
                """
                DELETE FROM developer_skill
                WHERE developer_profile_id = ?
                """,
                (profile_id,),
            )

            for skill_name in sorted(
                skills,
                key=lambda value: (
                    str(value).casefold(),
                    str(value),
                ),
            ):
                level = skills[
                    skill_name
                ]

                if (
                    isinstance(level, bool)
                    or not isinstance(
                        level,
                        int,
                    )
                    or not 0 <= level <= 4
                ):
                    raise ValueError(
                        "skill level must be "
                        "between 0 and 4"
                    )

                metadata = field_metadata.get(
                    f"skills.{skill_name}",
                    {},
                )

                if not isinstance(
                    metadata,
                    dict,
                ):
                    raise ValueError(
                        "skill field metadata "
                        "must be an object"
                    )

                evidence_source = str(
                    metadata.get(
                        "source",
                        profile_source,
                    )
                )

                connection.execute(
                    """
                    INSERT INTO developer_skill (
                        developer_profile_id,
                        skill_name,
                        skill_level,
                        evidence_source,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        profile_id,
                        str(skill_name),
                        level,
                        evidence_source,
                        now,
                    ),
                )

            connection.execute(
                """
                DELETE FROM profile_field_state
                WHERE developer_profile_id = ?
                """,
                (profile_id,),
            )

            for field_name in sorted(
                field_metadata
            ):
                metadata = field_metadata[
                    field_name
                ]

                if not isinstance(
                    metadata,
                    dict,
                ):
                    raise ValueError(
                        "field metadata "
                        "must be an object"
                    )

                source = str(
                    metadata.get(
                        "source",
                        "default",
                    )
                )

                if (
                    source
                    not in _PROFILE_SOURCES
                ):
                    raise ValueError(
                        "unsupported field "
                        f"source: {source}"
                    )

                locked = metadata.get(
                    "locked",
                    False,
                )

                if not isinstance(
                    locked,
                    bool,
                ):
                    raise ValueError(
                        "field locked must "
                        "be boolean"
                    )

                accepted_source = (
                    metadata.get(
                        "accepted_source"
                    )
                )

                if (
                    accepted_source
                    is not None
                    and accepted_source
                    not in _GITHUB_SOURCES
                ):
                    raise ValueError(
                        "invalid accepted_source"
                    )

                confidence = metadata.get(
                    "confidence"
                )

                if confidence is not None:
                    confidence = float(
                        confidence
                    )

                    if not (
                        0.0
                        <= confidence
                        <= 1.0
                    ):
                        raise ValueError(
                            "field confidence "
                            "must be between 0 and 1"
                        )

                evidence = metadata.get(
                    "evidence",
                    [],
                )

                if not isinstance(
                    evidence,
                    list,
                ):
                    raise ValueError(
                        "field evidence "
                        "must be an array"
                    )

                connection.execute(
                    """
                    INSERT INTO profile_field_state (
                        developer_profile_id,
                        field_name,
                        source,
                        locked,
                        observed_at,
                        accepted_source,
                        confidence,
                        evidence_json,
                        updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        profile_id,
                        str(field_name),
                        source,
                        int(locked),
                        metadata.get(
                            "observed_at"
                        ),
                        accepted_source,
                        confidence,
                        _json_dump(
                            evidence
                        ),
                        now,
                    ),
                )

            if user_id is not None:
                connection.execute(
                    """
                    INSERT INTO profile_user_binding (
                        user_key,
                        user_id,
                        developer_profile_id,
                        linked_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_id)
                    DO UPDATE SET
                        developer_profile_id =
                            excluded.developer_profile_id,
                        updated_at =
                            excluded.updated_at
                    """,
                    (
                        f"identity:{uuid4()}",
                        user_id,
                        profile_id,
                        now,
                        now,
                    ),
                )

            elif user_key is not None:
                if not isinstance(user_key, str) or not user_key.strip():
                    raise ValueError("user_key cannot be empty")
                existing_binding = connection.execute(
                    """SELECT user_id FROM profile_user_binding
                       WHERE user_key = ? OR developer_profile_id = ?""",
                    (user_key.strip(), profile_id),
                ).fetchall()
                if any(row["user_id"] is not None for row in existing_binding):
                    raise ValueError("cannot change authenticated ownership with user_key")
                connection.execute(
                    """INSERT INTO profile_user_binding
                       (user_key, developer_profile_id, linked_at, updated_at)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(user_key) DO UPDATE SET
                       developer_profile_id = excluded.developer_profile_id,
                       updated_at = excluded.updated_at""",
                    (user_key.strip(), profile_id, now, now),
                )

        return profile_id

    def load_profile(
        self,
        profile_key: str,
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    p.*,
                    b.user_key,
                    b.user_id
                FROM developer_profile AS p
                LEFT JOIN profile_user_binding AS b
                    ON b.developer_profile_id =
                        p.developer_profile_id
                WHERE p.profile_key = ?
                """,
                (profile_key,),
            ).fetchone()

            if row is None:
                return None

            profile_id = int(
                row[
                    "developer_profile_id"
                ]
            )

            skills = {
                str(skill["skill_name"]):
                    int(skill["skill_level"])
                for skill
                in connection.execute(
                    """
                    SELECT
                        skill_name,
                        skill_level
                    FROM developer_skill
                    WHERE developer_profile_id = ?
                    ORDER BY
                        skill_name COLLATE NOCASE,
                        skill_name
                    """,
                    (profile_id,),
                )
            }

            field_metadata: dict[
                str,
                dict[str, Any],
            ] = {}

            for state in connection.execute(
                """
                SELECT
                    field_name,
                    source,
                    locked,
                    observed_at,
                    accepted_source,
                    confidence,
                    evidence_json
                FROM profile_field_state
                WHERE developer_profile_id = ?
                ORDER BY field_name
                """,
                (profile_id,),
            ):
                metadata: dict[
                    str,
                    Any,
                ] = {
                    "source": str(
                        state["source"]
                    ),
                    "locked": bool(
                        state["locked"]
                    ),
                    "observed_at": (
                        state["observed_at"]
                    ),
                }

                if (
                    state["accepted_source"]
                    is not None
                ):
                    metadata[
                        "accepted_source"
                    ] = str(
                        state[
                            "accepted_source"
                        ]
                    )

                if (
                    state["confidence"]
                    is not None
                ):
                    metadata[
                        "confidence"
                    ] = float(
                        state["confidence"]
                    )

                evidence = _json_load(
                    state["evidence_json"],
                    default=[],
                )

                if evidence:
                    metadata[
                        "evidence"
                    ] = evidence

                field_metadata[
                    str(
                        state["field_name"]
                    )
                ] = metadata

        result: dict[str, Any] = {
            "profile_key": str(
                row["profile_key"]
            ),
            "display_name": str(
                row["display_name"]
            ),
            "service_track": str(
                row["service_track"]
            ),
            "preferred_languages":
                _json_load(
                    row[
                        "preferred_languages_json"
                    ],
                    default=[],
                ),
            "operating_systems":
                _json_load(
                    row[
                        "operating_systems_json"
                    ],
                    default=[],
                ),
            "preferred_task_types":
                _json_load(
                    row[
                        "preferred_task_types_json"
                    ],
                    default=[],
                ),
            "max_code_difficulty": int(
                row["max_code_difficulty"]
            ),
            "max_setup_difficulty": int(
                row["max_setup_difficulty"]
            ),
            "desired_skill_stretch": int(
                row["desired_skill_stretch"]
            ),
            "profile_source": str(
                row["profile_source"]
            ),
            "skills": skills,
            "field_metadata":
                field_metadata,
        }

        if row["consent_version"] is not None:
            result[
                "consent_version"
            ] = str(
                row["consent_version"]
            )

        if row["user_id"] is not None:
            result["user_id"] = int(
                row["user_id"]
            )
        elif row["user_key"] is not None:
            result["user_key"] = str(row["user_key"])

        return result

    def profile_for_user(
        self,
        user_id: int,
    ) -> dict[str, Any] | None:
        if (
            isinstance(user_id, bool)
            or not isinstance(user_id, int)
            or user_id < 1
        ):
            raise ValueError(
                "user_id must be a positive integer"
            )

        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT p.profile_key
                FROM profile_user_binding AS b
                JOIN developer_profile AS p
                    ON p.developer_profile_id =
                        b.developer_profile_id
                JOIN oss_user AS u ON u.user_id = b.user_id
                WHERE b.user_id = ? AND u.deleted_at IS NULL
                """,
                (user_id,),
            ).fetchone()

        if row is None:
            return None

        return self.load_profile(
            str(row["profile_key"])
        )

    def save_github_import(
        self,
        *,
        profile_key: str,
        github_import: dict[str, Any],
        merge_preview: dict[str, Any],
    ) -> dict[str, Any]:
        if (
            github_import.get(
                "schema_version"
            )
            != GITHUB_PROFILE_IMPORT_VERSION
        ):
            raise ValueError(
                "unsupported GitHub "
                "profile import version"
            )

        if (
            merge_preview.get(
                "schema_version"
            )
            != PROFILE_MERGE_VERSION
        ):
            raise ValueError(
                "unsupported profile "
                "merge version"
            )

        if (
            merge_preview.get(
                "profile_key"
            )
            != profile_key
        ):
            raise ValueError(
                "merge preview profile_key "
                "does not match"
            )

        consent_version = str(
            github_import.get(
                "consent_version"
            )
            or ""
        ).strip()

        if not consent_version:
            raise ValueError(
                "consent_version is required"
            )

        github_login = str(
            github_import.get(
                "github_login"
            )
            or ""
        ).strip()

        if not github_login:
            raise ValueError(
                "github_login is required"
            )

        import_key = _profile_import_key(
            github_import
        )

        imported_at = self._now()

        with self.connect() as connection:
            profile_id = self._profile_id(
                connection,
                profile_key=profile_key,
            )

            existing = connection.execute(
                """
                SELECT github_profile_import_id
                FROM github_profile_import
                WHERE developer_profile_id = ?
                  AND import_key = ?
                """,
                (
                    profile_id,
                    import_key,
                ),
            ).fetchone()

            if existing is not None:
                return {
                    "github_profile_import_id":
                        int(
                            existing[
                                "github_profile_import_id"
                            ]
                        ),
                    "import_key":
                        import_key,
                    "created": False,
                }

            cursor = connection.execute(
                """
                INSERT INTO github_profile_import (
                    developer_profile_id,
                    import_key,
                    github_login,
                    import_version,
                    consent_version,
                    observed_at,
                    imported_at,
                    public_repository_count,
                    recent_repository_count,
                    summary_json
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    profile_id,
                    import_key,
                    github_login,
                    str(
                        github_import[
                            "schema_version"
                        ]
                    ),
                    consent_version,
                    str(
                        github_import.get(
                            "observed_at"
                        )
                        or ""
                    ),
                    imported_at,
                    int(
                        github_import.get(
                            "public_repository_count",
                            0,
                        )
                    ),
                    int(
                        github_import.get(
                            "recent_active_repository_count",
                            0,
                        )
                    ),
                    _json_dump(
                        github_import
                    ),
                ),
            )

            if cursor.lastrowid is None:
                raise RuntimeError(
                    "failed to save GitHub "
                    "profile import"
                )

            import_id = int(
                cursor.lastrowid
            )

            suggestions = merge_preview.get(
                "suggestions",
                [],
            )

            if not isinstance(
                suggestions,
                list,
            ):
                raise ValueError(
                    "merge_preview.suggestions "
                    "must be an array"
                )

            for suggestion in suggestions:
                if not isinstance(
                    suggestion,
                    dict,
                ):
                    raise ValueError(
                        "suggestion must "
                        "be an object"
                    )

                source = str(
                    suggestion.get(
                        "source"
                    )
                    or ""
                )

                if (
                    source
                    not in _GITHUB_SOURCES
                ):
                    raise ValueError(
                        "invalid suggestion source"
                    )

                confidence = float(
                    suggestion.get(
                        "confidence",
                        0.0,
                    )
                )

                if not (
                    0.0
                    <= confidence
                    <= 1.0
                ):
                    raise ValueError(
                        "suggestion confidence "
                        "must be between 0 and 1"
                    )

                status = str(
                    suggestion.get(
                        "status",
                        "pending",
                    )
                )

                if (
                    status
                    not in _SUGGESTION_STATUSES
                ):
                    raise ValueError(
                        "invalid suggestion status"
                    )

                current_value = (
                    suggestion.get(
                        "current_value"
                    )
                )

                connection.execute(
                    """
                    INSERT INTO profile_field_suggestion (
                        github_profile_import_id,
                        developer_profile_id,
                        field_name,
                        current_value_json,
                        proposed_value_json,
                        suggestion_source,
                        confidence,
                        evidence_json,
                        observed_at,
                        status,
                        blocked_reason,
                        resolved_at,
                        created_at,
                        updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?
                    )
                    """,
                    (
                        import_id,
                        profile_id,
                        str(
                            suggestion[
                                "field"
                            ]
                        ),
                        (
                            None
                            if current_value
                            is None
                            else _json_dump(
                                current_value
                            )
                        ),
                        _json_dump(
                            suggestion.get(
                                "proposed_value"
                            )
                        ),
                        source,
                        confidence,
                        _json_dump(
                            suggestion.get(
                                "evidence",
                                [],
                            )
                        ),
                        str(
                            suggestion.get(
                                "observed_at"
                            )
                            or github_import.get(
                                "observed_at"
                            )
                            or ""
                        ),
                        status,
                        suggestion.get(
                            "blocked_reason"
                        ),
                        imported_at,
                        imported_at,
                    ),
                )

            import_suggestions = (
                github_import.get(
                    "suggestions",
                    {}
                )
            )

            if not isinstance(
                import_suggestions,
                dict,
            ):
                raise ValueError(
                    "github_import.suggestions "
                    "must be an object"
                )

            skill_rows = (
                import_suggestions.get(
                    "skills",
                    [],
                )
            )

            if not isinstance(
                skill_rows,
                list,
            ):
                raise ValueError(
                    "GitHub skill suggestions "
                    "must be an array"
                )

            for skill in skill_rows:
                if not isinstance(
                    skill,
                    dict,
                ):
                    raise ValueError(
                        "skill evidence "
                        "must be an object"
                    )

                source = str(
                    skill.get(
                        "source"
                    )
                    or ""
                )

                if source not in _GITHUB_SOURCES:
                    raise ValueError(
                        "invalid skill "
                        "evidence source"
                    )

                confidence = float(
                    skill.get(
                        "confidence",
                        0.0,
                    )
                )

                connection.execute(
                    """
                    INSERT INTO developer_skill_evidence (
                        developer_profile_id,
                        github_profile_import_id,
                        skill_name,
                        evidence_source,
                        confidence,
                        evidence_json,
                        observed_at,
                        created_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        profile_id,
                        import_id,
                        str(
                            skill[
                                "skill_name"
                            ]
                        ),
                        source,
                        confidence,
                        _json_dump(
                            skill.get(
                                "evidence",
                                [],
                            )
                        ),
                        str(
                            skill.get(
                                "observed_at"
                            )
                            or github_import.get(
                                "observed_at"
                            )
                            or ""
                        ),
                        imported_at,
                    ),
                )

        return {
            "github_profile_import_id":
                import_id,
            "import_key":
                import_key,
            "created": True,
        }

    def list_suggestions(
        self,
        *,
        profile_key: str,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        if (
            status is not None
            and status
            not in _SUGGESTION_STATUSES
        ):
            raise ValueError(
                "invalid suggestion status"
            )

        query = """
            SELECT
                s.profile_field_suggestion_id,
                s.field_name,
                s.current_value_json,
                s.proposed_value_json,
                s.suggestion_source,
                s.confidence,
                s.evidence_json,
                s.observed_at,
                s.status,
                s.blocked_reason,
                s.resolved_at,
                i.import_key,
                i.github_login
            FROM profile_field_suggestion AS s
            JOIN github_profile_import AS i
                ON i.github_profile_import_id =
                    s.github_profile_import_id
            JOIN developer_profile AS p
                ON p.developer_profile_id =
                    s.developer_profile_id
            WHERE p.profile_key = ?
        """

        parameters: list[Any] = [
            profile_key
        ]

        if status is not None:
            query += """
                AND s.status = ?
            """
            parameters.append(
                status
            )

        query += """
            ORDER BY
                s.created_at,
                s.profile_field_suggestion_id
        """

        with self.connect() as connection:
            rows = connection.execute(
                query,
                tuple(parameters),
            ).fetchall()

        return [
            {
                "profile_field_suggestion_id":
                    int(
                        row[
                            "profile_field_suggestion_id"
                        ]
                    ),
                "field_name": str(
                    row["field_name"]
                ),
                "field": str(row["field_name"]),  # Compatibility alias.
                "source": str(row["suggestion_source"]),
                "current_value":
                    _json_load(
                        row[
                            "current_value_json"
                        ],
                        default=None,
                    ),
                "proposed_value":
                    _json_load(
                        row[
                            "proposed_value_json"
                        ],
                        default=None,
                    ),
                "suggestion_source": str(
                    row[
                        "suggestion_source"
                    ]
                ),
                "confidence": float(
                    row["confidence"]
                ),
                "evidence":
                    _json_load(
                        row[
                            "evidence_json"
                        ],
                        default=[],
                    ),
                "observed_at":
                    str(
                        row[
                            "observed_at"
                        ]
                    ),
                "status": str(
                    row["status"]
                ),
                "blocked_reason":
                    row[
                        "blocked_reason"
                    ],
                "resolved_at":
                    row["resolved_at"],
                "import_key":
                    str(
                        row["import_key"]
                    ),
                "github_login":
                    str(
                        row["github_login"]
                    ),
            }
            for row in rows
        ]

    def mark_suggestion_status(
        self,
        suggestion_id: int,
        *,
        status: str,
    ) -> None:
        if status not in {
            "accepted",
            "rejected",
        }:
            raise ValueError(
                "status must be accepted "
                "or rejected"
            )

        now = self._now()

        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT status
                FROM profile_field_suggestion
                WHERE profile_field_suggestion_id = ?
                """,
                (suggestion_id,),
            ).fetchone()

            if row is None:
                raise KeyError(
                    "unknown profile suggestion"
                )

            current_status = str(
                row["status"]
            )

            if current_status == status:
                return

            if current_status != "pending":
                raise ValueError(
                    "resolved suggestion "
                    "cannot change status"
                )

            connection.execute(
                """
                UPDATE profile_field_suggestion
                SET
                    status = ?,
                    resolved_at = ?,
                    updated_at = ?
                WHERE profile_field_suggestion_id = ?
                """,
                (
                    status,
                    now,
                    now,
                    suggestion_id,
                ),
            )

    def delete_profile(
        self,
        profile_key: str,
    ) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM developer_profile
                WHERE profile_key = ?
                """,
                (profile_key,),
            )

            return cursor.rowcount > 0


class SQLiteMidtermProfileStore(SQLiteProfileStorage):
    """SQLite implementation of D's frozen midterm profile protocol."""

    def _owned_profile(
        self,
        connection: sqlite3.Connection,
        user_id: int,
    ) -> tuple[int, str]:
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
            raise ValueError("user_id must be a positive integer")
        row = connection.execute(
            """
            SELECT p.developer_profile_id, p.profile_key
            FROM profile_user_binding AS b
            JOIN developer_profile AS p
              ON p.developer_profile_id = b.developer_profile_id
            JOIN oss_user AS u ON u.user_id = b.user_id
            WHERE b.user_id = ? AND u.deleted_at IS NULL
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            raise ValueError("authenticated user has no owned developer profile")
        return int(row["developer_profile_id"]), str(row["profile_key"])

    def _require_transaction(self) -> sqlite3.Connection:
        connection = getattr(self, "_transaction_connection", None)
        if connection is None:
            raise RuntimeError("confirmation commit requires store.transaction()")
        return connection

    def put_questionnaire(
        self,
        *,
        questionnaire_id: str,
        questionnaire_version: str,
        schema_version: str,
        feature_schema_version: str,
        definition: dict,
        definition_sha256: str,
    ) -> None:
        identity = (
            _required_text(questionnaire_id, "questionnaire_id"),
            _required_text(questionnaire_version, "questionnaire_version"),
        )
        if not isinstance(definition, dict):
            raise ValueError("definition must be an object")
        payload = _json_dump(definition)
        digest = _required_text(definition_sha256, "definition_sha256")
        with self.connect() as connection:
            existing = connection.execute(
                """
                SELECT schema_version, feature_schema_version,
                       definition_json, definition_sha256
                FROM profile_questionnaire
                WHERE questionnaire_id = ? AND questionnaire_version = ?
                """,
                identity,
            ).fetchone()
            values = (
                _required_text(schema_version, "schema_version"),
                _required_text(feature_schema_version, "feature_schema_version"),
                payload,
                digest,
            )
            if existing is not None:
                current = tuple(str(existing[key]) for key in (
                    "schema_version",
                    "feature_schema_version",
                    "definition_json",
                    "definition_sha256",
                ))
                if current != values:
                    raise IdempotencyConflictError(
                        "questionnaire version already has a different definition"
                    )
                return
            connection.execute(
                """
                INSERT INTO profile_questionnaire (
                    questionnaire_id, questionnaire_version, schema_version,
                    feature_schema_version, definition_json,
                    definition_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (*identity, *values, self._now()),
            )

    def get_questionnaire(
        self,
        *,
        questionnaire_id: str,
        questionnaire_version: str,
    ) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM profile_questionnaire
                WHERE questionnaire_id = ? AND questionnaire_version = ?
                """,
                (questionnaire_id, questionnaire_version),
            ).fetchone()
        if row is None:
            return None
        return {
            "questionnaire_id": str(row["questionnaire_id"]),
            "questionnaire_version": str(row["questionnaire_version"]),
            "schema_version": str(row["schema_version"]),
            "feature_schema_version": str(row["feature_schema_version"]),
            "definition": json.loads(row["definition_json"]),
            "definition_sha256": str(row["definition_sha256"]),
            "created_at": str(row["created_at"]),
        }

    def create_assessment_submission(
        self,
        *,
        user_id: int,
        submission: AssessmentSubmission,
        idempotency_key: str,
    ) -> str:
        if getattr(self, "_transaction_connection", None) is None:
            with self.transaction() as transaction:
                return transaction.create_assessment_submission(
                    user_id=user_id,
                    submission=submission,
                    idempotency_key=idempotency_key,
                )
        key = _required_text(idempotency_key, "idempotency_key")
        payload = submission.to_dict()
        with self.connect() as connection:
            profile_id, _ = self._owned_profile(connection, user_id)
            questionnaire = connection.execute(
                """
                SELECT 1 FROM profile_questionnaire
                WHERE questionnaire_id = ? AND questionnaire_version = ?
                """,
                (submission.questionnaire_id, submission.questionnaire_version),
            ).fetchone()
            if questionnaire is None:
                raise ValueError("unknown questionnaire version")
            existing = connection.execute(
                """
                SELECT * FROM profile_assessment_submission
                WHERE user_id = ? AND idempotency_key = ?
                """,
                (user_id, key),
            ).fetchone()
            if existing is not None:
                current = {
                    "questionnaire_id": str(existing["questionnaire_id"]),
                    "questionnaire_version": str(existing["questionnaire_version"]),
                    "submitted_at": str(existing["submitted_at"]),
                    "answers": json.loads(existing["answers_json"]),
                }
                if current != payload:
                    raise IdempotencyConflictError(
                        "submission idempotency key was reused with another payload"
                    )
                return str(existing["submission_id"])
            submission_id = f"assessment-submission:{uuid4()}"
            connection.execute(
                """
                INSERT INTO profile_assessment_submission (
                    submission_id, user_id, developer_profile_id,
                    questionnaire_id, questionnaire_version, submitted_at,
                    answers_json, idempotency_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    submission_id,
                    user_id,
                    profile_id,
                    submission.questionnaire_id,
                    submission.questionnaire_version,
                    submission.submitted_at,
                    _json_dump(submission.answers),
                    key,
                    self._now(),
                ),
            )
            return submission_id

    def get_assessment_submission(
        self,
        *,
        user_id: int,
        submission_id: str,
    ) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM profile_assessment_submission
                WHERE user_id = ? AND submission_id = ?
                """,
                (user_id, submission_id),
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

    def create_profile_preview(
        self,
        *,
        user_id: int,
        preview: ProfilePreview,
        input_sha256: str,
    ) -> str:
        if getattr(self, "_transaction_connection", None) is None:
            with self.transaction() as transaction:
                return transaction.create_profile_preview(
                    user_id=user_id,
                    preview=preview,
                    input_sha256=input_sha256,
                )
        input_digest = _required_text(input_sha256, "input_sha256")
        payload = preview.to_dict()
        payload_digest = _json_sha256(payload)
        with self.connect() as connection:
            profile_id, profile_key = self._owned_profile(connection, user_id)
            if preview.profile_key != profile_key:
                raise ValueError("preview does not belong to the authenticated user")
            submission = connection.execute(
                """
                SELECT submission_id FROM profile_assessment_submission
                WHERE user_id = ? AND developer_profile_id = ?
                  AND questionnaire_id = ? AND questionnaire_version = ?
                  AND submitted_at = ? AND answers_json = ?
                ORDER BY created_at DESC, submission_id DESC LIMIT 1
                """,
                (
                    user_id,
                    profile_id,
                    preview.questionnaire_id,
                    preview.questionnaire_version,
                    preview.submitted_at,
                    _json_dump(preview.answers),
                ),
            ).fetchone()
            if submission is None:
                raise ValueError("preview has no matching persisted submission")
            existing = connection.execute(
                """SELECT preview_json, preview_sha256, input_sha256
                   FROM profile_assessment_preview WHERE preview_id = ?""",
                (preview.preview_id,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["preview_json"]) != _json_dump(payload)
                    or str(existing["preview_sha256"]) != payload_digest
                    or str(existing["input_sha256"]) != input_digest
                ):
                    raise IdempotencyConflictError(
                        "preview id already has a different payload"
                    )
                return preview.preview_id
            connection.execute(
                """
                INSERT INTO profile_assessment_preview (
                    preview_id, user_id, developer_profile_id, submission_id,
                    base_profile_version, questionnaire_id,
                    questionnaire_version, feature_schema_version,
                    submitted_at, input_sha256, preview_json,
                    preview_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    preview.preview_id,
                    user_id,
                    profile_id,
                    submission["submission_id"],
                    preview.base_profile_version,
                    preview.questionnaire_id,
                    preview.questionnaire_version,
                    preview.feature_schema_version,
                    preview.submitted_at,
                    input_digest,
                    _json_dump(payload),
                    payload_digest,
                    self._now(),
                ),
            )
            for feature in preview.features:
                connection.execute(
                    """
                    INSERT INTO profile_assessment_preview_feature (
                        preview_id, feature_name, value, confidence,
                        observed, locked, conflict, source_kind,
                        blocked_reason, evidence_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        preview.preview_id,
                        feature.feature_name,
                        feature.proposed_value,
                        feature.confidence,
                        int(feature.observed),
                        int(
                            feature.source_kind == "user_locked"
                            or feature.blocked_reason == "field_locked"
                        ),
                        int(feature.conflict),
                        feature.source_kind,
                        feature.blocked_reason,
                        _json_dump(list(feature.evidence_refs)),
                    ),
                )
            return preview.preview_id

    @staticmethod
    def _preview_from_payload(payload: dict[str, Any]) -> ProfilePreview:
        return ProfilePreview(
            preview_id=payload["preview_id"],
            profile_key=payload["profile_key"],
            base_profile_version=payload["base_profile_version"],
            questionnaire_id=payload["questionnaire_id"],
            questionnaire_version=payload["questionnaire_version"],
            feature_schema_version=payload["feature_schema_version"],
            submitted_at=payload["submitted_at"],
            answers=dict(payload["answers"]),
            features=tuple(ProfileFeature.from_dict(row) for row in payload["features"]),
            current_interests=tuple(payload["current_interests"]),
            interpretation=payload["interpretation"],
            persisted=True,
        )

    def get_profile_preview(
        self,
        *,
        user_id: int,
        preview_id: str,
    ) -> ProfilePreview | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT preview_json, preview_sha256 FROM profile_assessment_preview
                   WHERE user_id = ? AND preview_id = ?""",
                (user_id, preview_id),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["preview_json"])
        if _json_sha256(payload) != str(row["preview_sha256"]):
            raise ValueError("persisted preview checksum mismatch")
        return self._preview_from_payload(payload)

    def create_confirmation_plan(
        self,
        *,
        user_id: int,
        plan: ConfirmationPlan,
    ) -> str:
        if getattr(self, "_transaction_connection", None) is None:
            with self.transaction() as transaction:
                return transaction.create_confirmation_plan(
                    user_id=user_id,
                    plan=plan,
                )
        payload = plan.to_dict()
        payload_digest = _json_sha256(payload)
        with self.connect() as connection:
            profile_id, profile_key = self._owned_profile(connection, user_id)
            if plan.profile_key != profile_key:
                raise ValueError("plan does not belong to the authenticated user")
            preview = connection.execute(
                """
                SELECT base_profile_version, questionnaire_version,
                       feature_schema_version
                FROM profile_assessment_preview
                WHERE user_id = ? AND developer_profile_id = ? AND preview_id = ?
                """,
                (user_id, profile_id, plan.preview_id),
            ).fetchone()
            if preview is None:
                raise ValueError("plan references an unknown preview")
            if (
                str(preview["base_profile_version"]) != plan.base_profile_version
                or str(preview["questionnaire_version"]) != plan.questionnaire_version
                or str(preview["feature_schema_version"]) != plan.feature_schema_version
            ):
                raise ValueError("plan does not match its persisted preview")
            existing = connection.execute(
                "SELECT plan_json, plan_sha256 FROM profile_assessment_plan WHERE plan_id = ?",
                (plan.plan_id,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["plan_json"]) != _json_dump(payload)
                    or str(existing["plan_sha256"]) != payload_digest
                ):
                    raise IdempotencyConflictError("plan id already has a different payload")
                return plan.plan_id
            connection.execute(
                """
                INSERT INTO profile_assessment_plan (
                    plan_id, user_id, developer_profile_id, preview_id,
                    profile_key, base_profile_version, questionnaire_version,
                    feature_schema_version, plan_json, plan_sha256,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.plan_id,
                    user_id,
                    profile_id,
                    plan.preview_id,
                    profile_key,
                    plan.base_profile_version,
                    plan.questionnaire_version,
                    plan.feature_schema_version,
                    _json_dump(payload),
                    payload_digest,
                    plan.status,
                    plan.created_at,
                ),
            )
            for suggestion in plan.suggestions:
                connection.execute(
                    """
                    INSERT INTO profile_assessment_suggestion (
                        plan_id, suggestion_id, feature_name, current_json,
                        proposed_json, conflict, status, blocked_reason, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plan.plan_id,
                        suggestion.suggestion_id,
                        suggestion.feature_name,
                        _json_dump(suggestion.current.to_dict()),
                        _json_dump(suggestion.proposed.to_dict()),
                        int(suggestion.conflict),
                        suggestion.status,
                        suggestion.blocked_reason,
                        plan.created_at,
                    ),
                )
            return plan.plan_id

    @staticmethod
    def _plan_from_payload(payload: dict[str, Any], status: str) -> ConfirmationPlan:
        return ConfirmationPlan(
            plan_id=payload["plan_id"],
            profile_key=payload["profile_key"],
            base_profile_version=payload["base_profile_version"],
            preview_id=payload["preview_id"],
            questionnaire_version=payload["questionnaire_version"],
            feature_schema_version=payload["feature_schema_version"],
            preview_submitted_at=payload["preview_submitted_at"],
            suggestions=tuple(
                ConfirmationSuggestion.from_dict(row) for row in payload["suggestions"]
            ),
            created_at=payload["created_at"],
            status=status,
            persisted=True,
        )

    def get_confirmation_plan(
        self,
        *,
        user_id: int,
        plan_id: str,
    ) -> ConfirmationPlan | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT plan_json, plan_sha256, status FROM profile_assessment_plan
                   WHERE user_id = ? AND plan_id = ?""",
                (user_id, plan_id),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["plan_json"])
        if _json_sha256(payload) != str(row["plan_sha256"]):
            raise ValueError("persisted confirmation plan checksum mismatch")
        return self._plan_from_payload(payload, str(row["status"]))

    def _state_from_row(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> ProfileFeatureState:
        features = tuple(
            ProfileFeatureStateRow(
                feature_name=str(item["feature_name"]),
                observed=bool(item["observed"]),
                value=float(item["value"]),
                locked=bool(item["locked"]),
                evidence_refs=tuple(json.loads(item["evidence_json"])),
            )
            for item in connection.execute(
                """
                SELECT * FROM developer_profile_feature
                WHERE feature_state_id = ? ORDER BY feature_name
                """,
                (row["feature_state_id"],),
            ).fetchall()
        )
        state = ProfileFeatureState(
            profile_key=str(row["profile_key"]),
            profile_version=str(row["profile_version"]),
            feature_schema_version=str(row["feature_schema_version"]),
            updated_at=str(row["created_at"]),
            features=features,
        )
        if _json_sha256(state.to_dict()) != str(row["state_sha256"]):
            raise ValueError("persisted feature state checksum mismatch")
        return state

    def get_current_feature_state(
        self,
        *,
        user_id: int,
    ) -> ProfileFeatureState | None:
        with self.connect() as connection:
            profile_id, _ = self._owned_profile(connection, user_id)
            row = connection.execute(
                """
                SELECT s.* FROM developer_profile_current_feature AS c
                JOIN developer_profile_feature_state AS s
                  ON s.feature_state_id = c.feature_state_id
                WHERE c.developer_profile_id = ?
                """,
                (profile_id,),
            ).fetchone()
            return None if row is None else self._state_from_row(connection, row)

    def get_feature_state(
        self,
        *,
        user_id: int,
        profile_version: str,
    ) -> ProfileFeatureState | None:
        with self.connect() as connection:
            profile_id, _ = self._owned_profile(connection, user_id)
            row = connection.execute(
                """
                SELECT * FROM developer_profile_feature_state
                WHERE developer_profile_id = ? AND profile_version = ?
                """,
                (profile_id, profile_version),
            ).fetchone()
            return None if row is None else self._state_from_row(connection, row)

    def find_confirmation_by_idempotency_key(
        self,
        *,
        user_id: int,
        idempotency_key: str,
    ) -> AssessmentConfirmation | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT result_json, result_sha256 FROM profile_assessment_confirmation
                WHERE user_id = ? AND idempotency_key = ?
                """,
                (user_id, idempotency_key),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["result_json"])
        if _json_sha256(payload) != str(row["result_sha256"]):
            raise ValueError("persisted confirmation checksum mismatch")
        return AssessmentConfirmation.from_private(payload)

    def commit_assessment_confirmation(
        self,
        *,
        user_id: int,
        confirmation: AssessmentConfirmation,
        decisions: tuple[ConfirmationDecision, ...],
        feature_state: ProfileFeatureState,
        idempotency_key: str,
    ) -> AssessmentConfirmation:
        connection = self._require_transaction()
        key = _required_text(idempotency_key, "idempotency_key")
        profile_id, profile_key = self._owned_profile(connection, user_id)
        if tuple(decisions) != confirmation.decisions:
            raise ValueError("decisions must match the confirmation result")
        if feature_state != confirmation.profile_state:
            raise ValueError("feature_state must match the confirmation result")
        if confirmation.profile_key != profile_key or feature_state.profile_key != profile_key:
            raise ValueError("confirmation does not belong to the authenticated user")

        existing = self.find_confirmation_by_idempotency_key(
            user_id=user_id,
            idempotency_key=key,
        )
        if existing is not None:
            if existing == confirmation:
                return existing
            raise IdempotencyConflictError(
                "confirmation idempotency key was reused with another payload"
            )

        plan = self.get_confirmation_plan(user_id=user_id, plan_id=confirmation.plan_id)
        if plan is None:
            raise ValueError("confirmation references an unknown plan")
        if plan.status != "open":
            raise IdempotencyConflictError("confirmation plan is no longer open")
        if (
            plan.profile_key != profile_key
            or plan.base_profile_version != confirmation.base_profile_version
        ):
            raise ValueError("confirmation does not match its persisted plan")
        current = self.get_current_feature_state(user_id=user_id)
        if current is not None and current.profile_version != plan.base_profile_version:
            raise StalePlanError("confirmation plan is based on a stale profile version")

        suggestions = {item.suggestion_id: item for item in plan.suggestions}
        for decision in decisions:
            suggestion = suggestions.get(decision.suggestion_id)
            if suggestion is None:
                raise ValueError("decision references an unknown suggestion")
            if suggestion.status != "pending":
                raise ValueError("only pending suggestions can be decided")
            if (
                decision.feature_name != suggestion.feature_name
                or decision.conflict_was_visible != suggestion.conflict
            ):
                raise ValueError("decision does not match its persisted suggestion")

        plan_confirmation = connection.execute(
            """SELECT result_json FROM profile_assessment_confirmation
               WHERE plan_id = ?""",
            (plan.plan_id,),
        ).fetchone()
        if plan_confirmation is not None:
            raise IdempotencyConflictError("confirmation plan was already committed")

        result_payload = confirmation.to_dict()
        state_payload = feature_state.to_dict()
        state_id = f"feature-state:{uuid4()}"
        now = self._now()
        connection.execute(
            """
            INSERT INTO profile_assessment_confirmation (
                confirmation_id, plan_id, user_id, developer_profile_id,
                profile_key, base_profile_version, new_profile_version,
                confirmed_at, result_json, result_sha256,
                idempotency_key, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                confirmation.confirmation_id,
                confirmation.plan_id,
                user_id,
                profile_id,
                profile_key,
                confirmation.base_profile_version,
                confirmation.new_profile_version,
                confirmation.confirmed_at,
                _json_dump(result_payload),
                _json_sha256(result_payload),
                key,
                now,
            ),
        )
        for decision in decisions:
            connection.execute(
                """
                INSERT INTO profile_assessment_decision (
                    confirmation_id, plan_id, suggestion_id, feature_name,
                    decision, conflict_was_visible, decided_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    confirmation.confirmation_id,
                    confirmation.plan_id,
                    decision.suggestion_id,
                    decision.feature_name,
                    decision.decision,
                    int(decision.conflict_was_visible),
                    decision.decided_at,
                ),
            )
            connection.execute(
                """
                UPDATE profile_assessment_suggestion SET status = ?
                WHERE plan_id = ? AND suggestion_id = ?
                """,
                (
                    "accepted" if decision.decision == "accept" else "rejected",
                    confirmation.plan_id,
                    decision.suggestion_id,
                ),
            )
        connection.execute(
            """
            INSERT INTO developer_profile_feature_state (
                feature_state_id, developer_profile_id, profile_key,
                profile_version, feature_schema_version, confirmation_id,
                state_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                state_id,
                profile_id,
                profile_key,
                feature_state.profile_version,
                feature_state.feature_schema_version,
                confirmation.confirmation_id,
                _json_sha256(state_payload),
                feature_state.updated_at,
            ),
        )
        for feature in feature_state.features:
            connection.execute(
                """
                INSERT INTO developer_profile_feature (
                    feature_state_id, feature_name, observed,
                    value, locked, evidence_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    state_id,
                    feature.feature_name,
                    int(feature.observed),
                    feature.value,
                    int(feature.locked),
                    _json_dump(list(feature.evidence_refs)),
                ),
            )
        connection.execute(
            """
            INSERT INTO developer_profile_current_feature (
                developer_profile_id, feature_state_id, updated_at
            ) VALUES (?, ?, ?)
            ON CONFLICT(developer_profile_id) DO UPDATE SET
                feature_state_id = excluded.feature_state_id,
                updated_at = excluded.updated_at
            """,
            (profile_id, state_id, feature_state.updated_at),
        )
        connection.execute(
            "UPDATE profile_assessment_plan SET status = 'completed' WHERE plan_id = ?",
            (confirmation.plan_id,),
        )
        return confirmation

    def get_enhanced_profile_features(
        self,
        *,
        user_id: int,
        profile_version: str | None = None,
        developer_key: str | None = None,
    ) -> EnhancedProfileFeatures | None:
        external_developer_key = (
            None
            if developer_key is None
            else _required_text(developer_key, "developer_key")
        )
        state = (
            self.get_current_feature_state(user_id=user_id)
            if profile_version is None
            else self.get_feature_state(user_id=user_id, profile_version=profile_version)
        )
        if state is None:
            return None
        if external_developer_key == state.profile_key:
            raise ValueError("developer_key cannot be the platform profile_key")
        names = tuple(item.feature_name for item in state.features)
        return EnhancedProfileFeatures(
            profile_key=state.profile_key,
            # The caller may inject only a separately verified external identity.
            # A missing mapping remains None so model consumers take cold-start.
            developer_key=external_developer_key,
            profile_version=state.profile_version,
            feature_schema_version=state.feature_schema_version,
            as_of=state.updated_at,
            feature_names=names,
            values=tuple(item.value for item in state.features),
            observed_mask=tuple(item.observed for item in state.features),
            locked_mask=tuple(item.locked for item in state.features),
            current_interests=tuple(
                item.feature_name
                for item in state.features
                if item.feature_name.startswith("current_interest.")
                and item.observed
                and item.value > 0.0
            ),
            evidence_refs=tuple(item.evidence_refs for item in state.features),
        )
