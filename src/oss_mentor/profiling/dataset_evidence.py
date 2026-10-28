"""Adapt A-owned midterm dataset snapshots into frozen profile evidence.

The adapter is deliberately conservative.  It consumes only datasets whose
import completed, keeps fixture provenance visible, excludes unconfirmed
identities and unavailable code, and treats repository behaviours as weak
interest rather than ability evidence.
"""

from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from oss_mentor.contracts_midterm import (
    EVIDENCE_SOURCE_GITHUB_WEAK,
    ProfileEvidence,
)
from oss_mentor.datasets.snapshot import load_snapshot

from .assessment import _iso_utc
from .evidence import _canonical_digest, interpret_github_contributions


DATASET_PROFILE_EVIDENCE_SCHEMA_VERSION = "dataset-profile-evidence-adapter-v0.1"

_RECORD_TABLES = {
    "repositories": "midterm_dataset_repository",
    "developers": "midterm_dataset_developer",
    "files": "midterm_dataset_file",
    "code_documents": "midterm_dataset_code",
    "contributions": "midterm_dataset_contribution",
    "project_behaviors": "midterm_dataset_behavior",
    "issues": "midterm_dataset_issue",
    "issue_file_links": "midterm_dataset_issue_file",
    "file_aliases": "midterm_dataset_file_alias",
}

_CONTRIBUTION_BEHAVIORS = {
    "commit_author": "commit",
    "commit": "commit",
    "pull_request": "pull_request",
    "pull_request_commit": "pull_request_commit",
    "review": "review",
}

_LANGUAGE_FEATURES = {
    "go": "preference.language.go",
    "java": "preference.language.java",
    "javascript": "preference.language.javascript",
    "python": "preference.language.python",
    "rust": "preference.language.rust",
    "typescript": "preference.language.typescript",
}

_WEAK_BEHAVIOUR_STRENGTH = {
    "star": (0.25, 0.20),
    "fork": (0.35, 0.25),
    "owned_repository": (0.50, 0.35),
}


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _dataset_limitations(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    """Return explicit collection gaps without inventing unavailable evidence."""
    result: list[str] = []
    for field in ("quality_gaps", "not_delivered"):
        raw = manifest.get(field, ())
        if raw is None:
            continue
        if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
            raise ValueError(f"manifest.{field} must be an array of strings")
        for index, value in enumerate(raw):
            limitation = _text(value, f"manifest.{field}[{index}]")
            if limitation not in result:
                result.append(limitation)
    return tuple(result)


def _load_completed_dataset(
    connection: sqlite3.Connection,
    dataset_version: str,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    version = _text(dataset_version, "dataset_version")
    row = connection.execute(
        "SELECT status FROM midterm_dataset_import WHERE dataset_version = ?",
        (version,),
    ).fetchone()
    if row is None:
        raise ValueError(f"dataset is not imported: {version}")
    if row[0] != "completed":
        raise ValueError(f"dataset import must be completed: {version}")

    snapshot = connection.execute(
        "SELECT manifest_json FROM midterm_dataset_snapshot WHERE dataset_version = ?",
        (version,),
    ).fetchone()
    if snapshot is None:
        raise ValueError(f"dataset manifest is missing: {version}")
    manifest = json.loads(snapshot[0])

    records: dict[str, list[dict[str, Any]]] = {}
    for kind, table in _RECORD_TABLES.items():
        rows = connection.execute(
            f"SELECT payload_json FROM {table} "
            "WHERE dataset_version = ? ORDER BY ordinal",
            (version,),
        ).fetchall()
        records[kind] = [json.loads(item[0]) for item in rows]
    return manifest, records


def _normalize_documents(records: Mapping[str, Any]) -> list[dict[str, Any]]:
    documents = []
    for index, raw in enumerate(records.get("code_documents", ())):
        availability = _text(
            raw.get("availability"), f"code_documents[{index}].availability"
        )
        if availability not in {"available", "missing"}:
            raise ValueError(f"unsupported code availability: {availability}")
        documents.append(
            {
                "file_version_key": raw.get("file_version_key"),
                "repository_key": raw.get("repository_key"),
                "path": raw.get("path"),
                "available": availability == "available",
                "exclusion_reason": raw.get("exclusion_reason"),
            }
        )
    return documents


def _normalize_contributions(
    records: Mapping[str, Any], developer_key: str
) -> list[dict[str, Any]]:
    normalized = []
    for index, raw in enumerate(records.get("contributions", ())):
        if raw.get("developer_key") != developer_key:
            continue
        behaviour = _text(
            raw.get("behavior_type"), f"contributions[{index}].behavior_type"
        )
        try:
            mapped_behaviour = _CONTRIBUTION_BEHAVIORS[behaviour]
        except KeyError as error:
            raise ValueError(f"unsupported contribution behavior_type: {behaviour}") from error
        normalized.append(
            {
                "developer_key": developer_key,
                "repository_key": raw.get("repository_key"),
                "file_key": raw.get("file_key"),
                "file_version_key": raw.get("file_version_key"),
                "commit_sha": raw.get("commit_sha"),
                "event_at": raw.get("event_at"),
                "observed_at": raw.get("observed_at"),
                "behavior_type": mapped_behaviour,
                "author_link_status": raw.get("author_association_status"),
                "evidence_location": raw.get("evidence_location"),
            }
        )
    return normalized


def _aliases_by_file(records: Mapping[str, Any]) -> dict[str, tuple[dict[str, Any], ...]]:
    aliases: dict[str, list[dict[str, Any]]] = {}
    for row in records.get("file_aliases", ()):
        aliases.setdefault(_text(row.get("file_key"), "file_alias.file_key"), []).append(
            copy.deepcopy(dict(row))
        )
    return {
        key: tuple(sorted(rows, key=lambda item: (item["available_at"], item["old_path"])))
        for key, rows in aliases.items()
    }


def _contribution_boundary_flags(
    audit: dict[str, Any], documents: Mapping[str, Mapping[str, Any]]
) -> tuple[str, ...]:
    flags: list[str] = []
    if audit["identity_status"] not in {"confirmed", "verified"}:
        flags.append("author_identity_not_confirmed")
    document = documents.get(audit["file_version_key"])
    if document is None:
        flags.append("code_document_missing")
    elif document.get("availability") != "available":
        flags.append(str(document.get("exclusion_reason") or "code_document_unavailable"))
    return tuple(flags)


def _direct_profile_evidence(
    contribution_batch: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    records: Mapping[str, Any],
) -> tuple[ProfileEvidence, ...]:
    aliases = _aliases_by_file(records)
    result = []
    core_fields = {
        "feature_name",
        "value",
        "confidence",
        "source_kind",
        "evidence_ref",
        "observed_at",
    }
    for row in contribution_batch["evidence"]:
        details = {key: copy.deepcopy(value) for key, value in row.items() if key not in core_fields}
        details.update(
            {
                "dataset_version": manifest["dataset_version"],
                "fixture_only": manifest["fixture_only"],
                "file_aliases": copy.deepcopy(list(aliases.get(row["file_key"], ()))),
            }
        )
        result.append(
            ProfileEvidence(
                feature_name=row["feature_name"],
                value=row["value"],
                confidence=row["confidence"],
                source_kind=row["source_kind"],
                evidence_ref=row["evidence_ref"],
                observed_at=row["observed_at"],
                details=details,
            )
        )
    return tuple(result)


def _weak_behaviour_evidence(
    manifest: Mapping[str, Any],
    records: Mapping[str, Any],
    *,
    developer: Mapping[str, Any],
    developer_key: str,
    as_of: str,
) -> tuple[tuple[ProfileEvidence, ...], tuple[dict[str, Any], ...]]:
    repositories = {
        row["repository_key"]: row for row in records.get("repositories", ())
    }
    evidence: list[ProfileEvidence] = []
    audit_by_ref: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(records.get("project_behaviors", ())):
        if raw.get("developer_key") != developer_key:
            continue
        repository_key = _text(
            raw.get("repository_key"), f"project_behaviors[{index}].repository_key"
        )
        behaviour = _text(
            raw.get("behavior_type"), f"project_behaviors[{index}].behavior_type"
        )
        observed_at = _iso_utc(
            raw.get("observed_at"), f"project_behaviors[{index}].observed_at"
        )
        event_at = raw.get("event_at")
        normalized_event_at = (
            _iso_utc(event_at, f"project_behaviors[{index}].event_at")
            if event_at is not None
            else None
        )
        material = {
            "dataset_version": manifest["dataset_version"],
            "developer_key": developer_key,
            "repository_key": repository_key,
            "behavior_type": behaviour,
            "event_at": normalized_event_at,
            "observed_at": observed_at,
        }
        evidence_ref = _canonical_digest(material, "github:")
        if evidence_ref in audit_by_ref:
            audit_by_ref[evidence_ref]["duplicate_count"] += 1
            continue

        reason = None
        repository = repositories.get(repository_key)
        feature_name = None
        if developer.get("identity_status") not in {"confirmed", "verified"}:
            reason = "developer_identity_not_confirmed"
        elif behaviour not in _WEAK_BEHAVIOUR_STRENGTH:
            reason = "unsupported_weak_behavior"
        elif repository is None:
            reason = "repository_missing"
        else:
            language = str(repository.get("primary_language") or "").casefold()
            feature_name = _LANGUAGE_FEATURES.get(language)
            if feature_name is None:
                reason = "repository_language_not_supported"
        if normalized_event_at is not None and normalized_event_at > as_of:
            reason = "event_after_profile_as_of"

        audit = {
            **material,
            "evidence_ref": evidence_ref,
            "feature_name": feature_name,
            "event_time_unknown": bool(raw.get("event_time_unknown")),
            "status": "excluded" if reason else "interpreted",
            "exclusion_reason": reason,
            "duplicate_count": 0,
        }
        audit_by_ref[evidence_ref] = audit
        if reason:
            continue

        value, confidence = _WEAK_BEHAVIOUR_STRENGTH[behaviour]
        evidence.append(
            ProfileEvidence(
                feature_name=feature_name,
                value=value,
                confidence=confidence,
                source_kind=EVIDENCE_SOURCE_GITHUB_WEAK,
                evidence_ref=evidence_ref,
                observed_at=observed_at,
                details={
                    "dataset_version": manifest["dataset_version"],
                    "fixture_only": manifest["fixture_only"],
                    "developer_key": developer_key,
                    "repository_key": repository_key,
                    "behavior_type": behaviour,
                    "event_at": normalized_event_at,
                    "event_time_unknown": bool(raw.get("event_time_unknown")),
                    "recency_applied": False,
                    "interpretation_basis": (
                        "weak repository-language interest; not ability evidence"
                    ),
                },
            )
        )
    audits = tuple(sorted(audit_by_ref.values(), key=lambda item: item["evidence_ref"]))
    return tuple(evidence), audits


def _issue_file_audit(records: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    result = []
    for row in records.get("issue_file_links", ()):
        role = row.get("feature_role")
        result.append(
            {
                "issue_key": row.get("issue_key"),
                "file_key": row.get("file_key"),
                "feature_role": role,
                "status": "excluded",
                "exclusion_reason": (
                    "outcome_only_not_available_for_prediction"
                    if role == "outcome_only"
                    else "task_evidence_not_developer_attributed"
                ),
            }
        )
    return tuple(sorted(result, key=lambda item: (item["issue_key"], item["file_key"], item["feature_role"])))


def adapt_a_dataset_records_to_profile_evidence(
    manifest: Mapping[str, Any],
    records: Mapping[str, Any],
    *,
    developer_key: str,
    as_of: str,
    allow_fixture: bool = False,
) -> dict[str, Any]:
    """Convert one validated A dataset into auditable ``ProfileEvidence`` rows."""
    developer_id = _text(developer_key, "developer_key")
    normalized_as_of = _iso_utc(as_of, "as_of")
    fixture_only = manifest.get("fixture_only")
    if type(fixture_only) is not bool:
        raise ValueError("manifest.fixture_only must be a boolean")
    if fixture_only and not allow_fixture:
        raise ValueError("fixture dataset requires allow_fixture=True")
    dataset_version = _text(manifest.get("dataset_version"), "manifest.dataset_version")
    developers = {
        row["developer_key"]: row for row in records.get("developers", ())
    }
    try:
        developer = developers[developer_id]
    except KeyError as error:
        raise ValueError(f"developer is not present in dataset: {developer_id}") from error

    documents = _normalize_documents(records)
    normalized_contributions = _normalize_contributions(records, developer_id)
    limitations = _dataset_limitations(manifest)
    contribution_batch = interpret_github_contributions(
        normalized_contributions,
        documents,
        developer_key=developer_id,
        observed_at=manifest["time_coverage"]["end"],
        as_of=normalized_as_of,
        collection_status="complete",
        collection_limitations=limitations,
    )
    source_documents = {
        row["file_version_key"]: row for row in records.get("code_documents", ())
    }
    for audit in contribution_batch["records"]:
        audit["dataset_boundary_flags"] = list(
            _contribution_boundary_flags(audit, source_documents)
        )

    direct = _direct_profile_evidence(
        contribution_batch,
        manifest=manifest,
        records=records,
    )
    weak, behaviour_audit = _weak_behaviour_evidence(
        manifest,
        records,
        developer=developer,
        developer_key=developer_id,
        as_of=normalized_as_of,
    )
    evidence = tuple(sorted((*direct, *weak), key=lambda item: (item.feature_name, item.evidence_ref)))
    return {
        "schema_version": DATASET_PROFILE_EVIDENCE_SCHEMA_VERSION,
        "dataset_version": dataset_version,
        "fixture_only": fixture_only,
        "developer_key": developer_id,
        "as_of": normalized_as_of,
        "evidence": evidence,
        "contribution_audit": tuple(copy.deepcopy(contribution_batch["records"])),
        "project_behavior_audit": behaviour_audit,
        "issue_file_audit": _issue_file_audit(records),
        "coverage": {
            "direct_evidence_count": len(direct),
            "weak_evidence_count": len(weak),
            "excluded_contribution_count": contribution_batch["coverage"]["excluded_record_count"],
            "excluded_behavior_count": sum(row["status"] == "excluded" for row in behaviour_audit),
            "limitations": limitations,
        },
        "interpretation": (
            "fixture_safe_traceable_profile_evidence_not_verified_ability_or_model_quality"
            if fixture_only
            else "traceable_profile_evidence_not_verified_ability_or_model_quality"
        ),
    }


def build_profile_evidence_from_a_snapshot(
    snapshot_directory: str | Path,
    *,
    developer_key: str,
    as_of: str,
    allow_fixture: bool = False,
) -> dict[str, Any]:
    """Validate a portable A snapshot, then adapt it to profile evidence."""
    manifest, records, _ = load_snapshot(Path(snapshot_directory))
    return adapt_a_dataset_records_to_profile_evidence(
        manifest,
        records,
        developer_key=developer_key,
        as_of=as_of,
        allow_fixture=allow_fixture,
    )


def build_profile_evidence_from_a_dataset_store(
    connection: sqlite3.Connection,
    dataset_version: str,
    *,
    developer_key: str,
    as_of: str,
    allow_fixture: bool = False,
) -> dict[str, Any]:
    """Read a completed 011 import and adapt it to profile evidence."""
    manifest, records = _load_completed_dataset(connection, dataset_version)
    return adapt_a_dataset_records_to_profile_evidence(
        manifest,
        records,
        developer_key=developer_key,
        as_of=as_of,
        allow_fixture=allow_fixture,
    )


__all__ = [
    "DATASET_PROFILE_EVIDENCE_SCHEMA_VERSION",
    "adapt_a_dataset_records_to_profile_evidence",
    "build_profile_evidence_from_a_dataset_store",
    "build_profile_evidence_from_a_snapshot",
]
