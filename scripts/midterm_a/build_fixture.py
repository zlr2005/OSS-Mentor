"""Rebuild the small synthetic A fixture deterministically, without network access."""

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "fixtures/midterm/a/snapshot_v0.1"


def build_fixture(target: Path = TARGET) -> None:
    target.mkdir(parents=True, exist_ok=True)
    observed = "2026-10-14T00:00:00Z"
    event = "2026-09-03T10:00:00Z"
    resolved = "2026-09-04T10:00:00Z"
    sha1, sha2 = "1" * 40, "2" * 40
    payloads = {
        "content/parser.py.txt": "def parse(value):\n    return value.strip()\n",
        "content/issue_closed.txt": "Synthetic: trim whitespace in src/parser.py.\n",
        "content/issue_open.txt": "Synthetic: document parser input handling.\n",
    }
    records = {
        "repositories": [{
            "repository_key": "repo-a", "source_repository_id": "synthetic-repo-1",
            "full_name": "synthetic/example", "primary_language": "Python",
            "domains": ["developer_tools"], "is_archived": False,
            "maintenance_status": "active", "contribution_url": "https://example.invalid/CONTRIBUTING",
            "observed_at": observed,
        }],
        "developers": [
            {"developer_key": "dev-a", "source": "synthetic", "source_developer_id": "author-1",
             "identity_status": "verified", "github_numeric_id": None},
            {"developer_key": "dev-unknown", "source": "synthetic", "source_developer_id": "git-author-2",
             "identity_status": "unconfirmed", "github_numeric_id": None},
        ],
        "files": [
            {"file_key": "file-a", "repository_key": "repo-a"},
            {"file_key": "file-b", "repository_key": "repo-a"},
        ],
        "code_documents": [
            {"file_version_key": "version-a", "file_key": "file-a", "repository_key": "repo-a",
             "path": "src/parser.py", "commit_sha": sha1, "language": "Python",
             "snapshot_at": event, "availability": "available", "exclusion_reason": None,
             "content_location": "content/parser.py.txt",
             "content_hash": hashlib.sha256(payloads["content/parser.py.txt"].encode()).hexdigest()},
            {"file_version_key": "version-missing", "file_key": "file-b", "repository_key": "repo-a",
             "path": "src/optional.py", "commit_sha": sha2, "language": "Python",
             "snapshot_at": resolved, "availability": "missing", "exclusion_reason": "source_content_unavailable",
             "content_location": None, "content_hash": None},
        ],
        "contributions": [
            {"developer_key": "dev-a", "repository_key": "repo-a", "file_key": "file-a",
             "file_version_key": "version-a", "commit_sha": sha1, "event_at": event,
             "observed_at": observed, "behavior_type": "commit_author",
             "author_association_status": "verified", "committer_source_id": "synthetic-bot",
             "evidence_location": f"https://example.invalid/commit/{sha1}"},
            {"developer_key": "dev-unknown", "repository_key": "repo-a", "file_key": "file-b",
             "file_version_key": "version-missing", "commit_sha": sha2, "event_at": resolved,
             "observed_at": observed, "behavior_type": "commit_author",
             "author_association_status": "unconfirmed", "committer_source_id": "author-1",
             "evidence_location": f"https://example.invalid/commit/{sha2}"},
        ],
        "project_behaviors": [{
            "developer_key": "dev-a", "repository_key": "repo-a", "behavior_type": "star",
            "event_at": None, "event_time_unknown": True, "observed_at": observed, "source": "synthetic",
        }],
        "issues": [
            {"issue_key": "issue-closed", "source_issue_id": "synthetic-101", "number": 101,
             "repository_key": "repo-a", "text_location": "content/issue_closed.txt", "labels": ["bug"],
             "created_at": "2026-09-01T00:00:00Z", "closed_at": resolved,
             "observed_at": observed, "state": "closed", "current_availability": "excluded",
             "availability_checked_at": observed,
             "outcome": {"resolver_developer_key": "dev-a", "final_pr_number": 201,
                         "resolved_at": resolved, "label_kind": "resolved_issue"}},
            {"issue_key": "issue-open", "source_issue_id": "synthetic-102", "number": 102,
             "repository_key": "repo-a", "text_location": "content/issue_open.txt", "labels": ["documentation"],
             "created_at": "2026-10-01T00:00:00Z", "closed_at": None,
             "observed_at": observed, "state": "open", "current_availability": "eligible",
             "availability_checked_at": observed, "outcome": None},
        ],
        "issue_file_links": [
            {"issue_key": "issue-closed", "file_key": "file-a", "evidence_kind": "issue_body_path",
             "available_at": "2026-09-01T00:00:00Z", "confidence": 1.0, "source": "synthetic",
             "feature_role": "pre_resolution_hint"},
            {"issue_key": "issue-closed", "file_key": "file-a", "evidence_kind": "resolution_pr",
             "available_at": resolved, "confidence": 1.0, "source": "synthetic",
             "feature_role": "outcome_only"},
        ],
        "file_aliases": [{
            "file_key": "file-a", "old_path": "src/legacy_parser.py", "new_path": "src/parser.py",
            "commit_sha": sha1, "available_at": event,
            "evidence_location": f"https://example.invalid/commit/{sha1}#rename",
        }],
    }
    payloads["records.json"] = json.dumps(records, ensure_ascii=False, indent=2) + "\n"
    files = []
    for name, content in sorted(payloads.items()):
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        path.write_bytes(data)
        files.append({"path": name, "format": "json" if name.endswith(".json") else "text",
                      "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    manifest = {
        "schema_version": "midterm-a-draft-v1", "dataset_version": "synthetic-a-v0.1",
        "code_snapshot_version": "synthetic-code-v0.1", "fixture_only": True,
        "created_at": "2026-10-15T00:00:00Z", "source_versions": {"synthetic": "v0.1"},
        "time_coverage": {"start": "2026-09-01T00:00:00Z", "end": observed},
        "entity_counts": {kind: len(rows) for kind, rows in records.items()}, "files": files,
        "quality_gaps": ["Synthetic data only; not valid for training results or live recommendations.",
                         "One unconfirmed author and one missing code document are intentional.",
                         "Star event time is unknown; observation time is not event time.",
                         "Draft fields await D's S0 alignment; fixture verified identity is synthetic."],
        "license": "Project-authored synthetic fixture; contains no third-party source code.",
        "access_restrictions": "None; all names and example.invalid evidence URLs are fictional.",
    }
    (target / "manifest.json").write_bytes((json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode())


if __name__ == "__main__":
    build_fixture()
    print(TARGET)
