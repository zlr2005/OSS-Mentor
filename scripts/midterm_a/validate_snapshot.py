"""Validate A's draft fixture: run from repository root with PYTHONPATH=src."""

import argparse
import json
from pathlib import Path

from oss_mentor.datasets.snapshot import validate_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        report = validate_snapshot(args.directory)
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(json.dumps({"status": "invalid", "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
