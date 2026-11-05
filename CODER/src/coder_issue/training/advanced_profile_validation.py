"""Standalone validation command for B's advanced temporal profile export."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from statistics import mean
from typing import Any, Sequence

from coder_issue.models.profile import PROFILE_FEATURE_NAMES, ProfileAdapterConfig
from coder_issue.training.advanced_profile_data import DataFailure, load_temporal_profiles


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_profile_export(path: Path) -> tuple[dict[str, Any], list[DataFailure]]:
    """Validate one JSONL/Parquet export without A/C data or model artifacts."""

    source = Path(path)
    try:
        profiles, failures = load_temporal_profiles(
            source,
            adapter_config=ProfileAdapterConfig(),
        )
    except (OSError, ValueError) as exc:
        profiles = []
        failures = [DataFailure(str(source), "file", "invalid_profile_file", str(exc))]

    observed_counts = [int(profile.tensors.observed_mask.sum().item()) for profile in profiles]
    feature_counts = Counter()
    for profile in profiles:
        for index, observed in enumerate(profile.tensors.observed_mask.tolist()):
            if observed:
                feature_counts[PROFILE_FEATURE_NAMES[index]] += 1
    developer_counts = Counter(profile.developer_key for profile in profiles)
    hash_counts = Counter(profile.profile_hash for profile in profiles)
    timestamps = [profile.as_of for profile in profiles]
    total = len(profiles) + len(failures)
    report: dict[str, Any] = {
        "schema_version": "advanced-profile-validation-report-v0.1",
        "status": "passed" if not failures else "failed",
        "input": {
            "path": str(source),
            "size_bytes": source.stat().st_size if source.is_file() else None,
            "sha256": _sha256(source) if source.is_file() else None,
        },
        "records": {
            "total": total,
            "valid": len(profiles),
            "invalid": len(failures),
            "developers": len(developer_counts),
            "profile_keys": len({profile.profile_key for profile in profiles}),
            "duplicate_profile_hashes": sum(count - 1 for count in hash_counts.values() if count > 1),
            "all_unobserved_profiles": sum(count == 0 for count in observed_counts),
        },
        "time_coverage": {
            "earliest_as_of": min(timestamps).isoformat() if timestamps else None,
            "latest_as_of": max(timestamps).isoformat() if timestamps else None,
        },
        "observed_dimensions_per_profile": {
            "minimum": min(observed_counts) if observed_counts else None,
            "maximum": max(observed_counts) if observed_counts else None,
            "mean": mean(observed_counts) if observed_counts else None,
        },
        "feature_coverage": [
            {
                "feature_name": feature,
                "observed_profiles": feature_counts[feature],
                "observed_rate": feature_counts[feature] / len(profiles) if profiles else 0.0,
            }
            for feature in PROFILE_FEATURE_NAMES
        ],
        "profiles_per_developer": {
            "minimum": min(developer_counts.values()) if developer_counts else None,
            "maximum": max(developer_counts.values()) if developer_counts else None,
            "mean": mean(developer_counts.values()) if developer_counts else None,
        },
        "failure_codes": dict(sorted(Counter(item.code for item in failures).items())),
    }
    return report, failures


def write_validation_results(
    output_dir: Path,
    *,
    report: dict[str, Any],
    failures: Sequence[DataFailure],
) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "profile_validation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with (output_dir / "profile_failures.jsonl").open("w", encoding="utf-8") as handle:
        for failure in failures:
            handle.write(json.dumps(failure.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", required=True, help="B profile export (.jsonl or .parquet)")
    parser.add_argument("--output-dir", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report, failures = validate_profile_export(Path(args.profiles))
    write_validation_results(Path(args.output_dir), report=report, failures=failures)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
