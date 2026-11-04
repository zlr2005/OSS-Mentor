"""Enrich a small working history sample with explicit Issue/PR/file evidence."""

import argparse
import json
import os
from pathlib import Path

from oss_mentor.collector.github_client import GitHubClient
from oss_mentor.datasets.github_history import CachedGitHubSource, write_snapshot
from oss_mentor.datasets.issue_links import enrich_issue_links


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--working-dir", type=Path, required=True)
    parser.add_argument("--commits", type=int, default=4)
    parser.add_argument("--allow-network", action="store_true")
    args = parser.parse_args()
    if args.working_dir.exists() or not 2 <= args.commits <= 10:
        parser.error("Choose a new working directory and a sample size between 2 and 10 commits")
    client = GitHubClient(api_base="https://api.github.com", api_version="2026-03-10",
                          user_agent="OSS-Mentor-issue-links/0.1", token=os.getenv("GITHUB_TOKEN") or None,
                          timeout_seconds=20, max_retries=2)
    try:
        source = CachedGitHubSource(client, args.source_dir, args.repo, allow_network=args.allow_network)
        manifest, records, assets = enrich_issue_links(args.directory, source, commit_limit=args.commits)
        report = write_snapshot(args.working_dir, manifest, records, assets)
    except Exception as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__,
                          "detail": str(error) if isinstance(error, ValueError) else None,
                          "status_code": getattr(error, "status_code", None)}))
        return 1
    print(json.dumps({**report, "dataset_lifecycle": "working", "frozen": False,
                      "issue_audit": manifest["source_provenance"]["issue_enrichment"]["summary"],
                      "api_network_requests": client.request_count,
                      "html_network_requests": getattr(source, "html_request_count", 0)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
