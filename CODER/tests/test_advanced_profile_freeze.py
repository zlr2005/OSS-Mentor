from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from coder_issue.models.profile import PROFILE_FEATURE_NAMES
from coder_issue.training.advanced_profile_freeze import freeze


def _canonical(payload: object) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_freeze_promotes_validated_candidate(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    row = {
        "schema_version": "enhanced-profile-training-export-v0.1",
        "profile_state": "research_frozen",
        "delivery_status": "research_frozen_candidate",
        "profile_key": "research-profile:fixture",
        "profile_version": "research-profile-version:fixture",
        "developer_key": "developer",
        "as_of": "2025-01-01T00:00:00Z",
        "feature_names": list(PROFILE_FEATURE_NAMES),
        "values": [0.0] * len(PROFILE_FEATURE_NAMES),
        "observed_mask": [False] * len(PROFILE_FEATURE_NAMES),
        "locked_mask": [False] * len(PROFILE_FEATURE_NAMES),
        "current_interests": [],
        "evidence_refs": [[] for _ in PROFILE_FEATURE_NAMES],
    }
    hash_material = dict(row)
    hash_material.pop("delivery_status")
    row["profile_hash"] = hashlib.sha256(_canonical(hash_material)).hexdigest()
    profiles = candidate / "profiles.jsonl"
    profiles.write_bytes(_canonical(row) + b"\n")
    for name in ("profile_audit.jsonl", "profile_failures.jsonl"):
        (candidate / name).write_text("", encoding="utf-8")
    (candidate / "profile_manifest.json").write_text(
        json.dumps(
            {
                "delivery_status": "research_frozen_candidate",
                "training_allowed": False,
                "profiles": {"valid": 1},
                "limitations": ["This is a schema-integration preview and must not be used for training."],
            }
        ),
        encoding="utf-8",
    )
    validation = tmp_path / "validation.json"
    validation.write_text(
        json.dumps(
            {
                "status": "passed",
                "records": {"invalid": 0},
                "input": {"sha256": _sha(profiles)},
            }
        ),
        encoding="utf-8",
    )
    audit = tmp_path / "audit.json"
    audit.write_text(
        json.dumps(
            {
                "schema_version": "advanced-adapter-audit-v0.1",
                "candidate_embedding_missing": 0,
                "candidate_content_mismatch": 0,
                "split_pairs": {"train": 1, "validation": 1, "test": 1},
                "eligible_outcomes": 3,
                "pairs": 3,
                "failures": 0,
            }
        ),
        encoding="utf-8",
    )
    audit_failures = tmp_path / "audit_failures.jsonl"
    audit_failures.write_text("", encoding="utf-8")

    output = tmp_path / "frozen"
    result = freeze(
        argparse.Namespace(
            candidate_dir=candidate,
            validation_report=validation,
            audit_report=audit,
            audit_failures=audit_failures,
            output_dir=output,
        )
    )

    frozen = json.loads((output / "profiles.jsonl").read_text(encoding="utf-8"))
    manifest = json.loads((output / "profile_manifest.json").read_text(encoding="utf-8"))
    assert result["training_allowed"] is True
    assert frozen["delivery_status"] == "training_frozen"
    assert frozen["profile_hash"] == row["profile_hash"]
    assert manifest["training_allowed"] is True
    assert (output / "_SUCCESS").read_text(encoding="ascii").strip() == _sha(
        output / "profile_manifest.json"
    )
