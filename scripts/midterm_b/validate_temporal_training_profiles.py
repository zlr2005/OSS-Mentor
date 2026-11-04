"""Independently validate B's temporal research-frozen profile export."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from oss_mentor.profiling.temporal_training import (  # noqa: E402
    canonical_json_bytes,
    load_jsonl,
    load_questionnaire_contract,
    sha256_file,
    validate_temporal_training_export,
)


def _load_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.casefold() == ".jsonl":
        return load_jsonl(path)
    if path.suffix.casefold() == ".parquet":
        try:
            import pyarrow.parquet as pq
        except ImportError as error:  # pragma: no cover - environment dependent
            raise ValueError("validating Parquet requires pyarrow") from error
        return [dict(row) for row in pq.read_table(path).to_pylist()]
    raise ValueError("profiles must use .jsonl or .parquet")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("wb") as handle:
        for row in rows:
            handle.write(canonical_json_bytes(row) + b"\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", required=True)
    parser.add_argument(
        "--questionnaire",
        default=str(ROOT / "config/midterm_profile/questionnaire_v0.1.json"),
    )
    parser.add_argument("--output-dir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    profiles_path = Path(args.profiles).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        rows = _load_rows(profiles_path)
        questionnaire = load_questionnaire_contract(Path(args.questionnaire).resolve())
        report, failures = validate_temporal_training_export(
            rows,
            expected_feature_names=questionnaire["feature_names"],
            source=profiles_path.name,
        )
        report["input"].update(
            {
                "path": str(profiles_path),
                "size_bytes": profiles_path.stat().st_size,
                "sha256": sha256_file(profiles_path),
            }
        )
    except (OSError, TypeError, ValueError) as error:
        report = {
            "schema_version": "b-temporal-profile-validation-report-v0.1",
            "status": "failed",
            "input": {"path": str(profiles_path)},
            "failure_codes": {"invalid_profile_file": 1},
        }
        failures = [
            {
                "record_id": "file",
                "code": "invalid_profile_file",
                "detail": str(error),
            }
        ]

    (output_dir / "profile_validation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_jsonl(output_dir / "profile_validation_failures.jsonl", failures)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
