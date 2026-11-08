"""Read the isolated working DB through the existing candidate storage interface."""
import argparse
import json
from pathlib import Path

from oss_mentor.storage.candidates import SQLiteCandidateStorage


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--limit', type=int, default=5, choices=range(1, 101), metavar='1..100')
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error('Working database does not exist')
    store = SQLiteCandidateStorage(args.database, Path(__file__).resolve().parents[2] / 'db/sqlite/001_mvp.sql')
    # Existing interface refreshes expired availability locally before returning candidates.
    status = store.candidate_pool_status()
    rows = store.available_candidates(limit=args.limit)
    print(json.dumps({'status': status, 'sample': rows}, ensure_ascii=False, indent=2))
