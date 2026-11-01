"""Cache actual GitHub connector read results with their acquisition transport recorded."""

import argparse
import json
from pathlib import Path

from oss_mentor.collector.github_client import GitHubClient
from oss_mentor.datasets.connector_cache import cache_connector_reads
from oss_mentor.datasets.github_history import CachedGitHubSource


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    args = parser.parse_args()
    client = GitHubClient(api_base="https://api.github.com", api_version="2026-03-10", user_agent="OSS-Mentor", token=None)
    source = CachedGitHubSource(client, args.source_dir, args.repo, allow_network=False)
    count = cache_connector_reads(source, json.loads(args.input.read_text(encoding="utf-8"))["entries"])
    print(json.dumps({"connector_reads_cached": count, "network_requests": 0}))


if __name__ == "__main__":
    main()
