"""Export synthetic B assessment examples; no database, network or login required."""

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
    export_enhanced_features,
    load_questionnaire,
)


QUESTIONNAIRE = ROOT / "config/midterm_profile/questionnaire_v0.1.json"
SCENARIOS = ROOT / "fixtures/midterm/b/assessment_scenarios_v0.1.json"


def build_handoff() -> dict:
    """Use fixed fixture timestamps and identifiers for reproducible output."""
    questionnaire = load_questionnaire(QUESTIONNAIRE)
    scenarios = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    cases = []
    for scenario in scenarios["scenarios"]:
        preview = build_assessment_preview(
            questionnaire,
            scenario["submission"],
            external_evidence=scenario["external_evidence"],
        )
        features = export_enhanced_features(
            preview,
            profile_key=f"fixture:{scenario['id']}",
            profile_version="fixture-preview-v0.1",
            as_of=scenario["submission"]["submitted_at"],
            developer_key=None,
        )
        cases.append(
            {
                "scenario_id": scenario["id"],
                "description": scenario["description"],
                "input": {
                    "submission": scenario["submission"],
                    "external_evidence": scenario["external_evidence"],
                },
                "preview": preview,
                "feature_export": features,
                "summary": {
                    "feature_count": len(features["feature_names"]),
                    "unknown_features": [
                        row["feature_name"] for row in preview["features"]
                        if not row["observed"]
                    ],
                    "conflicting_features": [
                        row["feature_name"] for row in preview["features"]
                        if row["conflict"]
                    ],
                    "locked_features": [
                        row["feature_name"] for row in preview["features"]
                        if row["blocked_reason"] == "field_locked"
                    ],
                },
            }
        )
    return {
        "bundle_format": "b-assessment-handoff-v0.1",
        "data_status": "synthetic_fixture",
        "profile_state": "preview_only_not_confirmed",
        "shared_contract_status": "pending_s0_adapter",
        "limitations": [
            "No persisted answers, confirmation, HTTP API or DeveloperProfileV2 update.",
            "External evidence is synthetic; no A contribution or C encoder integration.",
            "Values are uncalibrated proxy features, not verified ability or probabilities.",
            "Fixed timestamps are fixture context, not historical evaluation evidence.",
        ],
        "input_files": [
            {
                "path": path.relative_to(ROOT).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in (QUESTIONNAIRE, SCENARIOS)
        ],
        "questionnaire": questionnaire,
        "scenario_count": len(cases),
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path,
        help="New UTF-8 JSON file; existing files are never overwritten. Default: stdout.",
    )
    args = parser.parse_args()
    bundle = build_handoff()
    payload = json.dumps(bundle, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
        sys.stdout.write(payload)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        try:
            with args.output.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
        except FileExistsError:
            parser.error(f"output already exists: {args.output}; choose a new filename")
        print(json.dumps({
            "output": str(args.output.resolve()),
            "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            "scenario_count": bundle["scenario_count"],
            "data_status": "synthetic_fixture",
        }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
