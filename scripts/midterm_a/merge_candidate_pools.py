"""Build a new isolated standard candidate database from local source evidence."""
import argparse
import json
from pathlib import Path
from oss_mentor.datasets.merged_candidates import build_merged

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old-candidates', type=Path, default=Path('data/midterm/working/live_issuepilot_v0.3/candidates.json'))
    parser.add_argument('--old-source', type=Path, default=Path('data/midterm/sources/live_issuepilot_v0.1'))
    parser.add_argument('--teacher-review', type=Path, required=True)
    parser.add_argument('--prior-review', dest='prior_reviews', type=Path, action='append', default=[])
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_merged(**vars(args), migration=Path('db/sqlite/001_mvp.sql')), ensure_ascii=False, indent=2))
