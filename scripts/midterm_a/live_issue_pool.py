"""Plan current GitHub verification or build an isolated current candidate DB."""

import argparse
import json
from pathlib import Path

from oss_mentor.datasets.live_issues import build_pool, plan_verification, seed_repositories


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('data/midterm/sources/live_issuepilot_v0.1'))
    parser.add_argument('--paper-archive', type=Path, default=Path('tmp/supplementary_papers/issue-pilot.zip'))
    parser.add_argument('--review', type=Path, default=Path('config/midterm_datasets/live_issuepilot_review_v0.1.json'))
    commands = parser.add_subparsers(dest='command', required=True)
    plan = commands.add_parser('plan')
    plan.add_argument('--limit-per-repository', type=int, default=10)
    build = commands.add_parser('build')
    build.add_argument('--database', type=Path, required=True)
    build.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    seeds = seed_repositories(args.paper_archive)
    if args.command == 'plan':
        if args.limit_per_repository < 1:
            parser.error('limit must be positive')
        jobs = plan_verification(args.source, seeds, args.limit_per_repository)
        print(json.dumps(jobs, ensure_ascii=False))
    else:
        report = build_pool(args.source, seeds, args.database, args.output,
                            Path(__file__).resolve().parents[2] / 'db/sqlite/001_mvp.sql',
                            json.loads(args.review.read_text(encoding='utf-8')))
        print(json.dumps({k: v for k, v in report.items() if k not in {'source_files', 'repositories'}}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
