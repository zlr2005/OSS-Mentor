"""Export read-only baseline diagnostics for OSS-Mentor skill requirements.

This script is deliberately diagnostic-only.  It opens the SQLite snapshot with
URI ``mode=ro`` and ``PRAGMA query_only = ON``; it never runs migrations,
feature extraction, matching/ranking, or network requests.  Candidate technology
signals are review inputs only and MUST NOT be interpreted as production skill
requirements or ground truth.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from oss_mentor.developer_profiles import ALLOWED_TASK_TYPES, load_profiles


DIAGNOSTIC_SCHEMA_VERSION = "skill_requirement_diagnostics_v0.1"
REVIEW_SCHEMA_VERSION = "skill_requirement_review_candidates_v0.1"
DEFAULT_DATABASE = Path("data/oss_mentor_task_features_v0.2.1.sqlite3")
DEFAULT_PROFILES_FILE = Path("config/demo_profiles_v0.1.json")
DEFAULT_OUTPUT = Path("data/reports/skill_requirement_diagnostics_v0.1.json")
DEFAULT_REVIEW_OUTPUT = Path(
    "data/annotations/skill_requirement_review_candidates_v0.1.json"
)
DEFAULT_MAX_REVIEW_CANDIDATES = 30
DEFAULT_MAX_BODY_CHARS = 700

PUBLIC_TASK_TYPES = frozenset(
    str(value).strip().casefold() for value in ALLOWED_TASK_TYPES if str(value).strip()
)
VALID_PLATFORM_SKILLS = frozenset(
    {"platform:windows", "platform:linux", "platform:macos"}
)
PLAIN_PLATFORM_SKILLS = frozenset({"windows", "linux", "macos"})

REQUIRED_TABLE_COLUMNS: dict[str, frozenset[str]] = {
    "repository": frozenset(
        {
            "repository_id",
            "full_name",
            "primary_language",
            "is_archived",
            "is_disabled",
        }
    ),
    "task_candidate": frozenset(
        {
            "task_candidate_id",
            "repository_id",
            "issue_number",
            "html_url",
            "title",
            "body_text",
            "labels_json",
            "task_types_json",
            "candidate_eligibility",
            "newcomer_label_signal",
            "estimated_code_difficulty",
            "estimated_setup_difficulty",
            "estimated_project_context_difficulty",
            "estimated_collaboration_difficulty",
            "estimated_effort_bucket",
            "task_feature_version",
        }
    ),
    "task_skill_requirement": frozenset(
        {
            "task_candidate_id",
            "skill_name",
            "minimum_level",
            "importance",
            "requirement_source",
            "feature_version",
        }
    ),
    "developer_profile": frozenset(
        {
            "developer_profile_id",
            "profile_key",
            "service_track",
            "preferred_languages_json",
            "preferred_task_types_json",
            "max_code_difficulty",
            "max_setup_difficulty",
            "operating_systems_json",
        }
    ),
    "developer_skill": frozenset(
        {
            "developer_profile_id",
            "skill_name",
            "skill_level",
        }
    ),
}

PLATFORM_TEXT_PATTERNS: dict[str, re.Pattern[str]] = {
    "macos": re.compile(r"\bmac\s?os\b|\bos\s?x\b|\bmacosx\b", re.IGNORECASE),
    "windows": re.compile(r"\bwindows\b|\bwin32\b", re.IGNORECASE),
    "linux": re.compile(r"\blinux\b", re.IGNORECASE),
}

_BODY_REQUIREMENT_ACTION = re.compile(
    r"\b(?:requires?|required|need(?:s|ed)?|must|should|use|using|adopt|migrate|"
    r"move|replace|configure|install|build|compile|test|run|deploy|integrate|"
    r"support|add|update|upgrade|fix|debug|implement|generate)\b",
    re.IGNORECASE,
)
_BODY_CONTEXT_ONLY = re.compile(
    r"\b(?:environment|version|logs?|output|stack\s+trace|traceback|reported|"
    r"tested\s+on|works?\s+on|running\s+on|different\s+from|compared\s+(?:to|with)|"
    r"example|diagnostic|system\s+information)\b",
    re.IGNORECASE,
)

_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]{1,200})`")
_URL = re.compile(r"https?://\S+")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class CandidateSignalRule:
    name: str
    category: str
    aliases: tuple[str, ...]
    pattern: re.Pattern[str]
    ambiguous: bool = False


def _rx(pattern: str, *, ignore_case: bool = True) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE if ignore_case else 0)


# Diagnostics-only lexicon.  These signals are deliberately small, high-value,
# and comparatively low-ambiguity.  They do NOT create SkillRequirement rows.
CANDIDATE_SIGNAL_RULES: tuple[CandidateSignalRule, ...] = (
    CandidateSignalRule(
        "Docker",
        "container",
        ("docker", "dockerfile", "docker compose"),
        _rx(r"\bdocker(?:file)?\b|\bdocker\s+compose\b"),
    ),
    CandidateSignalRule(
        "Kubernetes",
        "orchestration",
        ("kubernetes", "k8s", "kubelet"),
        _rx(r"\bkubernetes\b|\bk8s\b|\bkubelet\b"),
    ),
    CandidateSignalRule(
        "Maven", "build_tool", ("maven", "mvn"), _rx(r"\bmaven\b|\bmvn\b")
    ),
    CandidateSignalRule(
        "Gradle",
        "build_tool",
        ("gradle", "gradlew"),
        _rx(r"\bgradle\b|\bgradlew\b"),
    ),
    CandidateSignalRule(
        "CMake", "build_tool", ("cmake",), _rx(r"\bcmake\b")
    ),
    CandidateSignalRule(
        "Cargo",
        "build_tool",
        ("cargo", "Cargo.toml"),
        _rx(r"\bcargo\.toml\b|\bcargo\s+(?:build|test|check|run|clippy|fmt)\b"),
        ambiguous=True,
    ),
    CandidateSignalRule(
        "npm",
        "package_manager",
        ("npm", "package-lock.json"),
        _rx(r"\bnpm\b|\bpackage-lock\.json\b"),
    ),
    CandidateSignalRule(
        "pnpm",
        "package_manager",
        ("pnpm", "pnpm-lock.yaml"),
        _rx(r"\bpnpm\b|\bpnpm-lock\.ya?ml\b"),
    ),
    CandidateSignalRule(
        "Yarn",
        "package_manager",
        ("yarn", "yarn.lock"),
        _rx(r"\byarn\.lock\b|\byarn\s+(?:add|install|test|build|run|upgrade)\b"),
        ambiguous=True,
    ),
    CandidateSignalRule(
        "GitHub Actions",
        "ci_tool",
        ("github actions", "github action"),
        _rx(r"\bgithub\s+actions?\b"),
    ),
    CandidateSignalRule(
        "CI",
        "ci_concept",
        ("CI", "continuous integration"),
        re.compile(
            r"(?<![A-Za-z0-9])CI(?![A-Za-z0-9])|"
            r"(?i:\bcontinuous\s+integration\b|\bci\s+(?:workflow|pipeline|job|build|check|tests?)\b)"
        ),
        ambiguous=True,
    ),
    CandidateSignalRule(
        "pytest", "test_framework", ("pytest",), _rx(r"\bpytest\b")
    ),
    CandidateSignalRule(
        "Jest",
        "test_framework",
        ("jest",),
        _rx(r"\bjest\b(?=\s+(?:test|tests|suite|config|configuration|runner)|\b)"),
        ambiguous=True,
    ),
    CandidateSignalRule(
        "CUDA", "gpu_toolchain", ("cuda",), _rx(r"\bcuda\b")
    ),
    CandidateSignalRule(
        "ROCm", "gpu_toolchain", ("rocm",), _rx(r"\brocm\b")
    ),
    CandidateSignalRule(
        "SQL", "data_query", ("sql",), _rx(r"\bsql\b")
    ),
    CandidateSignalRule(
        "GraphQL", "api_protocol", ("graphql",), _rx(r"\bgraphql\b")
    ),
    CandidateSignalRule(
        "gRPC", "api_protocol", ("grpc",), _rx(r"\bgrpc\b")
    ),
    CandidateSignalRule(
        "REST API",
        "api_protocol",
        ("REST API", "RESTful"),
        _rx(r"\brest(?:ful)?\s+api\b|\brestful\b"),
        ambiguous=True,
    ),
)

SIGNAL_BY_NAME = {rule.name: rule for rule in CANDIDATE_SIGNAL_RULES}


class SkillDiagnosticsError(RuntimeError):
    """Raised when the diagnostic input cannot be interpreted safely."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_sha256(path: str | Path) -> str:
    resolved = Path(path).expanduser().resolve()
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def readonly_uri(database_path: str | Path) -> str:
    resolved = Path(database_path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {resolved}")
    return f"{resolved.as_uri()}?mode=ro"


def connect_readonly(database_path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(readonly_uri(database_path), uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table_name})")}


def validate_schema(connection: sqlite3.Connection) -> dict[str, list[str]]:
    existing_tables = {
        str(row["name"])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    }
    missing_tables = sorted(set(REQUIRED_TABLE_COLUMNS) - existing_tables)
    if missing_tables:
        raise SkillDiagnosticsError(
            "database is missing required tables: " + ", ".join(missing_tables)
        )

    missing_columns: dict[str, list[str]] = {}
    for table_name, required in REQUIRED_TABLE_COLUMNS.items():
        missing = sorted(required - table_columns(connection, table_name))
        if missing:
            missing_columns[table_name] = missing
    if missing_columns:
        detail = "; ".join(
            f"{table}: {', '.join(columns)}"
            for table, columns in sorted(missing_columns.items())
        )
        raise SkillDiagnosticsError(f"database schema is missing required columns: {detail}")

    return {
        table: sorted(table_columns(connection, table))
        for table in sorted(REQUIRED_TABLE_COLUMNS)
    }


def _safe_json(value: Any, *, expected: type | tuple[type, ...], field: str) -> Any:
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise SkillDiagnosticsError(f"invalid JSON in {field}: {value!r}") from exc
    if not isinstance(parsed, expected):
        raise SkillDiagnosticsError(
            f"{field} must decode to {expected!r}, got {type(parsed).__name__}"
        )
    return parsed


def parse_string_list(value: Any, *, field: str) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    parsed = _safe_json(value, expected=(list, tuple), field=field)
    if any(not isinstance(item, str) for item in parsed):
        raise SkillDiagnosticsError(f"{field} must contain only strings")
    return tuple(item.strip() for item in parsed if item.strip())


def parse_string_list_with_status(value: Any) -> tuple[tuple[str, ...], str]:
    """Parse candidate JSON list without hiding data anomalies.

    Unlike schema mismatches, malformed candidate JSON is a data-quality finding,
    so diagnostics records it instead of aborting the entire export.
    """

    if value is None or (isinstance(value, str) and not value.strip()):
        return (), "missing"
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return (), "invalid_json"
    if not isinstance(parsed, (list, tuple)):
        return (), "invalid_type"
    if any(not isinstance(item, str) for item in parsed):
        return (), "invalid_item_type"
    cleaned = tuple(item.strip() for item in parsed if item.strip())
    return cleaned, "ok" if cleaned else "empty"


def normalize_skill_name(value: Any) -> str:
    return str(value or "").strip().casefold()


def _is_active(record: Mapping[str, Any]) -> bool:
    return not bool(record.get("is_archived")) and not bool(record.get("is_disabled"))


def _is_eligible(record: Mapping[str, Any]) -> bool:
    return _is_active(record) and record.get("candidate_eligibility") == "eligible"


def _task_sort_key(record: Mapping[str, Any]) -> tuple[str, int, int]:
    return (
        str(record.get("repository") or "").casefold(),
        int(record.get("issue_number") or 0),
        int(record.get("task_candidate_id") or 0),
    )


def _profile_sort_key(profile: Mapping[str, Any]) -> str:
    return str(profile.get("profile_key") or "").casefold()


def load_database_snapshot(database_path: str | Path) -> dict[str, Any]:
    resolved = Path(database_path).expanduser().resolve()
    before_hash = file_sha256(resolved)
    connection = connect_readonly(resolved)
    try:
        schema = validate_schema(connection)
        candidate_rows = connection.execute(
            """
            SELECT
                tc.task_candidate_id,
                tc.repository_id,
                r.full_name AS repository,
                COALESCE(r.primary_language, '') AS primary_language,
                COALESCE(r.is_archived, 0) AS is_archived,
                COALESCE(r.is_disabled, 0) AS is_disabled,
                tc.issue_number,
                COALESCE(tc.html_url, '') AS html_url,
                COALESCE(tc.title, '') AS title,
                COALESCE(tc.body_text, '') AS body_text,
                tc.labels_json,
                tc.task_types_json,
                COALESCE(tc.candidate_eligibility, '') AS candidate_eligibility,
                COALESCE(tc.newcomer_label_signal, 0) AS newcomer_label_signal,
                tc.estimated_code_difficulty,
                tc.estimated_setup_difficulty,
                tc.estimated_project_context_difficulty,
                tc.estimated_collaboration_difficulty,
                tc.estimated_effort_bucket,
                COALESCE(tc.task_feature_version, '') AS task_feature_version
            FROM task_candidate AS tc
            JOIN repository AS r USING(repository_id)
            ORDER BY tc.task_candidate_id
            """
        ).fetchall()
        requirement_rows = connection.execute(
            """
            SELECT
                task_candidate_id,
                skill_name,
                minimum_level,
                importance,
                requirement_source,
                feature_version
            FROM task_skill_requirement
            ORDER BY task_candidate_id, LOWER(skill_name), skill_name
            """
        ).fetchall()
        orphan_rows = connection.execute(
            """
            SELECT tsr.task_candidate_id, tsr.skill_name
            FROM task_skill_requirement AS tsr
            LEFT JOIN task_candidate AS tc
              ON tc.task_candidate_id = tsr.task_candidate_id
            WHERE tc.task_candidate_id IS NULL
            ORDER BY tsr.task_candidate_id, LOWER(tsr.skill_name)
            """
        ).fetchall()
        db_profile_rows = connection.execute(
            """
            SELECT
                dp.developer_profile_id,
                dp.profile_key,
                dp.service_track,
                dp.preferred_languages_json,
                dp.preferred_task_types_json,
                dp.max_code_difficulty,
                dp.max_setup_difficulty,
                dp.operating_systems_json,
                ds.skill_name,
                ds.skill_level
            FROM developer_profile AS dp
            LEFT JOIN developer_skill AS ds
              ON ds.developer_profile_id = dp.developer_profile_id
            ORDER BY dp.profile_key, LOWER(ds.skill_name), ds.skill_name
            """
        ).fetchall()
    finally:
        connection.close()
    after_hash = file_sha256(resolved)
    if before_hash != after_hash:
        raise SkillDiagnosticsError(
            "SQLite database changed while diagnostics were reading it; aborting for reproducibility"
        )

    candidates: list[dict[str, Any]] = []
    for row in candidate_rows:
        record = dict(row)
        record["is_archived"] = bool(record["is_archived"])
        record["is_disabled"] = bool(record["is_disabled"])
        record["newcomer_label_signal"] = bool(record["newcomer_label_signal"])
        labels, labels_status = parse_string_list_with_status(record["labels_json"])
        task_types, task_types_status = parse_string_list_with_status(record["task_types_json"])
        record["labels"] = list(labels)
        record["labels_status"] = labels_status
        record["task_types"] = list(task_types)
        record["task_types_status"] = task_types_status
        candidates.append(record)

    requirements = [dict(row) for row in requirement_rows]

    db_profiles_by_key: dict[str, dict[str, Any]] = {}
    for row in db_profile_rows:
        key = str(row["profile_key"])
        profile = db_profiles_by_key.setdefault(
            key,
            {
                "profile_key": key,
                "service_track": str(row["service_track"]),
                "preferred_languages": list(
                    parse_string_list(
                        row["preferred_languages_json"], field="preferred_languages_json"
                    )
                ),
                "preferred_task_types": list(
                    parse_string_list(
                        row["preferred_task_types_json"], field="preferred_task_types_json"
                    )
                ),
                "max_code_difficulty": int(row["max_code_difficulty"]),
                "max_setup_difficulty": int(row["max_setup_difficulty"]),
                "operating_systems": list(
                    parse_string_list(
                        row["operating_systems_json"], field="operating_systems_json"
                    )
                ),
                "skills": {},
                "source": "database",
            },
        )
        if row["skill_name"] is not None:
            profile["skills"][normalize_skill_name(row["skill_name"])] = int(
                row["skill_level"]
            )

    return {
        "database_path": str(resolved),
        "database_sha256_before": before_hash,
        "database_sha256_after": after_hash,
        "database_unchanged": before_hash == after_hash,
        "sqlite_access_mode": "uri_mode_ro_and_query_only",
        "schema": schema,
        "candidates": candidates,
        "requirements": requirements,
        "orphans": [dict(row) for row in orphan_rows],
        "database_profiles": sorted(db_profiles_by_key.values(), key=_profile_sort_key),
    }


def load_configured_profiles(profiles_file: str | Path) -> dict[str, Any]:
    resolved = Path(profiles_file).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"developer profile file does not exist: {resolved}")
    profiles = load_profiles(resolved)
    rows = [
        {
            "profile_key": profile.profile_key,
            "service_track": profile.service_track,
            "preferred_languages": list(profile.preferred_languages),
            "preferred_task_types": list(profile.preferred_task_types),
            "max_code_difficulty": int(profile.max_code_difficulty),
            "max_setup_difficulty": int(profile.max_setup_difficulty),
            "operating_systems": [value.casefold() for value in profile.operating_systems],
            "skills": {
                normalize_skill_name(name): int(level)
                for name, level in profile.skills.items()
            },
            "source": "configured_profile_file",
        }
        for profile in profiles
    ]
    return {
        "path": str(resolved),
        "sha256": file_sha256(resolved),
        "profiles": sorted(rows, key=_profile_sort_key),
    }


def merge_reference_profiles(
    configured_profiles: Sequence[Mapping[str, Any]],
    database_profiles: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Merge by profile_key; database state wins when the same key is imported."""

    merged: dict[str, dict[str, Any]] = {}
    for profile in configured_profiles:
        merged[str(profile["profile_key"])] = {
            **profile,
            "skills": dict(profile.get("skills") or {}),
            "preferred_languages": list(profile.get("preferred_languages") or []),
            "preferred_task_types": list(profile.get("preferred_task_types") or []),
            "operating_systems": list(profile.get("operating_systems") or []),
        }
    for profile in database_profiles:
        merged[str(profile["profile_key"])] = {
            **profile,
            "skills": dict(profile.get("skills") or {}),
            "preferred_languages": list(profile.get("preferred_languages") or []),
            "preferred_task_types": list(profile.get("preferred_task_types") or []),
            "operating_systems": list(profile.get("operating_systems") or []),
        }
    return sorted(merged.values(), key=_profile_sort_key)


def requirement_validity(requirement: Mapping[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    skill_name = requirement.get("skill_name")
    level = requirement.get("minimum_level")
    importance = requirement.get("importance")
    source = requirement.get("requirement_source")
    version = requirement.get("feature_version")

    if not isinstance(skill_name, str) or not skill_name.strip():
        reasons.append("blank_skill_name")
    if isinstance(level, bool) or not isinstance(level, int):
        reasons.append("minimum_level_not_integer")
    elif not 0 <= level <= 4:
        reasons.append("minimum_level_outside_schema_range_0_4")
    if isinstance(importance, bool) or not isinstance(importance, (int, float)):
        reasons.append("importance_not_numeric")
    elif not 0 < float(importance) <= 1:
        reasons.append("importance_outside_range_0_1")
    if not isinstance(source, str) or not source.strip():
        reasons.append("blank_requirement_source")
    if not isinstance(version, str) or not version.strip():
        reasons.append("blank_feature_version")

    generator_outlier = (
        isinstance(level, int) and not isinstance(level, bool) and not 1 <= level <= 3
    )
    return {
        "schema_valid": not reasons,
        "schema_invalid_reasons": reasons,
        "current_generator_contract_outlier": generator_outlier,
    }


def classify_requirement(
    requirement: Mapping[str, Any], language_vocabulary: set[str]
) -> str:
    normalized = normalize_skill_name(requirement.get("skill_name"))
    if normalized.startswith("platform:"):
        return "platform"
    if normalized in PUBLIC_TASK_TYPES:
        return "task_type"
    if normalized in language_vocabulary:
        return "programming_language"
    return "other"


def _counter_dict(counter: Counter[Any]) -> dict[str, int]:
    return {
        str(key): int(value)
        for key, value in sorted(counter.items(), key=lambda item: str(item[0]))
    }


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def _nearest_rank(values: Sequence[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return int(ordered[rank - 1])


def _count_bucket(value: int) -> str:
    return str(value) if value <= 4 else "5+"


def _top_counter(counter: Counter[str], limit: int = 20) -> list[dict[str, Any]]:
    return [
        {"name": name, "count": int(count)}
        for name, count in sorted(
            counter.items(), key=lambda item: (-item[1], item[0].casefold(), item[0])
        )[:limit]
    ]


def _importance_key(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{float(value):.4g}"
    return repr(value)


def _requirement_index(
    requirements: Sequence[Mapping[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    output: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for requirement in requirements:
        output[int(requirement["task_candidate_id"])].append(dict(requirement))
    for task_id in output:
        output[task_id].sort(
            key=lambda item: (
                normalize_skill_name(item.get("skill_name")),
                str(item.get("skill_name") or ""),
            )
        )
    return dict(output)


def _valid_requirements(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [dict(row) for row in rows if requirement_validity(row)["schema_valid"]]


def _skill_stats_for_tasks(
    task_ids: set[int],
    requirements_by_task: Mapping[int, Sequence[Mapping[str, Any]]],
    language_vocabulary: set[str],
) -> dict[str, Any]:
    counts: list[int] = []
    skill_task_counter: Counter[str] = Counter()
    category_task_sets: dict[str, set[int]] = defaultdict(set)
    for task_id in sorted(task_ids):
        valid = _valid_requirements(requirements_by_task.get(task_id, []))
        counts.append(len(valid))
        seen_skills: set[str] = set()
        for requirement in valid:
            normalized = normalize_skill_name(requirement.get("skill_name"))
            if normalized and normalized not in seen_skills:
                skill_task_counter[str(requirement["skill_name"])] += 1
                seen_skills.add(normalized)
            category_task_sets[
                classify_requirement(requirement, language_vocabulary)
            ].add(task_id)
    return {
        "task_count": len(task_ids),
        "mean_skill_count": round(statistics.fmean(counts), 4) if counts else None,
        "median_skill_count": statistics.median(counts) if counts else None,
        "top_skill_names": _top_counter(skill_task_counter, limit=12),
        "programming_language_skill_task_count": len(
            category_task_sets.get("programming_language", set())
        ),
        "task_type_skill_task_count": len(category_task_sets.get("task_type", set())),
        "platform_skill_task_count": len(category_task_sets.get("platform", set())),
        "other_skill_task_count": len(category_task_sets.get("other", set())),
    }


def _find_matches(text: str, pattern: re.Pattern[str]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for match in pattern.finditer(text):
        value = _WHITESPACE.sub(" ", match.group(0)).strip()
        if value:
            matches.append({"matched_value": value[:160], "start": match.start(), "end": match.end()})
    return matches


def _body_signal_strength(body: str, matches: Sequence[Mapping[str, Any]]) -> str:
    for match in matches:
        start = max(0, int(match["start"]) - 120)
        end = min(len(body), int(match["end"]) + 120)
        context = body[start:end]
        action = bool(_BODY_REQUIREMENT_ACTION.search(context))
        context_only = bool(_BODY_CONTEXT_ONLY.search(context))
        if action and not context_only:
            return "body_explicit"
    return "body_only_contextual"


def detect_candidate_signals(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    title = str(record.get("title") or "")
    labels = [str(value) for value in record.get("labels") or []]
    body = str(record.get("body_text") or "")
    output: list[dict[str, Any]] = []
    for rule in CANDIDATE_SIGNAL_RULES:
        title_matches = _find_matches(title, rule.pattern)
        label_matches: list[dict[str, Any]] = []
        for label in labels:
            label_matches.extend(_find_matches(label, rule.pattern))
        body_matches = _find_matches(body, rule.pattern)
        if not (title_matches or label_matches or body_matches):
            continue
        sources: list[str] = []
        if title_matches:
            sources.append("title")
        if label_matches:
            sources.append("label")
        if body_matches:
            sources.append("body")
        if title_matches or label_matches:
            strength = "title_or_label_explicit"
        else:
            strength = _body_signal_strength(body, body_matches)
        output.append(
            {
                "signal_name": rule.name,
                "candidate_category": rule.category,
                "aliases": list(rule.aliases),
                "sources": sources,
                "strength": strength,
                "ambiguous": rule.ambiguous,
                "matched_values": {
                    "title": [item["matched_value"] for item in title_matches[:3]],
                    "label": [item["matched_value"] for item in label_matches[:3]],
                    "body": [item["matched_value"] for item in body_matches[:3]],
                },
            }
        )
    return sorted(output, key=lambda item: item["signal_name"].casefold())


def detect_body_only_platform_signals(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    title_and_labels = "\n".join(
        [str(record.get("title") or ""), *[str(value) for value in record.get("labels") or []]]
    )
    body = str(record.get("body_text") or "")
    output: list[dict[str, Any]] = []
    for platform, pattern in PLATFORM_TEXT_PATTERNS.items():
        if pattern.search(title_and_labels):
            continue
        body_matches = _find_matches(body, pattern)
        if body_matches:
            output.append(
                {
                    "platform": platform,
                    "matched_values": [item["matched_value"] for item in body_matches[:5]],
                }
            )
    return output


def _bounded_body_excerpt(body: Any, maximum: int) -> str:
    text = str(body or "")
    text = _CODE_FENCE.sub(" [code block omitted] ", text)
    text = _INLINE_CODE.sub(lambda match: match.group(1), text)
    text = _URL.sub("[url omitted]", text)
    text = _WHITESPACE.sub(" ", text).strip()
    if len(text) <= maximum:
        return text
    return text[: max(0, maximum - 1)].rstrip() + "…"


def _profile_support_level(profile: Mapping[str, Any], skill_name: str) -> int:
    normalized = normalize_skill_name(skill_name)
    if normalized.startswith("platform:"):
        platform = normalized.split(":", 1)[1]
        return 1 if platform in {str(x).casefold() for x in profile.get("operating_systems") or []} else 0
    return int((profile.get("skills") or {}).get(normalized, 0))


def _profile_has_skill_name(profile: Mapping[str, Any], skill_name: str) -> bool:
    normalized = normalize_skill_name(skill_name)
    if normalized.startswith("platform:"):
        platform = normalized.split(":", 1)[1]
        return platform in {str(x).casefold() for x in profile.get("operating_systems") or []}
    return normalized in (profile.get("skills") or {})


def _aggregate_candidate_signals(
    eligible_records: Sequence[Mapping[str, Any]],
    signals_by_task: Mapping[int, Sequence[Mapping[str, Any]]],
    reference_profiles: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    accumulator: dict[str, dict[str, Any]] = {}
    records_by_id = {int(record["task_candidate_id"]): record for record in eligible_records}
    for task_id in sorted(signals_by_task):
        record = records_by_id[task_id]
        for signal in signals_by_task[task_id]:
            name = str(signal["signal_name"])
            item = accumulator.setdefault(
                name,
                {
                    "signal_name": name,
                    "candidate_category": signal["candidate_category"],
                    "aliases": signal["aliases"],
                    "ambiguous": bool(signal["ambiguous"]),
                    "task_ids": set(),
                    "title_ids": set(),
                    "label_ids": set(),
                    "body_ids": set(),
                    "body_only_ids": set(),
                    "strength": Counter(),
                    "repositories": Counter(),
                    "languages": Counter(),
                    "task_types": Counter(),
                    "examples": [],
                },
            )
            item["task_ids"].add(task_id)
            sources = set(signal["sources"])
            if "title" in sources:
                item["title_ids"].add(task_id)
            if "label" in sources:
                item["label_ids"].add(task_id)
            if "body" in sources:
                item["body_ids"].add(task_id)
            if sources == {"body"}:
                item["body_only_ids"].add(task_id)
            item["strength"][signal["strength"]] += 1
            item["repositories"][str(record["repository"])] += 1
            if str(record.get("primary_language") or "").strip():
                item["languages"][str(record["primary_language"])] += 1
            for task_type in record.get("task_types") or []:
                item["task_types"][str(task_type)] += 1
            if len(item["examples"]) < 5:
                item["examples"].append(
                    {
                        "task_candidate_id": task_id,
                        "repository": record["repository"],
                        "issue_number": int(record["issue_number"]),
                        "title": record["title"],
                        "sources": list(signal["sources"]),
                        "strength": signal["strength"],
                    }
                )

    output: list[dict[str, Any]] = []
    for name, item in accumulator.items():
        profile_count = sum(
            1 for profile in reference_profiles if _profile_has_skill_name(profile, name)
        )
        output.append(
            {
                "signal_name": name,
                "candidate_category": item["candidate_category"],
                "aliases": item["aliases"],
                "ambiguous": item["ambiguous"],
                "task_count": len(item["task_ids"]),
                "title_count": len(item["title_ids"]),
                "label_count": len(item["label_ids"]),
                "body_count": len(item["body_ids"]),
                "body_only_count": len(item["body_only_ids"]),
                "strength_distribution": _counter_dict(item["strength"]),
                "repositories": _top_counter(item["repositories"], limit=10),
                "primary_languages": _top_counter(item["languages"], limit=10),
                "task_types": _top_counter(item["task_types"], limit=10),
                "current_profile_support": profile_count > 0,
                "profile_count_with_skill": profile_count,
                "potential_unknown_skill_risk": profile_count == 0,
                "examples": sorted(item["examples"], key=_task_sort_key),
            }
        )
    return sorted(
        output, key=lambda item: (-item["task_count"], item["signal_name"].casefold())
    )


def _review_sample_groups(
    record: Mapping[str, Any],
    valid_requirements: Sequence[Mapping[str, Any]],
    language_vocabulary: set[str],
    signals: Sequence[Mapping[str, Any]],
    body_only_platforms: Sequence[Mapping[str, Any]],
    high_frequency_signals: set[str],
    reference_profiles: Sequence[Mapping[str, Any]],
) -> tuple[set[str], int]:
    groups: set[str] = set()
    categories = {
        classify_requirement(requirement, language_vocabulary)
        for requirement in valid_requirements
    }
    normalized_existing = {
        normalize_skill_name(requirement.get("skill_name")) for requirement in valid_requirements
    }
    signal_names = {str(signal["signal_name"]) for signal in signals}
    signal_categories = {str(signal["candidate_category"]) for signal in signals}

    if body_only_platforms:
        groups.add("body_only_platform")
    if any(set(signal["sources"]) & {"title", "label"} for signal in signals):
        groups.add("tool_signal_title_or_label")
    if any(set(signal["sources"]) == {"body"} for signal in signals):
        groups.add("tool_signal_body_only")
    if (
        "build_tooling" in {str(value).casefold() for value in record.get("task_types") or []}
        and signals
        and not any(normalize_skill_name(name) in normalized_existing for name in signal_names)
    ):
        groups.add("build_tooling_without_specific_tool_skill")
    if (
        "testing" in {str(value).casefold() for value in record.get("task_types") or []}
        and "test_framework" in signal_categories
        and not any(normalize_skill_name(name) in normalized_existing for name in signal_names)
    ):
        groups.add("testing_without_specific_test_tool")
    if (
        "documentation"
        in {str(value).casefold() for value in record.get("task_types") or []}
        and categories <= {"programming_language", "task_type", "platform"}
    ):
        groups.add("documentation_language_dominated")
    if signal_names & high_frequency_signals:
        groups.add("high_frequency_candidate_tool")
    if any(
        not any(_profile_has_skill_name(profile, str(signal["signal_name"])) for profile in reference_profiles)
        for signal in signals
    ):
        groups.add("profile_unknown_candidate_skill")
    if any(bool(signal["ambiguous"]) or signal["strength"] == "body_only_contextual" for signal in signals):
        groups.add("potentially_ambiguous_signal")
    if categories == {"programming_language"}:
        groups.add("only_primary_language")
    if len(signals) >= 2:
        groups.add("multiple_candidate_signals")
    if not str(record.get("body_text") or "").strip():
        groups.add("missing_body")
    if isinstance(record.get("estimated_code_difficulty"), int) and int(
        record["estimated_code_difficulty"]
    ) >= 3:
        groups.add("high_code_difficulty")
    groups.add("newcomer_eligible" if record.get("newcomer_label_signal") else "non_newcomer_eligible")

    risk = 0
    risk += 4 * sum(
        1 for signal in signals if set(signal["sources"]) & {"title", "label"}
    )
    risk += 2 * sum(1 for signal in signals if signal["strength"] == "body_explicit")
    risk += sum(1 for signal in signals if signal["strength"] == "body_only_contextual")
    risk += 3 if "profile_unknown_candidate_skill" in groups else 0
    risk += 2 if body_only_platforms else 0
    risk += 2 if len(signals) >= 2 else 0
    risk += 1 if "high_code_difficulty" in groups else 0
    risk += 1 if "missing_body" in groups else 0
    return groups, risk


REVIEW_GROUP_PRIORITY: tuple[str, ...] = (
    "body_only_platform",
    "tool_signal_title_or_label",
    "tool_signal_body_only",
    "build_tooling_without_specific_tool_skill",
    "testing_without_specific_test_tool",
    "documentation_language_dominated",
    "high_frequency_candidate_tool",
    "profile_unknown_candidate_skill",
    "potentially_ambiguous_signal",
    "only_primary_language",
    "multiple_candidate_signals",
    "missing_body",
    "high_code_difficulty",
    "newcomer_eligible",
    "non_newcomer_eligible",
)


def build_review_candidates(
    eligible_records: Sequence[Mapping[str, Any]],
    requirements_by_task: Mapping[int, Sequence[Mapping[str, Any]]],
    language_vocabulary: set[str],
    signals_by_task: Mapping[int, Sequence[Mapping[str, Any]]],
    body_platform_by_task: Mapping[int, Sequence[Mapping[str, Any]]],
    aggregated_signals: Sequence[Mapping[str, Any]],
    reference_profiles: Sequence[Mapping[str, Any]],
    *,
    maximum: int,
    max_body_chars: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    high_frequency_signals = {
        str(item["signal_name"])
        for item in list(aggregated_signals)[:5]
        if int(item["task_count"]) >= 2
    }
    candidates: list[dict[str, Any]] = []
    for record in eligible_records:
        task_id = int(record["task_candidate_id"])
        valid_requirements = _valid_requirements(requirements_by_task.get(task_id, []))
        signals = list(signals_by_task.get(task_id, []))
        body_platforms = list(body_platform_by_task.get(task_id, []))
        groups, risk = _review_sample_groups(
            record,
            valid_requirements,
            language_vocabulary,
            signals,
            body_platforms,
            high_frequency_signals,
            reference_profiles,
        )
        candidates.append(
            {
                "task_candidate_id": task_id,
                "repository": record["repository"],
                "primary_language": record["primary_language"],
                "issue_number": int(record["issue_number"]),
                "html_url": record["html_url"],
                "title": record["title"],
                "labels": list(record.get("labels") or []),
                "task_types": list(record.get("task_types") or []),
                "body_excerpt": _bounded_body_excerpt(record.get("body_text"), max_body_chars),
                "difficulty": {
                    "code": record.get("estimated_code_difficulty"),
                    "setup": record.get("estimated_setup_difficulty"),
                    "project_context": record.get("estimated_project_context_difficulty"),
                    "collaboration": record.get("estimated_collaboration_difficulty"),
                    "effort": record.get("estimated_effort_bucket"),
                },
                "newcomer_label_signal": bool(record.get("newcomer_label_signal")),
                "existing_skill_requirements": [
                    {
                        "skill_name": requirement.get("skill_name"),
                        "minimum_level": requirement.get("minimum_level"),
                        "importance": requirement.get("importance"),
                        "requirement_source": requirement.get("requirement_source"),
                        "feature_version": requirement.get("feature_version"),
                    }
                    for requirement in valid_requirements
                ],
                "detected_candidate_signals": signals,
                "body_only_platform_signals": body_platforms,
                "sample_groups": sorted(groups),
                "selection_risk_score": risk,
            }
        )

    candidate_by_id = {int(item["task_candidate_id"]): item for item in candidates}
    selected_ids: list[int] = []
    selected_set: set[int] = set()
    target_per_group = 3

    def ordered_for_group(group: str) -> list[dict[str, Any]]:
        return sorted(
            [item for item in candidates if group in item["sample_groups"]],
            key=lambda item: (
                -int(item["selection_risk_score"]),
                str(item["repository"]).casefold(),
                int(item["issue_number"]),
                int(item["task_candidate_id"]),
            ),
        )

    for group in REVIEW_GROUP_PRIORITY:
        already = sum(
            1
            for task_id in selected_ids
            if group in candidate_by_id[task_id]["sample_groups"]
        )
        for item in ordered_for_group(group):
            if already >= target_per_group or len(selected_ids) >= maximum:
                break
            task_id = int(item["task_candidate_id"])
            if task_id in selected_set:
                continue
            selected_ids.append(task_id)
            selected_set.add(task_id)
            already += 1

    if len(selected_ids) < maximum:
        fill = sorted(
            candidates,
            key=lambda item: (
                -len(item["sample_groups"]),
                -int(item["selection_risk_score"]),
                str(item["repository"]).casefold(),
                int(item["issue_number"]),
                int(item["task_candidate_id"]),
            ),
        )
        for item in fill:
            if len(selected_ids) >= maximum:
                break
            task_id = int(item["task_candidate_id"])
            if task_id not in selected_set:
                selected_ids.append(task_id)
                selected_set.add(task_id)

    selected = [candidate_by_id[task_id] for task_id in selected_ids]
    selected.sort(key=_task_sort_key)
    group_counts = Counter(
        group for item in selected for group in item.get("sample_groups") or []
    )
    summary = {
        "requested_maximum": maximum,
        "selected_count": len(selected),
        "selection_method": "deterministic_targeted_stratified_review_queue",
        "target_per_priority_group": target_per_group,
        "group_counts": _counter_dict(group_counts),
        "high_frequency_signal_names": sorted(high_frequency_signals, key=str.casefold),
        "ground_truth": False,
    }
    return selected, summary


def build_documents(
    database_path: str | Path,
    profiles_file: str | Path,
    *,
    generated_at: str | None = None,
    max_review_candidates: int = DEFAULT_MAX_REVIEW_CANDIDATES,
    max_body_chars: int = DEFAULT_MAX_BODY_CHARS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if max_review_candidates < 1:
        raise ValueError("max_review_candidates must be positive")
    if not 100 <= max_body_chars <= 4000:
        raise ValueError("max_body_chars must be between 100 and 4000")

    snapshot = load_database_snapshot(database_path)
    configured = load_configured_profiles(profiles_file)
    reference_profiles = merge_reference_profiles(
        configured["profiles"], snapshot["database_profiles"]
    )

    records = list(snapshot["candidates"])
    requirements = list(snapshot["requirements"])
    requirements_by_task = _requirement_index(requirements)
    active_records = [record for record in records if _is_active(record)]
    eligible_records = [record for record in records if _is_eligible(record)]
    newcomer_records = [
        record for record in eligible_records if bool(record.get("newcomer_label_signal"))
    ]
    active_ids = {int(record["task_candidate_id"]) for record in active_records}
    eligible_ids = {int(record["task_candidate_id"]) for record in eligible_records}

    language_vocabulary = {
        str(record.get("primary_language") or "").strip().casefold()
        for record in active_records
        if str(record.get("primary_language") or "").strip()
    }

    validity_by_row: list[tuple[dict[str, Any], dict[str, Any]]] = [
        (dict(row), requirement_validity(row)) for row in requirements
    ]
    invalid_rows = [
        {"requirement": row, "reasons": validity["schema_invalid_reasons"]}
        for row, validity in validity_by_row
        if not validity["schema_valid"]
    ]
    generator_outliers = [
        row
        for row, validity in validity_by_row
        if validity["current_generator_contract_outlier"]
    ]

    raw_active_requirements = [
        row for row in requirements if int(row["task_candidate_id"]) in active_ids
    ]
    raw_eligible_requirements = [
        row for row in requirements if int(row["task_candidate_id"]) in eligible_ids
    ]
    eligible_valid_requirements = [
        row
        for row, validity in validity_by_row
        if validity["schema_valid"] and int(row["task_candidate_id"]) in eligible_ids
    ]
    active_valid_requirements = [
        row
        for row, validity in validity_by_row
        if validity["schema_valid"] and int(row["task_candidate_id"]) in active_ids
    ]

    identity_counter = Counter(
        (str(record["repository"]).casefold(), int(record["issue_number"]))
        for record in records
    )
    duplicate_identities = [
        {"repository": key[0], "issue_number": key[1], "count": count}
        for key, count in sorted(identity_counter.items())
        if count > 1
    ]
    candidate_json_anomalies = [
        {
            "task_candidate_id": int(record["task_candidate_id"]),
            "repository": record["repository"],
            "issue_number": int(record["issue_number"]),
            "labels_status": record["labels_status"],
            "task_types_status": record["task_types_status"],
        }
        for record in records
        if record["labels_status"] not in {"ok", "empty"}
        or record["task_types_status"] != "ok"
    ]

    normalized_requirement_groups: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for requirement in requirements:
        normalized_requirement_groups[
            (int(requirement["task_candidate_id"]), normalize_skill_name(requirement["skill_name"]))
        ].append(requirement)
    collisions: list[dict[str, Any]] = []
    for (task_id, normalized), rows in sorted(normalized_requirement_groups.items()):
        if len(rows) <= 1:
            continue
        collisions.append(
            {
                "task_candidate_id": task_id,
                "normalized_skill_name": normalized,
                "row_count": len(rows),
                "raw_skill_names": sorted({str(row["skill_name"]) for row in rows}),
                "requirement_sources": sorted(
                    {str(row["requirement_source"]) for row in rows}
                ),
                "minimum_levels": sorted({int(row["minimum_level"]) for row in rows}),
                "importance_values": sorted({float(row["importance"]) for row in rows}),
            }
        )

    skill_counts = [
        len(_valid_requirements(requirements_by_task.get(int(record["task_candidate_id"]), [])))
        for record in eligible_records
    ]
    skill_count_distribution = Counter(_count_bucket(value) for value in skill_counts)
    for bucket in ("0", "1", "2", "3", "4", "5+"):
        skill_count_distribution.setdefault(bucket, 0)
    covered_count = sum(value > 0 for value in skill_counts)
    raw_covered_count = sum(
        bool(requirements_by_task.get(int(record["task_candidate_id"]), []))
        for record in eligible_records
    )

    skill_accumulator: dict[str, dict[str, Any]] = {}
    for requirement in eligible_valid_requirements:
        normalized = normalize_skill_name(requirement["skill_name"])
        item = skill_accumulator.setdefault(
            normalized,
            {
                "normalized_skill_name": normalized,
                "raw_names": Counter(),
                "task_ids": set(),
                "levels": Counter(),
                "importance": Counter(),
                "sources": Counter(),
                "category": classify_requirement(requirement, language_vocabulary),
            },
        )
        item["raw_names"][str(requirement["skill_name"])] += 1
        item["task_ids"].add(int(requirement["task_candidate_id"]))
        item["levels"][str(requirement["minimum_level"])] += 1
        item["importance"][_importance_key(requirement["importance"])] += 1
        item["sources"][str(requirement["requirement_source"])] += 1

    skill_distribution: list[dict[str, Any]] = []
    for normalized, item in skill_accumulator.items():
        representative = sorted(
            item["raw_names"].items(),
            key=lambda pair: (-pair[1], pair[0].casefold(), pair[0]),
        )[0][0]
        skill_distribution.append(
            {
                "normalized_skill_name": normalized,
                "representative_skill_name": representative,
                "category": item["category"],
                "requirement_count": int(sum(item["raw_names"].values())),
                "distinct_task_count": len(item["task_ids"]),
                "minimum_level_distribution": _counter_dict(item["levels"]),
                "importance_distribution": _counter_dict(item["importance"]),
                "requirement_source_distribution": _counter_dict(item["sources"]),
            }
        )
    skill_distribution.sort(
        key=lambda item: (
            -int(item["distinct_task_count"]),
            item["normalized_skill_name"],
        )
    )

    source_accumulator: dict[str, dict[str, Any]] = {}
    for requirement in eligible_valid_requirements:
        source = str(requirement["requirement_source"])
        item = source_accumulator.setdefault(
            source,
            {
                "task_ids": set(),
                "levels": Counter(),
                "importance": Counter(),
                "skills": set(),
            },
        )
        item["task_ids"].add(int(requirement["task_candidate_id"]))
        item["levels"][str(requirement["minimum_level"])] += 1
        item["importance"][_importance_key(requirement["importance"])] += 1
        item["skills"].add(normalize_skill_name(requirement["skill_name"]))
    source_distribution = [
        {
            "requirement_source": source,
            "requirement_count": sum(item["levels"].values()),
            "task_count": len(item["task_ids"]),
            "minimum_level_distribution": _counter_dict(item["levels"]),
            "importance_distribution": _counter_dict(item["importance"]),
            "unique_skill_count": len(item["skills"]),
        }
        for source, item in sorted(source_accumulator.items())
    ]

    level_counter: Counter[str] = Counter()
    level_by_source: dict[str, Counter[str]] = defaultdict(Counter)
    level_by_category: dict[str, Counter[str]] = defaultdict(Counter)
    importance_counter: Counter[str] = Counter()
    importance_by_source: dict[str, Counter[str]] = defaultdict(Counter)
    importance_by_category: dict[str, Counter[str]] = defaultdict(Counter)
    importance_invalid_count = 0
    for requirement in eligible_valid_requirements:
        level_key = str(requirement["minimum_level"])
        category = classify_requirement(requirement, language_vocabulary)
        source = str(requirement["requirement_source"])
        level_counter[level_key] += 1
        level_by_source[source][level_key] += 1
        level_by_category[category][level_key] += 1
        importance_key = _importance_key(requirement["importance"])
        importance_counter[importance_key] += 1
        importance_by_source[source][importance_key] += 1
        importance_by_category[category][importance_key] += 1
    for requirement, validity in validity_by_row:
        if int(requirement["task_candidate_id"]) not in eligible_ids:
            continue
        if any(
            reason.startswith("importance_") for reason in validity["schema_invalid_reasons"]
        ):
            importance_invalid_count += 1

    structure_counts = Counter()
    structure_examples: dict[str, list[int]] = defaultdict(list)
    for record in eligible_records:
        task_id = int(record["task_candidate_id"])
        valid = _valid_requirements(requirements_by_task.get(task_id, []))
        categories = {classify_requirement(req, language_vocabulary) for req in valid}
        if categories == {"programming_language"}:
            structure_counts["only_primary_language"] += 1
            structure_examples["only_primary_language"].append(task_id)
        if (
            categories <= {"programming_language", "task_type"}
            and "programming_language" in categories
            and "task_type" in categories
        ):
            structure_counts["only_primary_language_plus_task_type"] += 1
            structure_examples["only_primary_language_plus_task_type"].append(task_id)
        if "platform" in categories:
            structure_counts["tasks_with_platform"] += 1
        if "other" in categories:
            structure_counts["tasks_with_other_skills"] += 1
        if "other" not in categories:
            structure_counts["tasks_without_fine_grained_other_skills"] += 1
        if categories <= {"programming_language", "task_type", "platform"}:
            structure_counts["coarse_baseline_only"] += 1

    task_type_composition: dict[str, Any] = {}
    for task_type in sorted(PUBLIC_TASK_TYPES):
        ids = {
            int(record["task_candidate_id"])
            for record in eligible_records
            if task_type in {str(value).casefold() for value in record.get("task_types") or []}
        }
        task_type_composition[task_type] = _skill_stats_for_tasks(
            ids, requirements_by_task, language_vocabulary
        )

    by_language: list[dict[str, Any]] = []
    language_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in eligible_records:
        language_groups[str(record.get("primary_language") or "")].append(record)
    for language, group in language_groups.items():
        ids = {int(record["task_candidate_id"]) for record in group}
        stats = _skill_stats_for_tasks(ids, requirements_by_task, language_vocabulary)
        by_language.append({"primary_language": language, **stats})
    by_language.sort(
        key=lambda item: (-int(item["task_count"]), str(item["primary_language"]).casefold())
    )

    by_repository: list[dict[str, Any]] = []
    repository_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in eligible_records:
        repository_groups[str(record["repository"])].append(record)
    for repository, group in repository_groups.items():
        ids = {int(record["task_candidate_id"]) for record in group}
        counts = [
            len(_valid_requirements(requirements_by_task.get(task_id, [])))
            for task_id in ids
        ]
        only_language = 0
        platform_count = 0
        other_count = 0
        for task_id in ids:
            categories = {
                classify_requirement(req, language_vocabulary)
                for req in _valid_requirements(requirements_by_task.get(task_id, []))
            }
            only_language += categories == {"programming_language"}
            platform_count += "platform" in categories
            other_count += "other" in categories
        by_repository.append(
            {
                "repository": repository,
                "primary_languages": sorted(
                    {
                        str(record.get("primary_language") or "")
                        for record in group
                        if str(record.get("primary_language") or "")
                    },
                    key=str.casefold,
                ),
                "eligible_task_count": len(group),
                "average_skill_count": round(statistics.fmean(counts), 4) if counts else None,
                "only_language_count": int(only_language),
                "platform_task_count": int(platform_count),
                "other_skill_task_count": int(other_count),
            }
        )
    by_repository.sort(
        key=lambda item: (-int(item["eligible_task_count"]), str(item["repository"]).casefold())
    )

    platform_counter: Counter[str] = Counter()
    plain_platform_rows: list[dict[str, Any]] = []
    invalid_platform_rows: list[dict[str, Any]] = []
    platform_by_task: dict[int, list[str]] = defaultdict(list)
    for requirement in eligible_valid_requirements:
        normalized = normalize_skill_name(requirement["skill_name"])
        if normalized.startswith("platform:"):
            platform_counter[normalized] += 1
            platform_by_task[int(requirement["task_candidate_id"])].append(normalized)
            if normalized not in VALID_PLATFORM_SKILLS:
                invalid_platform_rows.append(dict(requirement))
        elif normalized in PLAIN_PLATFORM_SKILLS:
            plain_platform_rows.append(dict(requirement))
    multi_platform = [
        {"task_candidate_id": task_id, "platform_requirements": sorted(set(values))}
        for task_id, values in sorted(platform_by_task.items())
        if len(set(values)) > 1
    ]

    body_platform_by_task: dict[int, list[dict[str, Any]]] = {}
    body_platform_queue: list[dict[str, Any]] = []
    for record in eligible_records:
        detected = detect_body_only_platform_signals(record)
        if not detected:
            continue
        task_id = int(record["task_candidate_id"])
        body_platform_by_task[task_id] = detected
        existing_platforms = sorted(
            normalize_skill_name(req["skill_name"])
            for req in _valid_requirements(requirements_by_task.get(task_id, []))
            if normalize_skill_name(req["skill_name"]).startswith("platform:")
        )
        body_platform_queue.append(
            {
                "task_candidate_id": task_id,
                "repository": record["repository"],
                "issue_number": int(record["issue_number"]),
                "title": record["title"],
                "task_types": list(record.get("task_types") or []),
                "existing_platform_requirements": existing_platforms,
                "matched_body_platform_tokens": detected,
                "body_excerpt": _bounded_body_excerpt(record.get("body_text"), 500),
            }
        )
    body_platform_queue.sort(key=_task_sort_key)

    signals_by_task: dict[int, list[dict[str, Any]]] = {}
    for record in eligible_records:
        signals = detect_candidate_signals(record)
        if signals:
            signals_by_task[int(record["task_candidate_id"])] = signals
    aggregated_signals = _aggregate_candidate_signals(
        eligible_records, signals_by_task, reference_profiles
    )

    configured_vocab = sorted(
        {
            skill
            for profile in configured["profiles"]
            for skill in (profile.get("skills") or {})
        }
    )
    database_vocab = sorted(
        {
            skill
            for profile in snapshot["database_profiles"]
            for skill in (profile.get("skills") or {})
        }
    )
    reference_vocab = sorted(
        {
            skill
            for profile in reference_profiles
            for skill in (profile.get("skills") or {})
        }
    )

    unsupported_requirements: list[dict[str, Any]] = []
    for requirement in eligible_valid_requirements:
        if not any(
            _profile_has_skill_name(profile, str(requirement["skill_name"]))
            for profile in reference_profiles
        ):
            unsupported_requirements.append(dict(requirement))
    unsupported_task_ids = {
        int(requirement["task_candidate_id"]) for requirement in unsupported_requirements
    }

    importance_one_requirements = [
        requirement
        for requirement in eligible_valid_requirements
        if math.isclose(float(requirement["importance"]), 1.0, rel_tol=0.0, abs_tol=1e-12)
    ]
    importance_one_unknown = [
        requirement
        for requirement in importance_one_requirements
        if not any(
            _profile_has_skill_name(profile, str(requirement["skill_name"]))
            for profile in reference_profiles
        )
    ]

    raw_critical_pairs: list[dict[str, Any]] = []
    for record in eligible_records:
        task_id = int(record["task_candidate_id"])
        valid = _valid_requirements(requirements_by_task.get(task_id, []))
        for profile in reference_profiles:
            for requirement in valid:
                importance = float(requirement["importance"])
                if importance < 1.0:
                    continue
                skill_name = str(requirement["skill_name"])
                needed = int(requirement["minimum_level"])
                actual = _profile_support_level(profile, skill_name)
                gap = max(needed - actual, 0)
                is_platform = normalize_skill_name(skill_name).startswith("platform:")
                critical = gap > 0 and (
                    is_platform
                    or str(profile.get("service_track")) == "newcomer"
                    or gap > 1
                )
                if critical:
                    raw_critical_pairs.append(
                        {
                            "task_candidate_id": task_id,
                            "profile_key": profile["profile_key"],
                            "skill_name": skill_name,
                            "required_level": needed,
                            "profile_level": actual,
                            "gap": gap,
                        }
                    )

    preference_compatible_critical_pairs: list[dict[str, Any]] = []
    for record in eligible_records:
        task_id = int(record["task_candidate_id"])
        task_language = str(record.get("primary_language") or "").casefold()
        task_types = {str(value).casefold() for value in record.get("task_types") or []}
        valid = _valid_requirements(requirements_by_task.get(task_id, []))
        for profile in reference_profiles:
            if str(profile.get("service_track")) == "newcomer" and not bool(
                record.get("newcomer_label_signal")
            ):
                continue
            if int(record.get("estimated_code_difficulty") or 0) > int(
                profile.get("max_code_difficulty", 3)
            ):
                continue
            if int(record.get("estimated_setup_difficulty") or 0) > int(
                profile.get("max_setup_difficulty", 3)
            ):
                continue
            preferred_languages = {
                str(value).casefold() for value in profile.get("preferred_languages") or []
            }
            # Reference profiles loaded for diagnostics do not persist preferred_languages;
            # when absent, do not invent a language filter.
            if preferred_languages and task_language not in preferred_languages:
                continue
            preferred_types = {
                str(value).casefold() for value in profile.get("preferred_task_types") or []
            }
            if preferred_types and not preferred_types.intersection(task_types):
                continue
            for requirement in valid:
                if float(requirement["importance"]) < 1.0:
                    continue
                skill_name = str(requirement["skill_name"])
                needed = int(requirement["minimum_level"])
                actual = _profile_support_level(profile, skill_name)
                gap = max(needed - actual, 0)
                is_platform = normalize_skill_name(skill_name).startswith("platform:")
                if gap > 0 and (
                    is_platform
                    or str(profile.get("service_track")) == "newcomer"
                    or gap > 1
                ):
                    preference_compatible_critical_pairs.append(
                        {
                            "task_candidate_id": task_id,
                            "profile_key": profile["profile_key"],
                            "skill_name": skill_name,
                            "required_level": needed,
                            "profile_level": actual,
                            "gap": gap,
                        }
                    )

    missing_source_count = sum(
        1
        for requirement in requirements
        if int(requirement["task_candidate_id"]) in eligible_ids
        and not str(requirement.get("requirement_source") or "").strip()
    )
    missing_version_count = sum(
        1
        for requirement in requirements
        if int(requirement["task_candidate_id"]) in eligible_ids
        and not str(requirement.get("feature_version") or "").strip()
    )

    review_records, review_summary = build_review_candidates(
        eligible_records,
        requirements_by_task,
        language_vocabulary,
        signals_by_task,
        body_platform_by_task,
        aggregated_signals,
        reference_profiles,
        maximum=max_review_candidates,
        max_body_chars=max_body_chars,
    )

    task_feature_version_counts = Counter(
        str(record.get("task_feature_version") or "") for record in eligible_records
    )
    eligible_requirement_versions = Counter(
        str(requirement.get("feature_version") or "")
        for requirement in eligible_valid_requirements
    )

    generated = generated_at or utc_now()
    diagnostics = {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "generated_at": generated,
        "methodological_boundary": {
            "ground_truth_used": False,
            "accuracy_metrics_permitted": False,
            "candidate_signals_are_requirements": False,
            "matching_rankings_computed": False,
            "purpose": (
                "baseline structure, explainability, candidate-signal, profile-vocabulary, "
                "and static matching-risk diagnostics"
            ),
        },
        "database": {
            "path": snapshot["database_path"],
            "sha256_before": snapshot["database_sha256_before"],
            "sha256_after": snapshot["database_sha256_after"],
            "unchanged": snapshot["database_unchanged"],
            "sqlite_access_mode": snapshot["sqlite_access_mode"],
            "schema_columns": snapshot["schema"],
        },
        "profile_reference": {
            "configured_profiles_file": configured["path"],
            "configured_profiles_sha256": configured["sha256"],
            "configured_profile_count": len(configured["profiles"]),
            "database_profile_count": len(snapshot["database_profiles"]),
            "reference_profile_count": len(reference_profiles),
            "merge_policy": "database_profile_overrides_same_configured_profile_key",
            "configured_skill_vocabulary": configured_vocab,
            "database_skill_vocabulary": database_vocab,
            "reference_skill_vocabulary": reference_vocab,
            "reference_profiles": [
                {
                    "profile_key": profile["profile_key"],
                    "service_track": profile["service_track"],
                    "preferred_languages": list(profile.get("preferred_languages") or []),
                    "preferred_task_types": list(profile.get("preferred_task_types") or []),
                    "max_code_difficulty": profile.get("max_code_difficulty"),
                    "max_setup_difficulty": profile.get("max_setup_difficulty"),
                    "operating_systems": list(profile.get("operating_systems") or []),
                    "skills": dict(sorted((profile.get("skills") or {}).items())),
                    "source": profile.get("source"),
                }
                for profile in reference_profiles
            ],
        },
        "scope": {
            "primary_scope": "active_eligible_candidates",
            "total_candidate_count": len(records),
            "active_candidate_count": len(active_records),
            "eligible_candidate_count": len(eligible_records),
            "newcomer_eligible_count": len(newcomer_records),
            "task_feature_version_distribution": _counter_dict(task_feature_version_counts),
            "eligible_requirement_feature_version_distribution": _counter_dict(
                eligible_requirement_versions
            ),
        },
        "data_integrity": {
            "distinct_task_identity_count": len(identity_counter),
            "duplicate_task_identity_count": len(duplicate_identities),
            "duplicate_task_identities": duplicate_identities,
            "total_skill_requirement_count": len(requirements),
            "active_skill_requirement_count": len(raw_active_requirements),
            "eligible_skill_requirement_count": len(raw_eligible_requirements),
            "active_valid_skill_requirement_count": len(active_valid_requirements),
            "eligible_valid_skill_requirement_count": len(eligible_valid_requirements),
            "candidate_json_anomaly_count": len(candidate_json_anomalies),
            "candidate_json_anomalies": candidate_json_anomalies[:100],
            "orphan_requirement_count": len(snapshot["orphans"]),
            "orphan_requirements": snapshot["orphans"],
            "schema_invalid_requirement_count": len(invalid_rows),
            "schema_invalid_requirements": invalid_rows[:100],
            "current_generator_contract_outlier_count": len(generator_outliers),
            "current_generator_contract_outliers": generator_outliers[:100],
        },
        "skill_coverage": {
            "eligible_task_count": len(eligible_records),
            "covered_task_count": covered_count,
            "no_skill_task_count": len(eligible_records) - covered_count,
            "coverage_rate": _rate(covered_count, len(eligible_records)),
            "raw_any_row_covered_task_count": raw_covered_count,
            "skill_count_distribution": {
                bucket: int(skill_count_distribution[bucket])
                for bucket in ("0", "1", "2", "3", "4", "5+")
            },
            "mean_skills_per_task": round(statistics.fmean(skill_counts), 4)
            if skill_counts
            else None,
            "median_skills_per_task": statistics.median(skill_counts)
            if skill_counts
            else None,
            "p90_skills_per_task": _nearest_rank(skill_counts, 0.90),
            "min_skills_per_task": min(skill_counts) if skill_counts else None,
            "max_skills_per_task": max(skill_counts) if skill_counts else None,
            "interpretation_boundary": (
                "coverage means at least one schema-valid requirement; it is not a measure "
                "of skill-extraction quality or granularity"
            ),
        },
        "skill_name_distribution": {
            "classification_rule": {
                "platform": "skill_name starts with platform:",
                "task_type": "normalized skill_name is in developer_profiles.ALLOWED_TASK_TYPES",
                "programming_language": "normalized skill_name appears as an active repository primary_language",
                "other": "everything else",
                "precedence": ["platform", "task_type", "programming_language", "other"],
            },
            "programming_language_vocabulary": sorted(language_vocabulary),
            "unique_skill_count": len(skill_distribution),
            "other_unique_skill_count": sum(
                item["category"] == "other" for item in skill_distribution
            ),
            "skills": skill_distribution,
        },
        "requirement_source_distribution": source_distribution,
        "minimum_level": {
            "schema_contract": "0..4",
            "current_generator_contract": "1..3",
            "distribution": _counter_dict(level_counter),
            "by_requirement_source": {
                key: _counter_dict(value) for key, value in sorted(level_by_source.items())
            },
            "by_skill_category": {
                key: _counter_dict(value) for key, value in sorted(level_by_category.items())
            },
            "schema_invalid_count": len(
                [
                    row
                    for row, validity in validity_by_row
                    if int(row["task_candidate_id"]) in eligible_ids
                    and any(
                        reason.startswith("minimum_level_")
                        for reason in validity["schema_invalid_reasons"]
                    )
                ]
            ),
            "current_generator_contract_outlier_count": len(
                [
                    row
                    for row in generator_outliers
                    if int(row["task_candidate_id"]) in eligible_ids
                ]
            ),
        },
        "importance": {
            "valid_contract": "0 < importance <= 1",
            "distribution": _counter_dict(importance_counter),
            "by_requirement_source": {
                key: _counter_dict(value)
                for key, value in sorted(importance_by_source.items())
            },
            "by_skill_category": {
                key: _counter_dict(value)
                for key, value in sorted(importance_by_category.items())
            },
            "invalid_count": importance_invalid_count,
            "importance_one_requirement_count": len(importance_one_requirements),
            "importance_one_task_count": len(
                {int(row["task_candidate_id"]) for row in importance_one_requirements}
            ),
        },
        "baseline_structure": {
            **{key: int(value) for key, value in sorted(structure_counts.items())},
            "eligible_task_count": len(eligible_records),
            "fine_grained_definition": (
                "a schema-valid requirement classified as other, i.e. not repository primary "
                "language, public task type, or platform namespace"
            ),
            "example_task_candidate_ids": {
                key: values[:20] for key, values in sorted(structure_examples.items())
            },
        },
        "task_type_skill_composition": task_type_composition,
        "by_language": by_language,
        "by_repository": by_repository,
        "platform_diagnostics": {
            "platform_requirement_distribution": _counter_dict(platform_counter),
            "plain_platform_skill_count": len(plain_platform_rows),
            "plain_platform_skills": plain_platform_rows[:100],
            "invalid_platform_namespace_count": len(invalid_platform_rows),
            "invalid_platform_requirements": invalid_platform_rows[:100],
            "multi_platform_requirement_task_count": len(multi_platform),
            "multi_platform_requirement_tasks": multi_platform[:100],
            "body_only_platform_signal_candidate_count": len(body_platform_queue),
            "body_only_platform_signal_candidates": body_platform_queue,
            "boundary": (
                "body-only platform signals are a manual-review queue and are not asserted "
                "to be true platform requirements"
            ),
        },
        "evidence_gap": {
            "missing_requirement_source_count": missing_source_count,
            "missing_feature_version_count": missing_version_count,
            "persisted_requirement_fields": [
                "skill_name",
                "minimum_level",
                "importance",
                "requirement_source",
                "feature_version",
            ],
            "persisted_text_evidence_fields": [],
            "full_text_evidence_supported_by_requirement_schema": False,
            "assessment": (
                "requirement_source records provenance class but cannot by itself preserve "
                "the exact matched title/label/body evidence for a future fine-grained skill"
            ),
        },
        "candidate_skill_signals": {
            "lexicon_scope": "diagnostics_only_not_production",
            "lexicon": [
                {
                    "signal_name": rule.name,
                    "candidate_category": rule.category,
                    "aliases": list(rule.aliases),
                    "ambiguous": rule.ambiguous,
                }
                for rule in CANDIDATE_SIGNAL_RULES
            ],
            "tasks_with_any_candidate_signal": len(signals_by_task),
            "signal_count": len(aggregated_signals),
            "signals": aggregated_signals,
        },
        "profile_vocabulary_compatibility": {
            "reference_profile_count": len(reference_profiles),
            "eligible_requirement_unknown_to_all_profiles_count": len(
                unsupported_requirements
            ),
            "eligible_task_with_unknown_requirement_count": len(unsupported_task_ids),
            "unknown_requirement_examples": unsupported_requirements[:100],
            "candidate_signal_compatibility": [
                {
                    "candidate_skill": item["signal_name"],
                    "task_signal_count": item["task_count"],
                    "current_profile_support": item["current_profile_support"],
                    "profile_count_with_skill": item["profile_count_with_skill"],
                    "potential_unknown_skill_risk": item["potential_unknown_skill_risk"],
                }
                for item in aggregated_signals
            ],
        },
        "matching_static_risk": {
            "matching_rankings_computed": False,
            "matching_py_called": False,
            "analysis_type": "raw_requirement_profile_pair_static_risk_only",
            "importance_one_unknown_to_all_profiles_count": len(importance_one_unknown),
            "importance_one_unknown_task_count": len(
                {int(row["task_candidate_id"]) for row in importance_one_unknown}
            ),
            "raw_potential_critical_mismatch_pair_count": len(raw_critical_pairs),
            "raw_potential_critical_mismatch_task_count": len(
                {int(row["task_candidate_id"]) for row in raw_critical_pairs}
            ),
            "raw_potential_critical_mismatch_pairs": raw_critical_pairs[:200],
            "filter_compatible_potential_critical_mismatch_pair_count": len(
                preference_compatible_critical_pairs
            ),
            "filter_compatible_potential_critical_mismatch_task_count": len(
                {
                    int(row["task_candidate_id"])
                    for row in preference_compatible_critical_pairs
                }
            ),
            "filter_compatible_potential_critical_mismatch_pairs": (
                preference_compatible_critical_pairs[:200]
            ),
            "interpretation_boundary": (
                "raw counts are requirement-side only; filter-compatible counts manually mirror "
                "the current non-ranking track/difficulty/language/task-type filters before the "
                "critical skill condition. Neither path calls matching.py, computes rankings, or "
                "measures recommendation quality"
            ),
        },
        "dedup_collision_diagnostics": {
            "normalized_duplicate_group_count": len(collisions),
            "normalized_duplicate_groups": collisions,
            "inference_design_risk": (
                "infer_skill_requirements currently keys a dict by skill_name.casefold(); future "
                "multi-source extraction needs an explicit merge policy instead of implicit "
                "last-writer-wins behavior"
            ),
        },
        "review_candidate_summary": review_summary,
    }

    review_document = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "generated_at": generated,
        "database_path": snapshot["database_path"],
        "database_sha256": snapshot["database_sha256_after"],
        "profiles_file": configured["path"],
        "profiles_sha256": configured["sha256"],
        "methodological_boundary": {
            "ground_truth": False,
            "purpose": "targeted manual/AI-assisted review queue for B4 design",
            "candidate_signals_are_requirements": False,
        },
        "selection_summary": review_summary,
        "records": review_records,
    }
    return diagnostics, review_document


def write_json(document: Mapping[str, Any], output_path: str | Path) -> Path:
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export read-only OSS-Mentor B4 skill-requirement baseline diagnostics."
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--profiles-file", type=Path, default=DEFAULT_PROFILES_FILE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--review-output", type=Path, default=DEFAULT_REVIEW_OUTPUT)
    parser.add_argument(
        "--max-review-candidates",
        type=int,
        default=DEFAULT_MAX_REVIEW_CANDIDATES,
    )
    parser.add_argument(
        "--max-body-chars",
        type=int,
        default=DEFAULT_MAX_BODY_CHARS,
    )
    parser.add_argument(
        "--generated-at",
        help="Optional fixed timestamp for deterministic tests; normally omitted.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    diagnostics, review_document = build_documents(
        args.database,
        args.profiles_file,
        generated_at=args.generated_at,
        max_review_candidates=args.max_review_candidates,
        max_body_chars=args.max_body_chars,
    )
    output = write_json(diagnostics, args.output)
    review_output = write_json(review_document, args.review_output)
    print(
        json.dumps(
            {
                "event": "skill_requirement_diagnostics_exported",
                "database_path": diagnostics["database"]["path"],
                "database_unchanged": diagnostics["database"]["unchanged"],
                "eligible_task_count": diagnostics["scope"]["eligible_candidate_count"],
                "eligible_requirement_count": diagnostics["data_integrity"][
                    "eligible_skill_requirement_count"
                ],
                "eligible_valid_requirement_count": diagnostics["data_integrity"][
                    "eligible_valid_skill_requirement_count"
                ],
                "skill_coverage_rate": diagnostics["skill_coverage"]["coverage_rate"],
                "unique_skill_count": diagnostics["skill_name_distribution"][
                    "unique_skill_count"
                ],
                "tasks_with_other_skills": diagnostics["baseline_structure"].get(
                    "tasks_with_other_skills", 0
                ),
                "tasks_with_candidate_signals": diagnostics["candidate_skill_signals"][
                    "tasks_with_any_candidate_signal"
                ],
                "review_candidate_count": review_document["selection_summary"][
                    "selected_count"
                ],
                "matching_rankings_computed": False,
                "output_path": str(output),
                "review_output_path": str(review_output),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())