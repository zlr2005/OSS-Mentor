"""Validation for consent-aware local developer profile imports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CUSTOM_PROFILE_VERSION = "custom-profile-v0.2"
ALLOWED_SERVICE_TRACKS = {"newcomer", "growth"}
ALLOWED_OPERATING_SYSTEMS = {"windows", "macos", "linux"}
ALLOWED_TASK_TYPES = {
    "bug_fix",
    "testing",
    "documentation",
    "feature",
    "refactor",
    "build_tooling",
}


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

    languages = _custom_string_list(
        payload.get("preferred_languages"), "preferred_languages", maximum=8
    )
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
