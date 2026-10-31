"""Conservative interpretation of GitHub contribution records.

The input mappings mirror the A-to-B fields in the midterm work plan without
claiming to be the shared S0 contract.  This module turns attributable,
time-valid contribution records into the small evidence rows consumed by the
assessment preview.  Counts never increase an ability value: every conclusion
remains traceable to an individual source record.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping

from .assessment import _iso_utc


GITHUB_EVIDENCE_BATCH_SCHEMA_VERSION = "github-profile-evidence-batch-v0.1"

_CONFIRMED_IDENTITIES = {"confirmed", "verified"}
_COLLECTION_STATUSES = {"complete", "partial", "failed"}
_DIRECT_BEHAVIORS = {"commit", "pull_request", "pull_request_commit"}
_KNOWN_BEHAVIORS = _DIRECT_BEHAVIORS | {"review"}
_SOURCE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cs", ".go", ".java", ".js", ".jsx",
    ".kt", ".php", ".py", ".rb", ".rs", ".scala", ".swift",
    ".ts", ".tsx",
}
_DOCUMENTATION_SUFFIXES = {".md", ".mdx", ".rst", ".adoc"}


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _canonical_digest(payload: Any, prefix: str) -> str:
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"{prefix}{hashlib.sha256(raw).hexdigest()[:24]}"


def _classify_path(path: str) -> str:
    normalized = path.replace("\\", "/").lower().strip("/")
    parts = PurePosixPath(normalized).parts
    suffix = PurePosixPath(normalized).suffix
    if (
        "docs" in parts
        or "documentation" in parts
        or suffix in _DOCUMENTATION_SUFFIXES
        or normalized.startswith("readme")
    ):
        return "documentation"
    if (
        any(part in {"test", "tests", "testing", "spec", "specs"} for part in parts)
        or any(part.startswith("test_") or part.endswith("_test.py") for part in parts)
    ):
        return "test"
    if suffix in _SOURCE_SUFFIXES:
        return "source"
    return "other"


def _feature_rules(path_kind: str, behavior_type: str) -> list[tuple[str, float, float, str]]:
    rules: list[tuple[str, float, float, str]] = [
        (
            "capability.open_source_experience",
            0.75,
            0.78,
            "confirmed public contribution or review",
        )
    ]
    if behavior_type not in _DIRECT_BEHAVIORS:
        return rules
    if path_kind == "documentation":
        rules.append(
            ("capability.documentation", 0.5, 0.72, "attributed documentation change")
        )
    elif path_kind == "test":
        rules.extend(
            [
                ("capability.testing", 0.5, 0.72, "attributed test-file change"),
                (
                    "capability.test_design",
                    0.25,
                    0.58,
                    "test-file change alone does not prove independent test design",
                ),
            ]
        )
    elif path_kind == "source":
        rules.append(
            ("capability.code_change", 0.5, 0.7, "attributed source-code change")
        )
    return rules


def interpret_github_contributions(
    contribution_records: Iterable[Mapping[str, Any]],
    code_documents: Iterable[Mapping[str, Any]],
    *,
    developer_key: str,
    observed_at: str,
    as_of: str,
    collection_status: str = "complete",
    collection_limitations: Iterable[str] = (),
) -> dict[str, Any]:
    """Return deterministic evidence and an audit row for every input record.

    Malformed records raise ``ValueError`` because they violate the adapter
    boundary.  Well-formed but unusable records are retained with an exclusion
    reason, so partial collection and unresolved identities remain visible.
    """
    developer = _text(developer_key, "developer_key")
    normalized_observed_at = _iso_utc(observed_at, "observed_at")
    normalized_as_of = _iso_utc(as_of, "as_of")
    if collection_status not in _COLLECTION_STATUSES:
        raise ValueError("collection_status must be complete, partial, or failed")
    limitations = []
    for index, item in enumerate(collection_limitations):
        limitations.append(_text(item, f"collection_limitations[{index}]"))
    limitations = list(dict.fromkeys(limitations))

    documents: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(code_documents):
        if not isinstance(raw, Mapping):
            raise ValueError(f"code_documents[{index}] must be an object")
        file_version_key = _text(
            raw.get("file_version_key"), f"code_documents[{index}].file_version_key"
        )
        if file_version_key in documents:
            raise ValueError(f"duplicate code document: {file_version_key}")
        path = raw.get("path", raw.get("file_path"))
        normalized = {
            "file_version_key": file_version_key,
            "repository_key": _text(
                raw.get("repository_key"), f"code_documents[{index}].repository_key"
            ),
            "path": _text(path, f"code_documents[{index}].path"),
            "available": raw.get("available", True),
            "exclusion_reason": raw.get("exclusion_reason"),
        }
        if not isinstance(normalized["available"], bool):
            raise ValueError(f"code_documents[{index}].available must be boolean")
        documents[file_version_key] = normalized

    source_records = copy.deepcopy(list(contribution_records))
    audit_by_identity: dict[str, dict[str, Any]] = {}
    evidence: list[dict[str, Any]] = []
    for index, raw in enumerate(source_records):
        if not isinstance(raw, Mapping):
            raise ValueError(f"contribution_records[{index}] must be an object")
        record_developer = _text(
            raw.get("developer_key"), f"contribution_records[{index}].developer_key"
        )
        if record_developer != developer:
            raise ValueError(f"contribution_records[{index}] belongs to another developer")
        repository_key = _text(
            raw.get("repository_key"), f"contribution_records[{index}].repository_key"
        )
        file_key = _text(raw.get("file_key"), f"contribution_records[{index}].file_key")
        file_version_key = _text(
            raw.get("file_version_key"),
            f"contribution_records[{index}].file_version_key",
        )
        commit_sha = _text(
            raw.get("commit_sha"), f"contribution_records[{index}].commit_sha"
        )
        behavior_type = _text(
            raw.get("behavior_type"), f"contribution_records[{index}].behavior_type"
        )
        if behavior_type not in _KNOWN_BEHAVIORS:
            raise ValueError(f"unsupported behavior_type: {behavior_type}")
        identity_status = _text(
            raw.get("author_link_status", raw.get("identity_status")),
            f"contribution_records[{index}].author_link_status",
        )
        event_at = _iso_utc(
            raw.get("event_at"), f"contribution_records[{index}].event_at"
        )
        record_observed_at = _iso_utc(
            raw.get("observed_at", normalized_observed_at),
            f"contribution_records[{index}].observed_at",
        )
        evidence_location = _text(
            raw.get("evidence_location"),
            f"contribution_records[{index}].evidence_location",
        )
        identity_material = {
            "developer_key": developer,
            "repository_key": repository_key,
            "file_version_key": file_version_key,
            "commit_sha": commit_sha,
            "behavior_type": behavior_type,
        }
        record_id = _canonical_digest(identity_material, "contribution:")
        if record_id in audit_by_identity:
            audit_by_identity[record_id]["duplicate_count"] += 1
            continue

        document = documents.get(file_version_key)
        exclusion_reason = None
        path_kind = None
        if identity_status not in _CONFIRMED_IDENTITIES:
            exclusion_reason = "author_identity_not_confirmed"
        elif event_at > normalized_as_of:
            exclusion_reason = "event_after_profile_as_of"
        elif event_at > record_observed_at:
            exclusion_reason = "event_after_collection_observed_at"
        elif document is None:
            exclusion_reason = "code_document_missing"
        elif document["repository_key"] != repository_key:
            exclusion_reason = "repository_document_mismatch"
        elif not document["available"]:
            exclusion_reason = str(document["exclusion_reason"] or "code_document_unavailable")
        else:
            path_kind = _classify_path(document["path"])

        audit = {
            "record_id": record_id,
            **identity_material,
            "file_key": file_key,
            "event_at": event_at,
            "observed_at": record_observed_at,
            "identity_status": identity_status,
            "evidence_location": evidence_location,
            "path_kind": path_kind,
            "status": "excluded" if exclusion_reason else "interpreted",
            "exclusion_reason": exclusion_reason,
            "duplicate_count": 0,
            "generated_evidence_refs": [],
        }
        audit_by_identity[record_id] = audit
        if exclusion_reason:
            continue

        assert document is not None and path_kind is not None
        for feature_name, value, confidence, basis in _feature_rules(
            path_kind, behavior_type
        ):
            evidence_ref = _canonical_digest(
                {"record_id": record_id, "feature_name": feature_name}, "github:"
            )
            audit["generated_evidence_refs"].append(evidence_ref)
            evidence.append(
                {
                    "feature_name": feature_name,
                    "value": value,
                    "confidence": confidence,
                    "source_kind": "github_direct",
                    "evidence_ref": evidence_ref,
                    "event_at": event_at,
                    "observed_at": record_observed_at,
                    "repository_key": repository_key,
                    "file_key": file_key,
                    "file_version_key": file_version_key,
                    "commit_sha": commit_sha,
                    "behavior_type": behavior_type,
                    "evidence_location": evidence_location,
                    "interpretation_basis": basis,
                }
            )

    audits = sorted(audit_by_identity.values(), key=lambda row: row["record_id"])
    evidence.sort(key=lambda row: (row["feature_name"], row["evidence_ref"]))
    exclusion_counts: dict[str, int] = {}
    for row in audits:
        reason = row["exclusion_reason"]
        if reason:
            exclusion_counts[reason] = exclusion_counts.get(reason, 0) + 1
    return {
        "schema_version": GITHUB_EVIDENCE_BATCH_SCHEMA_VERSION,
        "developer_key": developer,
        "as_of": normalized_as_of,
        "observed_at": normalized_observed_at,
        "coverage": {
            "status": collection_status,
            "limitations": limitations,
            "input_record_count": len(source_records),
            "unique_record_count": len(audits),
            "interpreted_record_count": sum(row["status"] == "interpreted" for row in audits),
            "excluded_record_count": sum(row["status"] == "excluded" for row in audits),
            "exclusion_counts": dict(sorted(exclusion_counts.items())),
        },
        "records": audits,
        "evidence": evidence,
        "interpretation": (
            "traceable_contribution_evidence_not_commit_count_or_verified_ability"
        ),
    }
