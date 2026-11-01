"""Export confirmed synthetic profile features, manifest and validation record."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from oss_mentor.profiling.assessment import (  # noqa: E402
    build_assessment_preview,
    load_questionnaire,
)
from oss_mentor.profiling.confirmation import (  # noqa: E402
    build_confirmation_plan,
    build_feature_state,
    confirm_assessment_plan,
)
from oss_mentor.profiling.delivery import (  # noqa: E402
    build_confirmed_feature_snapshot,
    build_delivery_validation,
    build_profile_delivery_manifest,
    canonical_json_bytes,
    sha256_payload,
)


QUESTIONNAIRE = ROOT / "config/midterm_profile/questionnaire_v0.1.json"
SCENARIOS = ROOT / "fixtures/midterm/b/assessment_scenarios_v0.1.json"


def _input_file(path: Path) -> dict[str, str]:
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def build_delivery(
    *,
    snapshot_version: str,
    source_commit: str,
) -> tuple[list[dict], dict, dict]:
    """Build deterministic confirmed fixtures without persistence or network."""
    questionnaire = load_questionnaire(QUESTIONNAIRE)
    scenario_payload = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    snapshots: list[dict] = []
    case_results: list[dict] = []
    for scenario in scenario_payload["scenarios"]:
        submitted_at = scenario["submission"]["submitted_at"]
        preview = build_assessment_preview(
            questionnaire,
            scenario["submission"],
            external_evidence=scenario["external_evidence"],
        )
        state = build_feature_state(
            preview,
            profile_key=f"fixture:{scenario['id']}",
            profile_version=f"fixture-empty:{scenario['id']}",
            updated_at=submitted_at,
        )
        plan = build_confirmation_plan(preview, state)
        decisions = {
            row["suggestion_id"]: "accept"
            for row in plan["suggestions"]
            if row["status"] == "pending"
        }
        result = confirm_assessment_plan(
            plan,
            state,
            decisions,
            confirmed_at=submitted_at,
        )
        snapshot = build_confirmed_feature_snapshot(
            result["profile_state"],
            as_of=submitted_at,
            developer_key=None,
        )
        snapshots.append(snapshot)
        case_results.append(
            {
                "scenario_id": scenario["id"],
                "profile_key": snapshot["profile_key"],
                "base_profile_version": result["base_profile_version"],
                "confirmed_profile_version": result["new_profile_version"],
                "accepted_count": len(result["applied_suggestion_ids"]),
                "rejected_count": len(result["rejected_suggestion_ids"]),
                "remaining_count": len(result["remaining_suggestion_ids"]),
                "blocked_count": len(result["blocked_suggestion_ids"]),
                "conflict_count": sum(bool(row["conflict"]) for row in plan["suggestions"]),
                "unknown_before_count": sum(not row["observed"] for row in state["features"]),
                "unknown_after_count": sum(not item for item in snapshot["observed_mask"]),
            }
        )

    snapshots.sort(key=lambda row: row["profile_key"])
    case_results.sort(key=lambda row: row["scenario_id"])
    validation = build_delivery_validation(
        case_results,
        snapshots,
        snapshot_version=snapshot_version,
    )
    manifest = build_profile_delivery_manifest(
        snapshots,
        snapshot_version=snapshot_version,
        created_at="2026-10-21T00:00:00Z",
        source_commit=source_commit,
        input_files=[_input_file(QUESTIONNAIRE), _input_file(SCENARIOS)],
        feature_payload_sha256=sha256_payload(snapshots),
        validation_payload_sha256=sha256_payload(validation),
    )
    return snapshots, manifest, validation


def write_delivery(
    output_root: Path,
    *,
    snapshot_version: str,
    source_commit: str,
) -> dict[str, str]:
    profile_dir = output_root / "profiles" / snapshot_version
    validation_file = output_root / "validation" / "b" / f"{snapshot_version}.json"
    if profile_dir.exists():
        raise FileExistsError(f"profile output already exists: {profile_dir}")
    if validation_file.exists():
        raise FileExistsError(f"validation output already exists: {validation_file}")
    snapshots, manifest, validation = build_delivery(
        snapshot_version=snapshot_version,
        source_commit=source_commit,
    )
    profile_dir.mkdir(parents=True)
    validation_file.parent.mkdir(parents=True, exist_ok=True)
    feature_file = profile_dir / "enhanced_profile_features.json"
    manifest_file = profile_dir / "manifest.json"
    feature_file.write_bytes(canonical_json_bytes(snapshots))
    validation_file.write_bytes(canonical_json_bytes(validation))
    manifest_file.write_bytes(canonical_json_bytes(manifest))
    return {
        "profile_dir": str(profile_dir.resolve()),
        "validation_file": str(validation_file.resolve()),
        "feature_sha256": hashlib.sha256(feature_file.read_bytes()).hexdigest(),
        "manifest_sha256": hashlib.sha256(manifest_file.read_bytes()).hexdigest(),
        "validation_sha256": hashlib.sha256(validation_file.read_bytes()).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "data/midterm",
        help="Root containing profiles/ and validation/ (default: data/midterm).",
    )
    parser.add_argument("--snapshot-version", default="b-w1-fixture-v0.1")
    parser.add_argument(
        "--source-commit",
        required=True,
        help="Git commit that produced this delivery; no repository lookup is performed.",
    )
    args = parser.parse_args()
    try:
        result = write_delivery(
            args.output_root,
            snapshot_version=args.snapshot_version,
            source_commit=args.source_commit,
        )
    except FileExistsError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
