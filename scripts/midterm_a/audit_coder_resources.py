"""Audit official CODER resources at a pinned commit, using cached public evidence."""

import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from oss_mentor.collector.github_client import GitHubClient
from oss_mentor.datasets.github_history import CachedGitHubSource, decode_blob, digest


DRIVE = "https://drive.google.com/drive/folders/1-dehBnSc9kBcEKwL9SroEqNMVoLOvU1F"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-network", action="store_true")
    args = parser.parse_args()
    client = GitHubClient(api_base="https://api.github.com", api_version="2026-03-10",
                          user_agent="OSS-Mentor-midterm-a-audit/0.1", token=os.getenv("GITHUB_TOKEN") or None,
                          timeout_seconds=20, max_retries=2)
    source = CachedGitHubSource(client, args.source_dir, "Ahren09/CODER", allow_network=args.allow_network)
    repo, _ = source.get("/repos/Ahren09/CODER")
    commit, _ = source.get("/repos/Ahren09/CODER/commits/" + repo.payload["default_branch"])
    sha = commit.payload["sha"]
    tree, _ = source.get("/repos/Ahren09/CODER/git/trees/" + commit.payload["commit"]["tree"]["sha"], {"recursive": 1})
    if tree.payload["truncated"]:
        raise ValueError("CODER audit needs a complete repository tree")
    files = {row["path"]: row for row in tree.payload["tree"] if row["type"] == "blob"}
    inspected = []
    for path in ("README.md", "src/dataset/download_repositories.py", "src/utility/utils.py"):
        response, evidence = source.get("/repos/Ahren09/CODER/git/blobs/" + files[path]["sha"])
        content = decode_blob(response.payload)
        inspected.append({"path": path, "size_bytes": len(content), "sha256": digest(content),
                          "source_location": evidence,
                          "url": f"https://github.com/Ahren09/CODER/blob/{sha}/{path}"})
    drive_path = args.source_dir / "drive_check.json"
    if args.allow_network:
        try:
            request = urllib.request.Request(DRIVE, headers={"User-Agent": "OSS-Mentor-resource-audit"})
            with urllib.request.urlopen(request, timeout=20) as response:
                status = response.status
                page = response.read()
                (args.source_dir / "drive_folder.html").write_bytes(page)
            drive = {"url": DRIVE, "http_status": status,
                     "status": "page_accessible_attachments_not_verified", "page_sha256": digest(page)}
        except urllib.error.HTTPError as error:
            drive = {"url": DRIVE, "http_status": error.code, "status": "unavailable_from_this_environment"}
        except (OSError, TimeoutError):
            drive = {"url": DRIVE, "http_status": None, "status": "unavailable_from_this_environment"}
        drive_path.write_text(json.dumps(drive, indent=2) + "\n", encoding="utf-8")
    else:
        drive = json.loads(drive_path.read_text(encoding="utf-8"))
    audit = {
        "audit_version": "coder-resource-audit-v1", "repository": "Ahren09/CODER",
        "checked_at": repo.fetched_at.isoformat(), "commit_sha": sha,
        "tree_sha": tree.payload["sha"], "tree_truncated": tree.payload["truncated"],
        "repository_license": repo.payload.get("license"),
        "repository_files": sorted(files), "inspected_files": inspected, "raw_data_link_check": drive,
        "findings": {
            "training_implementation_in_checked_tree": False if set(files) == {
                ".gitattributes", ".gitignore", "README.md", "img/CODER.png",
                "src/dataset/download_repositories.py", "src/utility/utils.py"} else None,
            "processed_dataset_in_checked_tree": False if set(files) == {
                ".gitattributes", ".gitignore", "README.md", "img/CODER.png",
                "src/dataset/download_repositories.py", "src/utility/utils.py"} else None,
            "license_file_in_checked_tree": any(Path(p).name.casefold().startswith(("license", "copying")) for p in files),
            "readme_lists_raw_archives": ["stargazers.tar.gz", "stargazers_2023.tar.gz", "watchers.tar.gz", "subscribers.tar.gz", "forks.tar.gz"],
            "raw_archives_downloaded": False,
            "reproduction_status": "partial_resources_only",
        },
        "limitations": ["Link failure reflects this request; it does not prove permanent data deletion.",
                        "No explicit repository license found; no upstream source was vendored into project code.",
                        "GitHub supplements are separate sources, not confirmed original CODER benchmark records.",
                        "Training reconstruction and experiment protocol belong to C."],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"commit_sha": sha, "tree_sha": tree.payload["sha"], "files": len(files),
                      "drive_check": drive, "output": str(args.output)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
