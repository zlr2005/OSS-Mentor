"""SQLite persistence for developer profiles and GitHub profile evidence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Protocol

from oss_mentor.developer_profiles import (
    GITHUB_PROFILE_IMPORT_VERSION,
    PROFILE_MERGE_VERSION,
)
from oss_mentor.sqlite_store import SQLiteCandidateStore


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


class ProfileStorage(Protocol):
    """Business-facing persistence contract for B's profile feature."""

    def initialize(self) -> None:
        ...

    def save_profile(
        self,
        profile: dict[str, Any],
        *,
        user_key: str | None = None,
    ) -> int:
        ...

    def load_profile(
        self,
        profile_key: str,
    ) -> dict[str, Any] | None:
        ...

    def save_github_import(
        self,
        *,
        profile_key: str,
        github_import: dict[str, Any],
        merge_preview: dict[str, Any],
    ) -> dict[str, Any]:
        ...

    def list_suggestions(
        self,
        *,
        profile_key: str,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        ...

    def mark_suggestion_status(
        self,
        suggestion_id: int,
        *,
        status: str,
    ) -> None:
        ...

    def delete_profile(
        self,
        profile_key: str,
    ) -> bool:
        ...


class SQLiteProfileStorage(
    SQLiteCandidateStore
):
    """SQLite implementation of the profile persistence contract."""

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

            if user_key is not None:
                normalized_user_key = str(
                    user_key
                ).strip()

                if not normalized_user_key:
                    raise ValueError(
                        "user_key cannot be empty"
                    )

                connection.execute(
                    """
                    INSERT INTO profile_user_binding (
                        user_key,
                        developer_profile_id,
                        linked_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_key)
                    DO UPDATE SET
                        developer_profile_id =
                            excluded.developer_profile_id,
                        updated_at =
                            excluded.updated_at
                    """,
                    (
                        normalized_user_key,
                        profile_id,
                        now,
                        now,
                    ),
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
                    b.user_key
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

        if row["user_key"] is not None:
            result["user_key"] = str(
                row["user_key"]
            )

        return result

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
                "field": str(
                    row["field_name"]
                ),
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
                "source": str(
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