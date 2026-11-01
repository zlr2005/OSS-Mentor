"""Confirmed enhanced-profile snapshots and reproducible delivery manifests."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping

from .assessment import ENHANCED_FEATURE_EXPORT_SCHEMA_VERSION, _iso_utc
from .confirmation import validate_feature_state


PROFILE_DELIVERY_MANIFEST_SCHEMA_VERSION = "enhanced-profile-manifest-v0.1"
PROFILE_DELIVERY_VALIDATION_SCHEMA_VERSION = "enhanced-profile-validation-v0.1"


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def canonical_json_bytes(payload: Any) -> bytes:
    """Return stable UTF-8 bytes used by manifests and repeatability checks."""
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def sha256_payload(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def build_confirmed_feature_snapshot(
    profile_state: Mapping[str, Any],
    *,
    as_of: str,
    developer_key: str | None = None,
) -> dict[str, Any]:
    """Export C-facing arrays only from a validated, confirmed feature state."""
    validate_feature_state(profile_state)
    if developer_key is not None:
        developer_key = _text(developer_key, "developer_key")
    rows = list(profile_state["features"])
    interests = [
        row["feature_name"]
        for row in rows
        if row["feature_name"].startswith("current_interest.")
        and row["observed"]
        and float(row["value"]) > 0.0
    ]
    return {
        "schema_version": ENHANCED_FEATURE_EXPORT_SCHEMA_VERSION,
        "profile_state": "confirmed",
        "profile_key": profile_state["profile_key"],
        "developer_key": developer_key,
        "profile_version": profile_state["profile_version"],
        "as_of": _iso_utc(as_of, "as_of"),
        "feature_schema_version": profile_state["feature_schema_version"],
        "feature_names": [row["feature_name"] for row in rows],
        "values": [float(row["value"]) for row in rows],
        "observed_mask": [bool(row["observed"]) for row in rows],
        "locked_mask": [bool(row["locked"]) for row in rows],
        "current_interests": interests,
        "evidence_refs": [list(row["evidence_refs"]) for row in rows],
        "interpretation": "confirmed_profile_features_not_verified_ability_probabilities",
    }


def validate_confirmed_feature_snapshot(snapshot: Mapping[str, Any]) -> None:
    if snapshot.get("schema_version") != ENHANCED_FEATURE_EXPORT_SCHEMA_VERSION:
        raise ValueError("unsupported enhanced profile export schema_version")
    if snapshot.get("profile_state") != "confirmed":
        raise ValueError("only confirmed profile snapshots can be delivered")
    for field in ("profile_key", "profile_version", "feature_schema_version"):
        _text(snapshot.get(field), field)
    _iso_utc(snapshot.get("as_of"), "as_of")
    names = snapshot.get("feature_names")
    if (
        not isinstance(names, list)
        or not names
        or names != sorted(names)
        or len(names) != len(set(names))
    ):
        raise ValueError("feature_names must be unique and sorted")
    expected = len(names)
    for field in ("values", "observed_mask", "locked_mask", "evidence_refs"):
        value = snapshot.get(field)
        if not isinstance(value, list) or len(value) != expected:
            raise ValueError(f"{field} must align with feature_names")
    for index, observed in enumerate(snapshot["observed_mask"]):
        if not isinstance(observed, bool):
            raise ValueError("observed_mask entries must be boolean")
        if not observed and float(snapshot["values"][index]) != 0.0:
            raise ValueError("unknown features must retain a zero placeholder")
    if any(not isinstance(item, bool) for item in snapshot["locked_mask"]):
        raise ValueError("locked_mask entries must be boolean")


def build_profile_delivery_manifest(
    snapshots: Iterable[Mapping[str, Any]],
    *,
    snapshot_version: str,
    created_at: str,
    source_commit: str,
    input_files: Iterable[Mapping[str, str]],
    feature_payload_sha256: str,
    validation_payload_sha256: str,
) -> dict[str, Any]:
    """Describe a versioned snapshot without embedding machine-local paths."""
    rows = [dict(row) for row in snapshots]
    if not rows:
        raise ValueError("at least one confirmed snapshot is required")
    for row in rows:
        validate_confirmed_feature_snapshot(row)
    schemas = {row["feature_schema_version"] for row in rows}
    if len(schemas) != 1:
        raise ValueError("all delivered snapshots must use one feature schema")
    inputs: list[dict[str, str]] = []
    for index, item in enumerate(input_files):
        if not isinstance(item, Mapping):
            raise ValueError(f"input_files[{index}] must be an object")
        digest = _text(item.get("sha256"), f"input_files[{index}].sha256")
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest.lower()):
            raise ValueError(f"input_files[{index}].sha256 must be hexadecimal SHA-256")
        inputs.append(
            {
                "path": _text(item.get("path"), f"input_files[{index}].path"),
                "sha256": digest.lower(),
            }
        )
    profile_keys = [row["profile_key"] for row in rows]
    if len(profile_keys) != len(set(profile_keys)):
        raise ValueError("delivered profile_key values must be unique")
    return {
        "schema_version": PROFILE_DELIVERY_MANIFEST_SCHEMA_VERSION,
        "snapshot_version": _text(snapshot_version, "snapshot_version"),
        "created_at": _iso_utc(created_at, "created_at"),
        "source_commit": _text(source_commit, "source_commit"),
        "data_status": "synthetic_fixture",
        "profile_state": "confirmed",
        "feature_schema_version": next(iter(schemas)),
        "profile_count": len(rows),
        "feature_count": len(rows[0]["feature_names"]),
        "profile_keys": sorted(profile_keys),
        "input_files": sorted(inputs, key=lambda item: item["path"]),
        "artifacts": [
            {
                "path": "enhanced_profile_features.json",
                "sha256": _text(feature_payload_sha256, "feature_payload_sha256"),
            },
            {
                "path": f"../../validation/b/{snapshot_version}.json",
                "sha256": _text(validation_payload_sha256, "validation_payload_sha256"),
            },
        ],
        "limitations": [
            "The bundled profiles are synthetic fixtures, not research participants.",
            "Values are explainable proxy features, not precise ability probabilities.",
            "Real A contribution records and C encoder outputs are not included yet.",
            "The S0 public-contract and SQLite persistence adapters remain pending.",
        ],
    }


def build_delivery_validation(
    cases: Iterable[Mapping[str, Any]],
    snapshots: Iterable[Mapping[str, Any]],
    *,
    snapshot_version: str,
) -> dict[str, Any]:
    """Summarize missingness, conflicts and confirmation deltas for review."""
    case_rows = [dict(row) for row in cases]
    snapshot_rows = [dict(row) for row in snapshots]
    for row in snapshot_rows:
        validate_confirmed_feature_snapshot(row)
    return {
        "schema_version": PROFILE_DELIVERY_VALIDATION_SCHEMA_VERSION,
        "snapshot_version": _text(snapshot_version, "snapshot_version"),
        "checks": {
            "all_snapshots_confirmed": all(
                row.get("profile_state") == "confirmed" for row in snapshot_rows
            ),
            "aligned_feature_arrays": all(
                len(row["feature_names"])
                == len(row["values"])
                == len(row["observed_mask"])
                == len(row["locked_mask"])
                == len(row["evidence_refs"])
                for row in snapshot_rows
            ),
            "unknown_values_use_zero_placeholder": all(
                all(observed or float(row["values"][index]) == 0.0
                    for index, observed in enumerate(row["observed_mask"]))
                for row in snapshot_rows
            ),
            "profile_keys_unique": len({row["profile_key"] for row in snapshot_rows})
            == len(snapshot_rows),
        },
        "case_count": len(case_rows),
        "cases": case_rows,
        "feature_payload_sha256": sha256_payload(snapshot_rows),
    }
