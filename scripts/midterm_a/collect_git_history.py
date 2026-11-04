"""Build unfrozen working data from a fetched Git history and cached GitHub metadata."""

import argparse
import json
import os
from pathlib import Path

from oss_mentor.collector.github_client import GitHubClient
from oss_mentor.datasets.github_history import CachedGitHubSource, write_snapshot
from oss_mentor.datasets.git_history import collect_git_history
from oss_mentor.datasets.git_objects import GitObjectReader
from oss_mentor.datasets.identity_map import IdentityMap


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--anchor", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--git-dir", required=True, type=Path)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--identity-map", required=True, type=Path)
    parser.add_argument("--working-dir", required=True, type=Path)
    parser.add_argument("--commits", type=int, default=100)
    parser.add_argument("--files-per-commit", type=int, default=2)
    parser.add_argument("--allow-network", action="store_true")
    args = parser.parse_args()
    if args.working_dir.exists():
        parser.error("Working directory exists; choose a new revision directory")
    client = GitHubClient(api_base="https://api.github.com", api_version="2026-03-10",
                          user_agent="OSS-Mentor-midterm-a/0.2", token=os.getenv("GITHUB_TOKEN") or None,
                          timeout_seconds=20, max_retries=2)
    try:
        source = CachedGitHubSource(client, args.source_dir, args.repo, allow_network=args.allow_network)
        repo, _ = source.get("/repos/" + args.repo)
        identities = IdentityMap(args.identity_map, repo.payload["id"])
        with GitObjectReader(args.git_dir) as reader:
            manifest, records, assets = collect_git_history(reader, source, identities, anchor=args.anchor,
                                                            until=args.until, commit_limit=args.commits,
                                                            files_per_commit=args.files_per_commit)
        report = write_snapshot(args.working_dir, manifest, records, assets)
        identities.save()
    except Exception as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__,
                          "status_code": getattr(error, "status_code", None),
                          "detail": str(error) if isinstance(error, ValueError) else None,
                          "resume": "Git objects and successful API requests remain cached."}))
        return 1
    print(json.dumps({**report, "dataset_lifecycle": "working", "frozen": False,
                      "network_request_count": client.request_count}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
