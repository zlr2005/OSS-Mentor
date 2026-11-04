"""Collect a small real GitHub history snapshot, or rebuild it from cached evidence."""

import argparse
import json
import os
from pathlib import Path

from oss_mentor.collector.github_client import GitHubClient
from oss_mentor.datasets.github_history import CachedGitHubSource, collect_history, write_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commits", type=int, default=3)
    parser.add_argument("--files-per-commit", type=int, default=2)
    parser.add_argument("--allow-network", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a new snapshot directory")
    client = GitHubClient(api_base="https://api.github.com", api_version="2026-03-10",
                          user_agent="OSS-Mentor-midterm-a/0.1", token=os.getenv("GITHUB_TOKEN") or None,
                          timeout_seconds=20, max_retries=2)
    source = CachedGitHubSource(client, args.source_dir, args.repo, allow_network=args.allow_network)
    try:
        manifest, records, assets = collect_history(source, until=args.until, commit_limit=args.commits,
                                                    files_per_commit=args.files_per_commit)
        report = write_snapshot(args.output, manifest, records, assets)
    except Exception as error:
        # Keep secrets and public raw identities out of terminal/report errors.
        print(json.dumps({"status": "failed", "error_type": type(error).__name__,
                          "status_code": getattr(error, "status_code", None),
                          "detail": str(error) if isinstance(error, ValueError) else None,
                          "message": "Collection failed; completed source requests remain cached for retry."}))
        return 1
    print(json.dumps({**report, "network_request_count": client.request_count}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
