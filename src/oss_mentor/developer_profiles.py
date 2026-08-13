"""Validation for consent-aware local developer profile imports."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CUSTOM_PROFILE_VERSION = "custom-profile-v0.2"
ALLOWED_SERVICE_TRACKS = {"newcomer", "growth"}
ALLOWED_OPERATING_SYSTEMS = {"windows", "macos", "linux"}
ALLOWED_LANGUAGES = {"python", "javascript", "typescript", "java", "go", "rust"}
LANGUAGE_DISPLAY_NAMES = {
    "python": "Python",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "java": "Java",
    "go": "Go",
    "rust": "Rust",
}
ALLOWED_TASK_TYPES = {
    "bug_fix",
    "testing",
    "documentation",
    "feature",
    "refactor",
    "build_tooling",
}
GITHUB_PROFILE_INPUT_SCHEMA_VERSION = "github-profile-input-v0.1"
GITHUB_PROFILE_IMPORT_VERSION = "github-profile-import-v0.1"
GITHUB_PROFILE_RECENCY_DAYS = 180

GITHUB_PROFILE_SOURCE_EXPLICIT = "github_explicit_evidence"
GITHUB_PROFILE_SOURCE_WEAK = "github_weak_inference"

_PROFILE_ACTIVITY_FIELDS = (
    "commits",
    "pull_requests",
    "issues",
    "reviews",
)

_PROFILE_SKILL_NAMES = (
    "testing",
    "documentation",
    "build_tooling",
)


@dataclass(frozen=True, slots=True)
class DeveloperProfile:
    profile_key: str
    display_name: str
    service_track: str
    preferred_languages: tuple[str, ...]
    operating_systems: tuple[str, ...]
    preferred_task_types: tuple[str, ...]
    max_code_difficulty: int
    max_setup_difficulty: int
    desired_skill_stretch: int
    profile_source: str
    skills: dict[str, int]
    consent_version: str | None = None

def _parse_profile_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a UTC ISO 8601 string")

    raw = value.strip()
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(
            f"{field} must be a valid ISO 8601 timestamp"
        ) from exc

    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")

    return parsed.astimezone(timezone.utc)


def _profile_iso_z(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _non_negative_int(value: Any, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
    ):
        raise ValueError(
            f"{field} must be a non-negative integer"
        )
    return value


def _repository_last_contribution_at(
    repository: dict[str, Any],
) -> datetime | None:
    raw = repository.get("last_contribution_at")
    if raw is None:
        return None

    return _parse_profile_datetime(
        raw,
        "repositories[].last_contribution_at",
    )


def _normalized_path(value: str) -> str:
    return (
        value.strip()
        .replace("\\", "/")
        .casefold()
    )


def _path_skill_categories(path: str) -> tuple[str, ...]:
    normalized = _normalized_path(path)
    basename = normalized.rsplit("/", 1)[-1]

    categories: list[str] = []

    testing_names = {
        "pytest.ini",
        "tox.ini",
        "conftest.py",
        "jest.config.js",
        "jest.config.cjs",
        "jest.config.mjs",
        "jest.config.ts",
    }

    if (
        normalized.startswith("tests/")
        or "/tests/" in normalized
        or normalized.startswith("test/")
        or "/test/" in normalized
        or basename in testing_names
        or basename.startswith("test_")
        or basename.endswith("_test.py")
        or basename.endswith(".test.js")
        or basename.endswith(".test.ts")
        or basename.endswith(".spec.js")
        or basename.endswith(".spec.ts")
    ):
        categories.append("testing")

    if (
        normalized.startswith("docs/")
        or "/docs/" in normalized
        or basename.startswith("readme")
        or basename.startswith("contributing")
        or basename in {
            "mkdocs.yml",
            "mkdocs.yaml",
            "conf.py",
        }
    ):
        categories.append("documentation")

    build_names = {
        "pyproject.toml",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        "gradle.properties",
        "cargo.toml",
        "makefile",
        "dockerfile",
    }

    if (
        basename in build_names
        or normalized.startswith(".github/workflows/")
        or "/.github/workflows/" in normalized
    ):
        categories.append("build_tooling")

    return tuple(categories)


def _language_profile_confidence(
    share: float,
    repository_count: int,
) -> float:
    """
    Confidence means confidence that the language is commonly used.

    It is deliberately not a proficiency score.
    """
    score = (
        0.35
        + min(0.30, share * 0.30)
        + min(0.15, repository_count * 0.05)
    )
    return round(min(0.80, score), 2)


def _skill_profile_confidence(
    repository_count: int,
    evidence_count: int,
) -> float:
    score = (
        0.50
        + min(0.20, repository_count * 0.08)
        + min(0.20, evidence_count * 0.04)
    )
    return round(min(0.90, score), 2)


def build_github_profile_import(
    payload: Any,
) -> dict[str, Any]:
    """
    Build deterministic profile suggestions from sanitized public
    GitHub evidence.

    This function performs no network requests.

    Contribution volume may affect activity summaries and confidence,
    but it never directly determines developer proficiency.
    """

    if not isinstance(payload, dict):
        raise ValueError(
            "GitHub profile input must be a JSON object"
        )

    if (
        payload.get("schema_version")
        != GITHUB_PROFILE_INPUT_SCHEMA_VERSION
    ):
        raise ValueError(
            "unsupported GitHub profile input schema_version"
        )

    observed_at = _parse_profile_datetime(
        payload.get("observed_at"),
        "observed_at",
    )
    observed_at_text = _profile_iso_z(observed_at)

    consent_version = payload.get("consent_version")
    if (
        not isinstance(consent_version, str)
        or not consent_version.strip()
    ):
        raise ValueError(
            "consent_version must be a non-empty string"
        )

    user = payload.get("user")
    if not isinstance(user, dict):
        raise ValueError("user must be an object")

    github_login = user.get("login")
    if (
        not isinstance(github_login, str)
        or not github_login.strip()
    ):
        raise ValueError(
            "user.login must be a non-empty string"
        )

    display_name = user.get("name")
    if (
        display_name is not None
        and not isinstance(display_name, str)
    ):
        raise ValueError(
            "user.name must be a string or null"
        )

    repositories = payload.get("repositories")
    if not isinstance(repositories, list):
        raise ValueError(
            "repositories must be an array"
        )

    public_rows: list[dict[str, Any]] = []
    active_rows: list[dict[str, Any]] = []
    recent_rows: list[dict[str, Any]] = []

    cutoff = observed_at - timedelta(
        days=GITHUB_PROFILE_RECENCY_DAYS
    )

    for index, repository in enumerate(repositories):
        if not isinstance(repository, dict):
            raise ValueError(
                f"repositories[{index}] must be an object"
            )

        # Security boundary:
        # anything not explicitly public is ignored.
        if repository.get("private") is not False:
            continue

        full_name = repository.get("full_name")
        if (
            not isinstance(full_name, str)
            or not full_name.strip()
        ):
            raise ValueError(
                f"repositories[{index}].full_name "
                "must be a non-empty string"
            )

        public_rows.append(repository)

        if repository.get("archived") is True:
            continue

        active_rows.append(repository)

        last_contribution = (
            _repository_last_contribution_at(repository)
        )

        if (
            last_contribution is not None
            and last_contribution >= cutoff
        ):
            recent_rows.append(repository)

    activity_summary = {
        field: 0
        for field in _PROFILE_ACTIVITY_FIELDS
    }

    first_activity: datetime | None = None
    last_activity: datetime | None = None

    for repository in active_rows:
        contributions = repository.get(
            "contributions",
            {},
        )

        if not isinstance(contributions, dict):
            raise ValueError(
                "repositories[].contributions "
                "must be an object"
            )

        for field in _PROFILE_ACTIVITY_FIELDS:
            activity_summary[field] += (
                _non_negative_int(
                    contributions.get(field, 0),
                    (
                        "repositories[].contributions."
                        f"{field}"
                    ),
                )
            )

        first_raw = repository.get(
            "first_contribution_at"
        )

        if first_raw is not None:
            first_value = _parse_profile_datetime(
                first_raw,
                (
                    "repositories[]."
                    "first_contribution_at"
                ),
            )

            first_activity = (
                first_value
                if first_activity is None
                else min(
                    first_activity,
                    first_value,
                )
            )

        last_raw = repository.get(
            "last_contribution_at"
        )

        if last_raw is not None:
            last_value = _parse_profile_datetime(
                last_raw,
                (
                    "repositories[]."
                    "last_contribution_at"
                ),
            )

            last_activity = (
                last_value
                if last_activity is None
                else max(
                    last_activity,
                    last_value,
                )
            )

    language_bytes: dict[str, int] = defaultdict(int)

    language_repositories: dict[
        str,
        set[str],
    ] = defaultdict(set)

    language_evidence: dict[
        str,
        list[dict[str, Any]],
    ] = defaultdict(list)

    for repository in recent_rows:
        full_name = str(
            repository["full_name"]
        ).strip()

        languages = repository.get(
            "languages",
            {},
        )

        if not isinstance(languages, dict):
            raise ValueError(
                "repositories[].languages "
                "must be an object"
            )

        for raw_language, raw_bytes in languages.items():
            if (
                not isinstance(raw_language, str)
                or not raw_language.strip()
            ):
                raise ValueError(
                    "repository language names "
                    "must be non-empty strings"
                )

            byte_count = _non_negative_int(
                raw_bytes,
                (
                    "repositories[].languages."
                    f"{raw_language}"
                ),
            )

            if byte_count == 0:
                continue

            display_language = (
                LANGUAGE_DISPLAY_NAMES.get(
                    raw_language.strip().casefold(),
                    raw_language.strip(),
                )
            )

            language_bytes[
                display_language
            ] += byte_count

            language_repositories[
                display_language
            ].add(full_name)

            language_evidence[
                display_language
            ].append(
                {
                    "source": (
                        "public_repository_"
                        "language_metadata"
                    ),
                    "repository": full_name,
                    "language": display_language,
                    "bytes": byte_count,
                }
            )

    total_language_bytes = sum(
        language_bytes.values()
    )

    language_distribution: list[
        dict[str, Any]
    ] = []

    for language, byte_count in sorted(
        language_bytes.items(),
        key=lambda item: (
            -item[1],
            item[0].casefold(),
        ),
    ):
        share = (
            byte_count / total_language_bytes
            if total_language_bytes
            else 0.0
        )

        language_distribution.append(
            {
                "language": language,
                "bytes": byte_count,
                "share": round(share, 4),
                "repository_count": len(
                    language_repositories[
                        language
                    ]
                ),
            }
        )

    supported_language_rows = [
        row
        for row in language_distribution
        if str(
            row["language"]
        ).casefold() in ALLOWED_LANGUAGES
    ][:3]

    language_skill_suggestions: list[
        dict[str, Any]
    ] = []

    for row in supported_language_rows:
        language = str(row["language"])

        confidence = (
            _language_profile_confidence(
                float(row["share"]),
                int(row["repository_count"]),
            )
        )

        language_skill_suggestions.append(
            {
                "skill_name": language,

                # Conservative default:
                # repository activity is not
                # developer proficiency.
                "suggested_level": 1,

                "source": (
                    GITHUB_PROFILE_SOURCE_WEAK
                ),
                "confidence": confidence,
                "observed_at": observed_at_text,
                "evidence": sorted(
                    language_evidence[language],
                    key=lambda item: (
                        item["repository"],
                        item["language"],
                    ),
                ),
            }
        )

    skill_evidence: dict[
        str,
        list[dict[str, Any]],
    ] = defaultdict(list)

    skill_repositories: dict[
        str,
        set[str],
    ] = defaultdict(set)

    for repository in recent_rows:
        full_name = str(
            repository["full_name"]
        ).strip()

        contributed_paths = repository.get(
            "contributed_paths",
            [],
        )

        if (
            not isinstance(contributed_paths, list)
            or any(
                not isinstance(path, str)
                for path in contributed_paths
            )
        ):
            raise ValueError(
                "repositories[].contributed_paths "
                "must be an array of strings"
            )

        for path in contributed_paths:
            for skill_name in (
                _path_skill_categories(path)
            ):
                skill_repositories[
                    skill_name
                ].add(full_name)

                skill_evidence[
                    skill_name
                ].append(
                    {
                        "source": (
                            "public_contributed_path"
                        ),
                        "repository": full_name,
                        "path": path,
                    }
                )

    category_skill_suggestions: list[
        dict[str, Any]
    ] = []

    for skill_name in _PROFILE_SKILL_NAMES:
        evidence = skill_evidence.get(
            skill_name,
            [],
        )

        if not evidence:
            continue

        deduplicated = {
            (
                item["repository"],
                item["path"],
            ): item
            for item in evidence
        }

        ordered_evidence = [
            deduplicated[key]
            for key in sorted(
                deduplicated,
                key=lambda item: (
                    item[0],
                    item[1],
                ),
            )
        ]

        category_skill_suggestions.append(
            {
                "skill_name": skill_name,
                "suggested_level": 1,
                "source": (
                    GITHUB_PROFILE_SOURCE_EXPLICIT
                ),
                "confidence": (
                    _skill_profile_confidence(
                        len(
                            skill_repositories[
                                skill_name
                            ]
                        ),
                        len(ordered_evidence),
                    )
                ),
                "observed_at": observed_at_text,
                "evidence": ordered_evidence,
            }
        )

    preferred_languages = [
        str(row["language"])
        for row in supported_language_rows
    ]

    if supported_language_rows:
        preferred_language_confidence = round(
            sum(
                _language_profile_confidence(
                    float(row["share"]),
                    int(
                        row["repository_count"]
                    ),
                )
                for row
                in supported_language_rows
            )
            / len(supported_language_rows),
            2,
        )
    else:
        preferred_language_confidence = 0.0

    recent_active_repositories = []

    for repository in sorted(
        recent_rows,
        key=lambda item: str(
            item["full_name"]
        ).casefold(),
    ):
        last_contribution = (
            _repository_last_contribution_at(
                repository
            )
        )

        recent_active_repositories.append(
            {
                "full_name": str(
                    repository["full_name"]
                ).strip(),
                "last_contribution_at": (
                    _profile_iso_z(
                        last_contribution
                    )
                    if last_contribution
                    is not None
                    else None
                ),
            }
        )

    return {
        "schema_version": (
            GITHUB_PROFILE_IMPORT_VERSION
        ),
        "github_login": github_login.strip(),
        "display_name": (
            display_name.strip()
            if (
                isinstance(
                    display_name,
                    str,
                )
                and display_name.strip()
            )
            else github_login.strip()
        ),
        "consent_version": (
            consent_version.strip()
        ),
        "observed_at": observed_at_text,

        "public_repository_count": len(
            public_rows
        ),
        "active_public_repository_count": len(
            active_rows
        ),
        "recent_active_repository_count": len(
            recent_rows
        ),

        "recent_active_repositories": (
            recent_active_repositories
        ),

        "language_distribution_basis": (
            "recent_public_contributed_repositories"
        ),
        "language_distribution": (
            language_distribution
        ),

        "activity_summary": activity_summary,

        "activity_range": {
            "first_contribution_at": (
                _profile_iso_z(first_activity)
                if first_activity is not None
                else None
            ),
            "last_contribution_at": (
                _profile_iso_z(last_activity)
                if last_activity is not None
                else None
            ),
        },

        "suggestions": {
            "preferred_languages": {
                "value": preferred_languages,
                "source": (
                    GITHUB_PROFILE_SOURCE_WEAK
                ),
                "confidence": (
                    preferred_language_confidence
                ),
                "observed_at": (
                    observed_at_text
                ),
                "evidence": [
                    {
                        "language": (
                            row["language"]
                        ),
                        "share": row["share"],
                        "repository_count": (
                            row[
                                "repository_count"
                            ]
                        ),
                    }
                    for row
                    in supported_language_rows
                ],
            },

            "skills": [
                *language_skill_suggestions,
                *category_skill_suggestions,
            ],
        },
    }

def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be an array of strings")
    return tuple(item.strip() for item in value if item.strip())


def _custom_string_list(
    value: Any,
    field: str,
    *,
    maximum: int,
    allowed: set[str] | None = None,
) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{field} must contain strings")
        item = item.strip()
        normalized = item.casefold()
        if not item or len(item) > 40:
            raise ValueError(f"{field} contains an invalid value")
        if allowed is not None and normalized not in allowed:
            raise ValueError(f"{field} contains an unsupported value: {item}")
        if normalized not in seen:
            cleaned.append(normalized if allowed is not None else item)
            seen.add(normalized)
    if not cleaned:
        raise ValueError(f"{field} must contain at least one value")
    if len(cleaned) > maximum:
        raise ValueError(f"{field} may contain at most {maximum} values")
    return cleaned


def custom_profile_for_matching(payload: Any) -> dict[str, Any]:
    """Validate a transient browser-supplied profile and normalize it for matching."""

    if not isinstance(payload, dict):
        raise ValueError("profile must be a JSON object")
    unknown = set(payload) - {
        "display_name",
        "service_track",
        "preferred_languages",
        "operating_systems",
        "preferred_task_types",
        "max_code_difficulty",
        "max_setup_difficulty",
        "desired_skill_stretch",
        "skills",
    }
    if unknown:
        raise ValueError(f"profile contains unsupported fields: {', '.join(sorted(unknown))}")

    display_name = payload.get("display_name", "自定义画像")
    if not isinstance(display_name, str) or not display_name.strip() or len(display_name.strip()) > 60:
        raise ValueError("display_name must contain between 1 and 60 characters")

    track = payload.get("service_track")
    if track not in ALLOWED_SERVICE_TRACKS:
        raise ValueError("service_track must be newcomer or growth")

    def bounded_int(field: str, upper: int) -> int:
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= upper:
            raise ValueError(f"{field} must be an integer between 0 and {upper}")
        return value

    languages = [
        LANGUAGE_DISPLAY_NAMES[value]
        for value in _custom_string_list(
            payload.get("preferred_languages"),
            "preferred_languages",
            maximum=6,
            allowed=ALLOWED_LANGUAGES,
        )
    ]
    operating_systems = _custom_string_list(
        payload.get("operating_systems"),
        "operating_systems",
        maximum=3,
        allowed=ALLOWED_OPERATING_SYSTEMS,
    )
    task_types = _custom_string_list(
        payload.get("preferred_task_types"),
        "preferred_task_types",
        maximum=6,
        allowed=ALLOWED_TASK_TYPES,
    )

    skills_raw = payload.get("skills")
    if not isinstance(skills_raw, dict) or not skills_raw or len(skills_raw) > 24:
        raise ValueError("skills must contain between 1 and 24 entries")
    skills: dict[str, int] = {}
    for raw_name, level in skills_raw.items():
        if not isinstance(raw_name, str):
            raise ValueError("skill names must be strings")
        name = raw_name.strip().casefold()
        if not name or len(name) > 50 or name.startswith("platform:"):
            raise ValueError("skills contains an invalid skill name")
        if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 4:
            raise ValueError(f"skill level for {raw_name!r} must be between 0 and 4")
        skills[name] = level

    return {
        "profile_key": "custom",
        "display_name": display_name.strip(),
        "service_track": track,
        "preferred_languages": languages,
        "operating_systems": operating_systems,
        "preferred_task_types": task_types,
        "max_code_difficulty": bounded_int("max_code_difficulty", 3),
        "max_setup_difficulty": bounded_int("max_setup_difficulty", 3),
        "desired_skill_stretch": bounded_int("desired_skill_stretch", 2),
        "profile_source": "user_input",
        "skills": skills,
    }


def load_profiles(path: Path) -> list[DeveloperProfile]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "developer-profiles-v0.1":
        raise ValueError("unsupported developer profile schema_version")
    rows = payload.get("profiles")
    if not isinstance(rows, list) or not rows:
        raise ValueError("profiles must be a non-empty array")
    profiles: list[DeveloperProfile] = []
    keys: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each profile must be an object")
        key = str(row.get("profile_key") or "").strip()
        if not key or key in keys:
            raise ValueError(f"invalid or duplicate profile_key: {key!r}")
        keys.add(key)
        track = row.get("service_track")
        if track not in {"newcomer", "growth", "hybrid"}:
            raise ValueError(f"invalid service_track for {key}: {track!r}")
        source = row.get("profile_source", "import")
        if source not in {"demo", "user_input", "import"}:
            raise ValueError(f"invalid profile_source for {key}: {source!r}")
        skills_raw = row.get("skills")
        if not isinstance(skills_raw, dict) or not skills_raw:
            raise ValueError(f"skills must be a non-empty object for {key}")
        skills: dict[str, int] = {}
        for name, level in skills_raw.items():
            if not isinstance(name, str) or not isinstance(level, int) or not 0 <= level <= 4:
                raise ValueError(f"invalid skill entry for {key}: {name!r}={level!r}")
            skills[name.strip()] = level

        def bounded_int(field: str, upper: int) -> int:
            value = row.get(field)
            if not isinstance(value, int) or not 0 <= value <= upper:
                raise ValueError(f"{field} for {key} must be between 0 and {upper}")
            return value

        profiles.append(
            DeveloperProfile(
                profile_key=key,
                display_name=str(row.get("display_name") or key),
                service_track=track,
                preferred_languages=_string_tuple(
                    row.get("preferred_languages", []), "preferred_languages"
                ),
                operating_systems=tuple(
                    value.casefold()
                    for value in _string_tuple(
                        row.get("operating_systems", []), "operating_systems"
                    )
                ),
                preferred_task_types=_string_tuple(
                    row.get("preferred_task_types", []), "preferred_task_types"
                ),
                max_code_difficulty=bounded_int("max_code_difficulty", 3),
                max_setup_difficulty=bounded_int("max_setup_difficulty", 3),
                desired_skill_stretch=bounded_int("desired_skill_stretch", 2),
                profile_source=source,
                consent_version=row.get("consent_version"),
                skills=skills,
            )
        )
    return profiles
