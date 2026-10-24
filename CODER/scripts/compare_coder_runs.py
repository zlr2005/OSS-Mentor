"""Create a protocol-aware JSON/CSV comparison from existing CODER runs."""

from __future__ import annotations

import argparse
from pathlib import Path

from coder_issue.evaluation.comparison import build_run_comparison, write_run_comparison


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, action="append", required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--split", choices=("valid", "test"), default="test")
    parser.add_argument("--mode", choices=("same_repo", "cross_repo"), default="cross_repo")
    parser.add_argument(
        "--group",
        choices=(
            "all",
            "predictable",
            "unpredictable",
            "warm_start",
            "cold_start",
            "cross_repository_user",
        ),
        default="predictable",
    )
    parser.add_argument("--cutoff", type=int, default=10)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    if args.cutoff <= 0:
        raise ValueError("--cutoff must be positive")
    report = build_run_comparison(
        args.run_dir,
        split=args.split,
        mode=args.mode,
        group=args.group,
        cutoff=args.cutoff,
    )
    write_run_comparison(
        report,
        json_path=args.output_json,
        csv_path=args.output_csv,
        overwrite=args.overwrite,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
