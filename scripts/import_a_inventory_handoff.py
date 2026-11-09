"""Import A's offline full-inventory handoff into an isolated SQLite database."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from oss_mentor.inventory_handoff import import_recommendable_handoff, write_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--assume-inventory-available", action="store_true", required=True)
    parser.add_argument("--allow-existing", action="store_true")
    args = parser.parse_args()
    result = import_recommendable_handoff(
        args.handoff,
        args.database,
        assume_inventory_available=args.assume_inventory_available,
        allow_existing=args.allow_existing,
    )
    if args.report:
        write_report(args.report, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
