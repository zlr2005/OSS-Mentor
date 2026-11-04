"""Verify the complete GFI-Bot resolved_issue member and stage paper outcomes."""

import argparse
import json
from pathlib import Path

from oss_mentor.datasets.gfibot import extract_resolved, import_resolved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('data/midterm/sources/gfibot/6837711'))
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if args.database.exists() or args.report.exists():
        parser.error('Choose new output paths; existing work is preserved')
    member, evidence = extract_resolved(args.source)
    report = import_resolved(member, args.database, evidence)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'source_evidence'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
