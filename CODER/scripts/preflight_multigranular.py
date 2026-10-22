"""Read-only readiness report for CODER multigranular training."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from coder_issue.training.config import load_neural_settings
from coder_issue.training.preflight import run_preflight


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--variant", default="coder_issue_repo")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = run_preflight(load_neural_settings(args.config), variant=args.variant)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["status"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
