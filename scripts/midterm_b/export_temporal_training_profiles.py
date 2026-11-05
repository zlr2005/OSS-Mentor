"""Export time-safe B profiles for Advanced Adapter v1.

The command reads A's temporal X indexes plus the separately delivered source
roots.  It never consumes Y/outcome fields while constructing a profile.  A
formal training release is opt-in and requires a full, zero-failure export.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from oss_mentor.profiling.temporal_training import (  # noqa: E402
    TEMPORAL_DATASET_VERSION,
    TEMPORAL_PROFILE_RULES_VERSION,
    build_temporal_training_profile,
    canonical_json_bytes,
    load_jsonl,
    load_questionnaire_contract,
    sha256_file,
    validate_temporal_training_export,
)


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("wb") as handle:
        for row in rows:
            handle.write(canonical_json_bytes(row) + b"\n")


def _select_preview(groups: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    if count <= 0 or count >= len(groups):
        return list(groups)
    ordered = sorted(
        groups,
        key=lambda row: (str(row["cutoff_at"]), int(row["github_numeric_id"])),
    )
    if count == 1:
        return [ordered[len(ordered) // 2]]
    indices = {
        round(index * (len(ordered) - 1) / (count - 1))
        for index in range(count)
    }
    return [ordered[index] for index in sorted(indices)]


def _git_head(repo: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _sha256_lf_text(path: Path) -> str:
    """Hash tracked text with Git's LF byte convention, independent of core.autocrlf."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _artifact_metadata(path: Path) -> dict[str, Any]:
    return {
        "path": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _write_parquet_mirror(path: Path, rows: list[dict[str, Any]]) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as error:  # pragma: no cover - depends on release environment
        raise ValueError(
            "formal training export requires pyarrow to write the Parquet mirror"
        ) from error
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, path, compression="zstd")
    restored = [dict(row) for row in pq.read_table(path).to_pylist()]
    if len(restored) != len(rows) or any(
        canonical_json_bytes(expected) != canonical_json_bytes(actual)
        for expected, actual in zip(rows, restored)
    ):
        raise ValueError("Parquet mirror does not round-trip to the JSONL content")


def export_profiles(args: argparse.Namespace) -> dict[str, Any]:
    dataset_root = Path(args.dataset_root).resolve()
    ab_root = Path(args.ab_root).resolve()
    backfill_root = Path(args.backfill_root).resolve()
    questionnaire_path = Path(args.questionnaire).resolve()
    output_dir = Path(args.output_dir).resolve()
    release_training_package = bool(args.release_training_package)
    for path, label in (
        (dataset_root, "dataset_root"),
        (ab_root, "ab_root"),
        (backfill_root, "backfill_root"),
    ):
        if not path.is_dir():
            raise ValueError(f"{label} is not a directory: {path}")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"output directory must be absent or empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    source_summary = json.loads(
        (dataset_root / "summary.json").read_text(encoding="utf-8-sig")
    )
    if not isinstance(source_summary, dict):
        raise ValueError("dataset summary must be a JSON object")

    contribution_rows = load_jsonl(dataset_root / "file_contributions.jsonl")
    contributions_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in contribution_rows:
        key = (str(row["repository"]), str(row["commit_sha"]), str(row["path"]))
        if key in contributions_by_key:
            raise ValueError(f"duplicate contribution index key: {key}")
        contributions_by_key[key] = row

    groups_by_split = {
        split: load_jsonl(dataset_root / f"{split}.jsonl")
        for split in ("train", "validation", "test")
    }
    selected: list[dict[str, Any]] = []
    for split, groups in groups_by_split.items():
        candidates = _select_preview(groups, args.preview_per_split)
        if any(row.get("split") != split for row in candidates):
            raise ValueError(f"{split}.jsonl contains a mismatched split field")
        selected.extend(candidates)

    questionnaire = load_questionnaire_contract(questionnaire_path)
    if release_training_package:
        delivery_status = "research_frozen_training"
    elif args.preview_per_split > 0:
        delivery_status = "preview_not_for_training"
    else:
        delivery_status = "research_frozen_candidate"
    profiles: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for group in sorted(
        selected,
        key=lambda row: (
            str(row["split"]),
            str(row["cutoff_at"]),
            int(row["github_numeric_id"]),
        ),
    ):
        record_id = f"{group.get('github_numeric_id')}@{group.get('cutoff_at')}"
        try:
            profile, audit = build_temporal_training_profile(
                group,
                contributions_by_key=contributions_by_key,
                source_roots={"ab": ab_root, "backfill": backfill_root},
                questionnaire=questionnaire,
                delivery_status=delivery_status,
                dataset_version=TEMPORAL_DATASET_VERSION,
                rules_version=TEMPORAL_PROFILE_RULES_VERSION,
            )
            profiles.append(profile)
            audits.append(audit)
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            failures.append(
                {
                    "record_id": record_id,
                    "github_numeric_id": group.get("github_numeric_id"),
                    "as_of": group.get("cutoff_at"),
                    "split": group.get("split"),
                    "code": "profile_generation_failed",
                    "detail": str(error),
                }
            )

    profiles_filename = (
        "enhanced_profiles.jsonl" if release_training_package else "profiles.jsonl"
    )
    profiles_path = output_dir / profiles_filename
    failures_path = output_dir / "profile_failures.jsonl"
    audits_path = output_dir / "profile_audit.jsonl"
    validation_path = output_dir / "profile_validation_report.json"
    validation_report, validation_failures = validate_temporal_training_export(
        profiles,
        expected_feature_names=questionnaire["feature_names"],
        source=profiles_filename,
    )
    failures.extend(validation_failures)
    validation_report["records"]["selected_groups"] = len(selected)
    validation_report["records"]["generation_failed"] = (
        len(failures) - len(validation_failures)
    )
    if failures:
        validation_report["status"] = "failed"

    _write_jsonl(profiles_path, profiles)
    _write_jsonl(failures_path, failures)
    _write_jsonl(audits_path, audits)
    validation_report["input"].update(_artifact_metadata(profiles_path))
    validation_path.write_text(
        json.dumps(validation_report, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )

    parquet_path: Path | None = None
    if release_training_package and not failures:
        parquet_path = output_dir / "enhanced_profiles.parquet"
        _write_parquet_mirror(parquet_path, profiles)

    split_counts = Counter(row["split"] for row in profiles)
    feature_counts = Counter()
    for row in profiles:
        for feature_name, observed in zip(row["feature_names"], row["observed_mask"]):
            if observed:
                feature_counts[feature_name] += 1
    input_files = {
        name: {
            "path": str((dataset_root / name).relative_to(ROOT)).replace("\\", "/")
            if (dataset_root / name).is_relative_to(ROOT)
            else str(dataset_root / name),
            "sha256": _sha256_lf_text(dataset_root / name),
            "hash_basis": "utf8_lf_normalized",
            "working_tree_sha256": sha256_file(dataset_root / name),
        }
        for name in (
            "source_manifest.json",
            "summary.json",
            "file_contributions.jsonl",
            "train.jsonl",
            "validation.jsonl",
            "test.jsonl",
        )
    }
    training_allowed = bool(
        release_training_package
        and not failures
        and validation_report["status"] == "passed"
        and len(profiles) == len(selected)
        and parquet_path is not None
    )
    observed_cutoffs = list(validation_report["time_coverage"]["cutoffs"])
    known_gaps = [
        {
            "code": "source_dataset_not_declared_training_ready",
            "detail": (
                "A source summary remains status="
                f"{source_summary.get('status')!r}, "
                f"training_ready={source_summary.get('training_ready')!r}; "
                "B freezes only the validated profile projection."
            ),
        },
        {
            "code": "commit_time_proxy",
            "detail": str(source_summary.get("x_time_basis") or "unknown"),
        },
        {
            "code": "incomplete_2026_q4_follow_up",
            "detail": str(source_summary.get("test_observation") or "unknown"),
        },
        {
            "code": "pre_2024_q2_not_covered",
            "detail": (
                "The earliest available profile cutoff is "
                f"{observed_cutoffs[0] if observed_cutoffs else 'unknown'}."
            ),
        },
    ]
    artifacts = {
        profiles_filename: _artifact_metadata(profiles_path),
        "profile_failures.jsonl": _artifact_metadata(failures_path),
        "profile_audit.jsonl": _artifact_metadata(audits_path),
        "profile_validation_report.json": _artifact_metadata(validation_path),
    }
    if parquet_path is not None:
        artifacts[parquet_path.name] = _artifact_metadata(parquet_path)

    limitations = [
        "Only time-safe direct file contributions are used.",
        "Weak-interest evidence, Y outcomes, and post-cutoff facts are excluded.",
        "Commit event time is used; first public availability was not independently verified.",
        "Capability values are conservative path proxies, not verified ability probabilities.",
    ]
    if not release_training_package:
        limitations.insert(
            0,
            "This is a schema-integration preview/candidate and must not be used for training.",
        )

    manifest = {
        "schema_version": "temporal-profile-delivery-manifest-v0.1",
        "delivery_status": delivery_status,
        "training_allowed": training_allowed,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_commit": args.source_commit or _git_head(ROOT),
        "source_dataset_version": TEMPORAL_DATASET_VERSION,
        "rules_version": TEMPORAL_PROFILE_RULES_VERSION,
        "profile_state": "research_frozen",
        "feature_schema_version": questionnaire["feature_schema_version"],
        "feature_names": list(questionnaire["feature_names"]),
        "selection": {
            "mode": (
                "training_release"
                if release_training_package
                else ("preview" if args.preview_per_split > 0 else "full_candidate")
            ),
            "requested_per_split": args.preview_per_split,
            "selected_groups": len(selected),
        },
        "profiles": {
            "valid": len(profiles),
            "failed": len(failures),
            "developers": len({row["developer_key"] for row in profiles}),
            "by_split": dict(sorted(split_counts.items())),
            "all_unobserved": sum(not any(row["observed_mask"]) for row in profiles),
            "per_developer": validation_report["profiles_per_developer"],
        },
        "time_coverage": validation_report["time_coverage"],
        "feature_coverage": [
            {
                "feature_name": name,
                "observed_profiles": feature_counts[name],
                "observed_rate": round(feature_counts[name] / len(profiles), 6)
                if profiles
                else 0.0,
            }
            for name in questionnaire["feature_names"]
        ],
        "source_roots": {
            "ab_manifest_sha256": sha256_file(ab_root / "manifest.json"),
            "backfill_manifest_sha256": sha256_file(backfill_root / "manifest.json"),
            "machine_local_paths_omitted": True,
        },
        "input_files": input_files,
        "artifacts": artifacts,
        "known_gaps": known_gaps,
        "limitations": limitations,
    }
    manifest_path = output_dir / "profile_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary = {
        "output_dir": str(output_dir),
        "valid_profiles": len(profiles),
        "failed_profiles": len(failures),
        "training_allowed": training_allowed,
        "profiles_sha256": manifest["artifacts"][profiles_filename]["sha256"],
        "manifest_sha256": sha256_file(manifest_path),
    }
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-root",
        default=str(ROOT / "datasets/temporal_xy_2024_2026_v3"),
    )
    parser.add_argument(
        "--ab-root",
        default=str(ROOT / "datasets/b_real_contributions_v0.2"),
    )
    parser.add_argument("--backfill-root", required=True)
    parser.add_argument(
        "--questionnaire",
        default=str(ROOT / "config/midterm_profile/questionnaire_v0.1.json"),
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--preview-per-split",
        type=int,
        default=4,
        help="Deterministically select this many groups from each split; 0 processes all groups.",
    )
    parser.add_argument(
        "--release-training-package",
        action="store_true",
        help=(
            "Freeze a full zero-failure JSONL+Parquet training package. "
            "Requires --preview-per-split 0."
        ),
    )
    parser.add_argument("--source-commit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.preview_per_split < 0:
        raise SystemExit("--preview-per-split must be non-negative")
    if args.release_training_package and args.preview_per_split != 0:
        raise SystemExit(
            "--release-training-package requires --preview-per-split 0"
        )
    try:
        summary = export_profiles(args)
    except (OSError, TypeError, ValueError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if summary["failed_profiles"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
