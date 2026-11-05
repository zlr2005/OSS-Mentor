"""Promote validated temporal profiles to a training-approved frozen bundle."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any, Iterable, Mapping, Sequence


FROZEN_DELIVERY_STATUS = "training_frozen"


def _canonical_json(payload: Any) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _rows(path: Path) -> Iterable[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: row must be an object")
            yield row


def _verify_profile_hash(row: Mapping[str, Any]) -> None:
    profile_hash = row.get("profile_hash")
    if not isinstance(profile_hash, str) or len(profile_hash) != 64:
        raise ValueError("profile_hash must be a SHA-256")
    material = dict(row)
    material.pop("profile_hash", None)
    material.pop("delivery_status", None)
    if hashlib.sha256(_canonical_json(material)).hexdigest() != profile_hash:
        raise ValueError(f"profile_hash mismatch: {row.get('profile_version')}")


def freeze(args: argparse.Namespace) -> dict[str, Any]:
    candidate_dir = Path(args.candidate_dir)
    validation_report_path = Path(args.validation_report)
    audit_report_path = Path(args.audit_report)
    audit_failures_path = Path(args.audit_failures)
    output_dir = Path(args.output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory must be absent or empty: {output_dir}")

    candidate_manifest_path = candidate_dir / "profile_manifest.json"
    candidate_profiles_path = candidate_dir / "profiles.jsonl"
    candidate_manifest = _json(candidate_manifest_path)
    validation = _json(validation_report_path)
    audit = _json(audit_report_path)
    if candidate_manifest.get("training_allowed") is not False:
        raise ValueError("source candidate manifest must not already allow training")
    if candidate_manifest.get("delivery_status") != "research_frozen_candidate":
        raise ValueError("source delivery_status must be research_frozen_candidate")
    if validation.get("status") != "passed" or validation.get("records", {}).get("invalid") != 0:
        raise ValueError("standalone profile validation must pass without invalid rows")
    if validation.get("input", {}).get("sha256") != _sha256(candidate_profiles_path):
        raise ValueError("validation report does not refer to the candidate profiles")
    if audit.get("schema_version") != "advanced-adapter-audit-v0.1":
        raise ValueError("unsupported adapter audit report")
    if audit.get("candidate_embedding_missing") != 0 or audit.get("candidate_content_mismatch") != 0:
        raise ValueError("adapter audit contains candidate cache failures")
    split_pairs = audit.get("split_pairs")
    if not isinstance(split_pairs, dict) or any(
        int(split_pairs.get(split, 0)) <= 0 for split in ("train", "validation", "test")
    ):
        raise ValueError("adapter audit must contain non-empty train/validation/test pairs")

    output_dir.mkdir(parents=True, exist_ok=True)
    profiles_path = output_dir / "profiles.jsonl"
    count = 0
    with profiles_path.open("wb") as handle:
        for row in _rows(candidate_profiles_path):
            _verify_profile_hash(row)
            row["delivery_status"] = FROZEN_DELIVERY_STATUS
            handle.write(_canonical_json(row) + b"\n")
            count += 1
    expected = int(candidate_manifest.get("profiles", {}).get("valid", -1))
    if count != expected:
        raise ValueError(f"profile count mismatch: expected {expected}, wrote {count}")

    for source_name, target_name in (
        ("profile_audit.jsonl", "profile_audit.jsonl"),
        ("profile_failures.jsonl", "profile_failures.jsonl"),
    ):
        shutil.copyfile(candidate_dir / source_name, output_dir / target_name)
    shutil.copyfile(validation_report_path, output_dir / "profile_validation_report.json")
    shutil.copyfile(audit_report_path, output_dir / "adapter_audit_report.json")
    shutil.copyfile(audit_failures_path, output_dir / "adapter_audit_failures.jsonl")

    manifest = dict(candidate_manifest)
    manifest.update(
        {
            "delivery_status": FROZEN_DELIVERY_STATUS,
            "training_allowed": True,
            "frozen_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "promotion": {
                "source_candidate_manifest_sha256": _sha256(candidate_manifest_path),
                "profile_validation_report_sha256": _sha256(validation_report_path),
                "adapter_audit_report_sha256": _sha256(audit_report_path),
                "adapter_audit_failures_sha256": _sha256(audit_failures_path),
                "eligible_outcomes": int(audit["eligible_outcomes"]),
                "training_pairs": int(audit["pairs"]),
                "audit_failures": int(audit["failures"]),
            },
            "artifacts": {
                "profiles.jsonl": _sha256(profiles_path),
                "profile_audit.jsonl": _sha256(output_dir / "profile_audit.jsonl"),
                "profile_failures.jsonl": _sha256(output_dir / "profile_failures.jsonl"),
                "profile_validation_report.json": _sha256(
                    output_dir / "profile_validation_report.json"
                ),
                "adapter_audit_report.json": _sha256(output_dir / "adapter_audit_report.json"),
                "adapter_audit_failures.jsonl": _sha256(
                    output_dir / "adapter_audit_failures.jsonl"
                ),
            },
            "limitations": [
                item
                for item in candidate_manifest.get("limitations", [])
                if "schema-integration preview" not in str(item)
            ],
        }
    )
    manifest_path = output_dir / "profile_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    success = output_dir / "_SUCCESS"
    success.write_text(_sha256(manifest_path) + "\n", encoding="ascii")
    return {
        "profiles": count,
        "profiles_sha256": manifest["artifacts"]["profiles.jsonl"],
        "manifest_sha256": _sha256(manifest_path),
        "training_allowed": True,
        "output_dir": str(output_dir),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-dir", required=True)
    parser.add_argument("--validation-report", required=True)
    parser.add_argument("--audit-report", required=True)
    parser.add_argument("--audit-failures", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    result = freeze(build_parser().parse_args(argv))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
