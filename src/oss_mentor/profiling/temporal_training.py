"""Build time-safe research profiles from A's temporal X handoff.

This module is deliberately B-owned.  It consumes only pre-cutoff file
contributions, verifies every referenced byte and identity projection, and
produces the 23 ordered features expected by C's advanced-adapter contract.
Outcome labels are never accepted here, which keeps X construction separate
from the later Y join.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from oss_mentor.datasets.code_languages import code_language

from .assessment import (
    _fuse_feature,
    _iso_utc,
    _normalize_external_evidence,
    load_questionnaire,
)
from .evidence import interpret_github_contributions


TEMPORAL_PROFILE_SCHEMA_VERSION = "enhanced-profile-training-export-v0.1"
TEMPORAL_PROFILE_STATE = "research_frozen"
TEMPORAL_PROFILE_RULES_VERSION = "temporal-profile-baseline-v0.1"
TEMPORAL_DATASET_VERSION = "temporal_xy_2024_2026_v3"

_LANGUAGE_FEATURES = {
    "go": "preference.language.go",
    "java": "preference.language.java",
    "javascript": "preference.language.javascript",
    "python": "preference.language.python",
    "rust": "preference.language.rust",
    "typescript": "preference.language.typescript",
}


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from error
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: row must be an object")
            rows.append(row)
    return rows


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _timestamp(value: Any, field: str) -> datetime:
    normalized = _iso_utc(value, field)
    return datetime.fromisoformat(normalized.replace("Z", "+00:00"))


def _digest(payload: Any, prefix: str, length: int = 24) -> str:
    value = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return f"{prefix}{value[:length]}"


def _identity_candidates(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    result: list[Mapping[str, Any]] = []
    for container_name in ("payload", "fields"):
        container = payload.get(container_name)
        if not isinstance(container, Mapping):
            continue
        result.append(container)
        for field in ("author", "user", "committer", "owner"):
            value = container.get(field)
            if isinstance(value, Mapping):
                result.append(value)
    return result


def verified_login_from_evidence(
    payload: Mapping[str, Any],
    *,
    github_numeric_id: int,
) -> tuple[str, str]:
    """Return a login only when the evidence object carries the same numeric ID."""
    matches: set[str] = set()
    for candidate in _identity_candidates(payload):
        if candidate.get("id") != github_numeric_id:
            continue
        login = candidate.get("login")
        if isinstance(login, str) and login.strip():
            matches.add(login.strip().casefold())
    if not matches:
        raise ValueError(
            f"identity evidence does not map GitHub numeric ID {github_numeric_id} to a login"
        )
    if len(matches) != 1:
        raise ValueError(
            f"identity evidence maps GitHub numeric ID {github_numeric_id} to conflicting logins"
        )
    observed_at = _iso_utc(payload.get("observed_at"), "identity_evidence.observed_at")
    return next(iter(matches)), observed_at


def _choose_source(
    row: Mapping[str, Any],
    *,
    source_roots: Mapping[str, Path],
) -> tuple[dict[str, Any], Path, Path]:
    candidates: list[tuple[int, str, dict[str, Any], Path, Path]] = []
    priorities = {"backfill": 0, "ab": 1}
    sources = row.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("contribution row has no sources")
    for raw in sources:
        if not isinstance(raw, Mapping):
            continue
        source = raw.get("source")
        if source not in source_roots:
            continue
        content_location = raw.get("content_location")
        evidence_location = raw.get("evidence_ref")
        if not isinstance(content_location, str) or not isinstance(evidence_location, str):
            continue
        root = Path(source_roots[source])
        content_path = root / content_location
        evidence_path = root / evidence_location
        if content_path.is_file() and evidence_path.is_file():
            candidates.append(
                (
                    priorities.get(str(source), 99),
                    f"{content_location}\0{evidence_location}",
                    dict(raw),
                    content_path,
                    evidence_path,
                )
            )
    if not candidates:
        raise ValueError("no source has both content bytes and identity evidence")
    _, _, source, content_path, evidence_path = min(candidates, key=lambda item: item[:2])
    return source, content_path, evidence_path


def _language_evidence(
    materials: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in materials:
        language = code_language(str(row["path"]))
        feature_name = _LANGUAGE_FEATURES.get(str(language or "").casefold())
        if feature_name is None:
            continue
        evidence_ref = _digest(
            {
                "source_ref": row["source_ref"],
                "content_sha256": row["content_sha256"],
                "feature_name": feature_name,
            },
            "github-language:",
        )
        result.append(
            {
                "feature_name": feature_name,
                "value": 1.0,
                "confidence": 0.65,
                "source_kind": "github_direct",
                "evidence_ref": evidence_ref,
                "observed_at": row["observed_at"],
                "source_ref": row["source_ref"],
                "content_sha256": row["content_sha256"],
            }
        )
    return result


def _fuse_evidence(
    feature_names: list[str],
    evidence: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    normalized = _normalize_external_evidence(
        evidence,
        allowed_features=set(feature_names),
    )
    by_feature: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in normalized:
        by_feature[row["feature_name"]].append(row)
    return [
        _fuse_feature(feature_name, by_feature.get(feature_name, []))
        for feature_name in feature_names
    ]


def build_temporal_training_profile(
    group: Mapping[str, Any],
    *,
    contributions_by_key: Mapping[tuple[str, str, str], Mapping[str, Any]],
    source_roots: Mapping[str, Path],
    questionnaire: Mapping[str, Any],
    delivery_status: str = "preview_not_for_training",
    dataset_version: str = TEMPORAL_DATASET_VERSION,
    rules_version: str = TEMPORAL_PROFILE_RULES_VERSION,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build one immutable snapshot from a single developer/cutoff X group."""
    github_numeric_id = group.get("github_numeric_id")
    if isinstance(github_numeric_id, bool) or not isinstance(github_numeric_id, int):
        raise ValueError("github_numeric_id must be an integer")
    split = _text(group.get("split"), "split")
    if split not in {"train", "validation", "test"}:
        raise ValueError("split must be train, validation, or test")
    as_of = _iso_utc(group.get("cutoff_at"), "cutoff_at")
    cutoff = _timestamp(as_of, "cutoff_at")
    raw_keys = group.get("x_contribution_keys")
    if not isinstance(raw_keys, list) or not raw_keys:
        raise ValueError("x_contribution_keys must be a non-empty array")
    keys: list[tuple[str, str, str]] = []
    for index, raw_key in enumerate(raw_keys):
        if not isinstance(raw_key, list) or len(raw_key) != 3:
            raise ValueError(f"x_contribution_keys[{index}] must contain three strings")
        key = tuple(_text(value, f"x_contribution_keys[{index}]") for value in raw_key)
        keys.append(key)  # type: ignore[arg-type]
    if len(keys) != len(set(keys)):
        raise ValueError("x_contribution_keys contains duplicates")

    materials: list[dict[str, Any]] = []
    identity_observations: list[tuple[datetime, str]] = []
    code_hash_cache: dict[Path, str] = {}
    for key in keys:
        raw = contributions_by_key.get(key)
        if raw is None:
            raise ValueError(f"contribution index is missing X key: {key}")
        row = dict(raw)
        if row.get("github_numeric_id") != github_numeric_id:
            raise ValueError(f"X key belongs to another GitHub identity: {key}")
        if row.get("content_available") is not True:
            raise ValueError(f"X key has unavailable content: {key}")
        event_at = _iso_utc(row.get("event_at"), "contribution.event_at")
        if _timestamp(event_at, "contribution.event_at") >= cutoff:
            raise ValueError(f"X key is not strictly before cutoff: {key}")

        source, content_path, evidence_path = _choose_source(
            row,
            source_roots=source_roots,
        )
        if source.get("github_numeric_id") != github_numeric_id:
            raise ValueError(f"source identity does not match X group: {key}")
        source_event = _iso_utc(source.get("event_at"), "source.event_at")
        if source_event != event_at:
            raise ValueError(f"source event timestamp does not match index: {key}")
        expected_hash = _text(row.get("content_sha256"), "content_sha256").casefold()
        if source.get("content_sha256") != expected_hash:
            raise ValueError(f"source content hash does not match index: {key}")
        actual_hash = code_hash_cache.get(content_path)
        if actual_hash is None:
            actual_hash = sha256_file(content_path)
            code_hash_cache[content_path] = actual_hash
        if actual_hash != expected_hash:
            raise ValueError(f"content bytes fail SHA-256 verification: {key}")

        evidence_payload = json.loads(evidence_path.read_text(encoding="utf-8-sig"))
        login, observed_at = verified_login_from_evidence(
            evidence_payload,
            github_numeric_id=github_numeric_id,
        )
        identity_observations.append((_timestamp(observed_at, "observed_at"), login))
        materials.append(
            {
                "repository": key[0],
                "commit_sha": key[1],
                "path": key[2],
                "event_at": event_at,
                "observed_at": observed_at,
                "content_sha256": expected_hash,
                "file_version_key": _text(
                    source.get("file_version_key"), "source.file_version_key"
                ),
                "source_ref": f"{source['source']}:{source['evidence_ref']}",
            }
        )

    if not identity_observations:
        raise ValueError("profile has no verified identity observations")
    identity_observations.sort()
    developer_key = identity_observations[-1][1]
    latest_time = identity_observations[-1][0]
    latest_logins = {
        login for observed_at, login in identity_observations if observed_at == latest_time
    }
    if len(latest_logins) != 1:
        raise ValueError("latest identity evidence contains conflicting GitHub logins")
    aliases = sorted({login for _, login in identity_observations if login != developer_key})

    contribution_records: list[dict[str, Any]] = []
    code_documents: dict[str, dict[str, Any]] = {}
    for row in materials:
        file_version_key = row["file_version_key"]
        document = {
            "file_version_key": file_version_key,
            "repository_key": row["repository"],
            "path": row["path"],
            "available": True,
            "exclusion_reason": None,
        }
        existing = code_documents.get(file_version_key)
        if existing is not None and existing != document:
            raise ValueError(f"conflicting code document: {file_version_key}")
        code_documents[file_version_key] = document
        contribution_records.append(
            {
                "developer_key": developer_key,
                "repository_key": row["repository"],
                "file_key": f"{row['repository']}:{row['path']}",
                "file_version_key": file_version_key,
                "commit_sha": row["commit_sha"],
                "event_at": row["event_at"],
                "observed_at": row["observed_at"],
                "behavior_type": "commit",
                "author_link_status": "verified",
                "evidence_location": row["source_ref"],
            }
        )

    observed_at = max(row["observed_at"] for row in materials)
    batch = interpret_github_contributions(
        contribution_records,
        code_documents.values(),
        developer_key=developer_key,
        observed_at=observed_at,
        as_of=as_of,
        collection_status="complete",
        collection_limitations=(
            "commit event time is used; first public availability was not independently verified",
            "weak-interest evidence and post-cutoff outcomes are excluded",
        ),
    )
    if batch["coverage"]["excluded_record_count"]:
        raise ValueError(
            "validated X unexpectedly produced excluded contribution records: "
            f"{batch['coverage']['exclusion_counts']}"
        )
    language_evidence = _language_evidence(materials)
    evidence = [*batch["evidence"], *language_evidence]
    feature_names = list(questionnaire["feature_names"])
    features = _fuse_evidence(feature_names, evidence)

    version_material = {
        "dataset_version": dataset_version,
        "rules_version": rules_version,
        "github_numeric_id": github_numeric_id,
        "as_of": as_of,
        "x": [
            {
                "key": list(key),
                "content_sha256": contributions_by_key[key]["content_sha256"],
            }
            for key in sorted(keys)
        ],
    }
    profile_key = _digest(
        {"github_numeric_id": github_numeric_id},
        "research-profile:",
    )
    profile_version = _digest(version_material, "research-profile-version:")
    row: dict[str, Any] = {
        "schema_version": TEMPORAL_PROFILE_SCHEMA_VERSION,
        "profile_state": TEMPORAL_PROFILE_STATE,
        "delivery_status": _text(delivery_status, "delivery_status"),
        "profile_key": profile_key,
        "profile_version": profile_version,
        "developer_key": developer_key,
        "github_numeric_id": github_numeric_id,
        "identity_aliases": aliases,
        "as_of": as_of,
        "split": split,
        "feature_schema_version": questionnaire["feature_schema_version"],
        "feature_names": feature_names,
        "values": [float(item["proposed_value"]) for item in features],
        "observed_mask": [bool(item["observed"]) for item in features],
        "locked_mask": [False for _ in features],
        "current_interests": [],
        "evidence_refs": [list(item["evidence_refs"]) for item in features],
        "source_dataset_version": dataset_version,
        "rules_version": rules_version,
        "x_contribution_count": len(keys),
        "time_basis": "commit_event_at_strictly_before_as_of",
        "interpretation": "historical_direct_contribution_proxy_not_verified_ability",
    }
    hash_material = dict(row)
    hash_material.pop("delivery_status", None)
    row["profile_hash"] = hashlib.sha256(canonical_json_bytes(hash_material)).hexdigest()
    validate_temporal_training_profile(row, verify_hash=True)
    audit = {
        "github_numeric_id": github_numeric_id,
        "developer_key": developer_key,
        "as_of": as_of,
        "split": split,
        "x_contribution_count": len(keys),
        "interpreted_record_count": batch["coverage"]["interpreted_record_count"],
        "evidence_count": len(evidence),
        "observed_feature_count": sum(row["observed_mask"]),
        "identity_aliases": aliases,
        "contribution_records": batch["records"],
        "language_evidence": [
            {
                "feature_name": item["feature_name"],
                "evidence_ref": item["evidence_ref"],
                "source_ref": item["source_ref"],
                "content_sha256": item["content_sha256"],
            }
            for item in language_evidence
        ],
    }
    return row, audit


def validate_temporal_training_profile(
    row: Mapping[str, Any],
    *,
    verify_hash: bool = False,
) -> None:
    if row.get("schema_version") != TEMPORAL_PROFILE_SCHEMA_VERSION:
        raise ValueError("unsupported temporal profile schema_version")
    if row.get("profile_state") != TEMPORAL_PROFILE_STATE:
        raise ValueError("profile_state must be research_frozen")
    for field in ("profile_key", "profile_version", "developer_key"):
        _text(row.get(field), field)
    if str(row["developer_key"]).strip().casefold() == str(row["profile_key"]).strip().casefold():
        raise ValueError("developer_key must not reuse profile_key")
    _timestamp(row.get("as_of"), "as_of")
    feature_names = row.get("feature_names")
    if not isinstance(feature_names, list) or feature_names != sorted(feature_names):
        raise ValueError("feature_names must use stable sorted order")
    if len(feature_names) != 23 or len(set(feature_names)) != 23:
        raise ValueError("feature_names must contain 23 unique names")
    length = len(feature_names)
    for field in ("values", "observed_mask", "locked_mask", "evidence_refs"):
        value = row.get(field)
        if not isinstance(value, list) or len(value) != length:
            raise ValueError(f"{field} must align with feature_names")
    for index, value in enumerate(row["values"]):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("values entries must be JSON numbers")
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError("values entries must be within [0, 1]")
        observed = row["observed_mask"][index]
        if not isinstance(observed, bool):
            raise ValueError("observed_mask entries must be boolean")
        if not observed and float(value) != 0.0:
            raise ValueError("unobserved dimensions must use zero placeholders")
    if any(not isinstance(value, bool) for value in row["locked_mask"]):
        raise ValueError("locked_mask entries must be boolean")
    for refs in row["evidence_refs"]:
        if not isinstance(refs, list) or any(
            not isinstance(ref, str) or not ref for ref in refs
        ):
            raise ValueError("evidence_refs entries must be arrays of non-empty strings")
    if row.get("current_interests") != []:
        raise ValueError("direct historical X must not invent current interests")
    profile_hash = row.get("profile_hash")
    if not isinstance(profile_hash, str) or len(profile_hash) != 64 or any(
        character not in "0123456789abcdef" for character in profile_hash
    ):
        raise ValueError("profile_hash must be a lowercase SHA-256")
    if verify_hash:
        material = dict(row)
        material.pop("profile_hash", None)
        material.pop("delivery_status", None)
        if hashlib.sha256(canonical_json_bytes(material)).hexdigest() != profile_hash:
            raise ValueError("profile_hash does not match canonical profile content")


def load_questionnaire_contract(path: Path) -> dict[str, Any]:
    questionnaire = load_questionnaire(path)
    if len(questionnaire["feature_names"]) != 23:
        raise ValueError("temporal training contract requires exactly 23 features")
    return questionnaire


__all__ = [
    "TEMPORAL_DATASET_VERSION",
    "TEMPORAL_PROFILE_RULES_VERSION",
    "TEMPORAL_PROFILE_SCHEMA_VERSION",
    "TEMPORAL_PROFILE_STATE",
    "build_temporal_training_profile",
    "canonical_json_bytes",
    "load_jsonl",
    "load_questionnaire_contract",
    "sha256_file",
    "validate_temporal_training_profile",
    "verified_login_from_evidence",
]
