"""Offline checks for A's draft snapshot; no shared contract types are defined here."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path


def _local_file(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"Missing file or path outside snapshot: {relative}")
    return path


def _utc(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as error:
        raise ValueError(f"Invalid UTC timestamp: {value}") from error
    if result.utcoffset() != timedelta(0):
        raise ValueError(f"Timestamp must use UTC: {value}")
    return result


def load_snapshot(directory: Path) -> tuple[dict, dict, dict[str, bytes]]:
    """Read and validate one immutable in-memory copy of a draft snapshot."""
    root = directory.resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest["schema_version"] not in ("midterm-a-draft-v1", "midterm-a-source-v1"):
        raise ValueError("Unsupported draft schema_version")
    if type(manifest["fixture_only"]) is not bool:
        raise ValueError("fixture_only must be a boolean")
    if (manifest["schema_version"] == "midterm-a-draft-v1") != manifest["fixture_only"]:
        raise ValueError("Fixture/source schema and fixture_only flag disagree")
    _utc(manifest["created_at"])
    start = _utc(manifest["time_coverage"]["start"])
    end = _utc(manifest["time_coverage"]["end"])
    if start is None or end is None or start > end:
        raise ValueError("Invalid time coverage")
    if not manifest["source_versions"] or not manifest["license"]:
        raise ValueError("Source versions and license are required")
    contents = {}
    for item in manifest["files"]:
        if item["path"] in contents:
            raise ValueError("Duplicate manifest file path")
        payload = _local_file(root, item["path"]).read_bytes()
        if hashlib.sha256(payload).hexdigest() != item["sha256"]:
            raise ValueError(f"Checksum mismatch: {item['path']}")
        if len(payload) != item["size_bytes"]:
            raise ValueError(f"Size mismatch: {item['path']}")
        contents[item["path"]] = payload
    listed = {item["path"] for item in manifest["files"]}
    if "records.json" not in listed:
        raise ValueError("records.json must have a manifest checksum")
    records = json.loads(contents["records.json"])
    expected_kinds = {"repositories", "developers", "files", "code_documents", "issues",
                      "contributions", "project_behaviors", "issue_file_links", "file_aliases"}
    if set(records) != expected_kinds:
        raise ValueError("Unexpected or missing record kinds")
    indexes = {}
    for kind, key in (
        ("repositories", "repository_key"), ("developers", "developer_key"),
        ("files", "file_key"), ("code_documents", "file_version_key"),
        ("issues", "issue_key"),
    ):
        rows = records[kind]
        indexes[kind] = {row[key]: row for row in rows}
        if len(indexes[kind]) != len(rows):
            raise ValueError(f"Duplicate key in {kind}")
    counts = {kind: len(rows) for kind, rows in records.items()}
    if counts != manifest["entity_counts"]:
        raise ValueError("Entity counts differ from manifest")

    def reference(kind: str, key: str) -> dict:
        try:
            return indexes[kind][key]
        except KeyError as error:
            raise ValueError(f"Broken reference to {kind}: {key}") from error

    for rows in records.values():
        for row in rows:
            for name, value in row.items():
                if name.endswith("_at"):
                    _utc(value)
            if "repository_key" in row:
                reference("repositories", row["repository_key"])
            if "developer_key" in row:
                reference("developers", row["developer_key"])
    for row in records["code_documents"]:
        logical_file = reference("files", row["file_key"])
        if logical_file["repository_key"] != row["repository_key"]:
            raise ValueError("Code document repository mismatch")
        if row["availability"] == "available":
            if row["content_location"] not in listed:
                raise ValueError("Code content must have a manifest checksum")
            content = contents[row["content_location"]]
            if hashlib.sha256(content).hexdigest() != row["content_hash"]:
                raise ValueError("Code content hash mismatch")
        elif row["availability"] != "missing" or not row["exclusion_reason"]:
            raise ValueError("Missing code must state a reason")
        elif row["content_location"] is not None or row["content_hash"] is not None:
            raise ValueError("Missing code must not claim content or checksum")
    for row in records["contributions"]:
        document = reference("code_documents", row["file_version_key"])
        reference("files", row["file_key"])
        for name in ("repository_key", "file_key", "commit_sha"):
            if row[name] != document[name]:
                raise ValueError(f"Contribution/code mismatch: {name}")
        author = reference("developers", row["developer_key"])
        if row["author_association_status"] != author["identity_status"]:
            raise ValueError("Author identity status mismatch")
        event = _utc(row["event_at"])
        if event is None or not start <= event <= end:
            raise ValueError("Contribution event outside coverage")
        if not row["evidence_location"]:
            raise ValueError("Contribution must include source evidence")
    for row in records["project_behaviors"]:
        if row["event_time_unknown"] != (row["event_at"] is None):
            raise ValueError("Unknown behavior time must remain null")
    for row in records["issues"]:
        if row["text_location"] not in listed:
            raise ValueError("Issue text must have a manifest checksum")
        _local_file(root, row["text_location"])
        if row["current_availability"] == "eligible" and row["state"] != "open":
            raise ValueError("Closed issue cannot be eligible")
        if row["availability_checked_at"] is None:
            raise ValueError("Fixture availability must have a check time")
        if row["state"] == "open" and row["outcome"] is not None:
            raise ValueError("Open fixture issue must not invent an outcome")
    for row in records["issue_file_links"]:
        issue = reference("issues", row["issue_key"])
        logical_file = reference("files", row["file_key"])
        if issue["repository_key"] != logical_file["repository_key"]:
            raise ValueError("Issue/file repository mismatch")
        available = _utc(row["available_at"])
        created = _utc(issue["created_at"])
        if available is None or available < created:
            raise ValueError("Issue/file link predates issue")
        if row["evidence_kind"] == "resolution_pr":
            outcome = issue["outcome"]
            if outcome is None or available < _utc(outcome["resolved_at"]):
                raise ValueError("Resolution link cannot be available before resolution")
            if row["feature_role"] != "outcome_only":
                raise ValueError("Resolution link must remain outcome_only")
        elif row["evidence_kind"] != "issue_body_path" or row["feature_role"] not in ("pre_resolution_hint", "observed_text_hint"):
            raise ValueError("Unknown issue/file evidence role")
        elif row["feature_role"] == "observed_text_hint" and _utc(row["available_at"]) != _utc(issue["text_observed_at"]):
            raise ValueError("Observed text hints cannot be backdated")
    for row in records["file_aliases"]:
        reference("files", row["file_key"])
        if not row["evidence_location"] or row["old_path"] == row["new_path"]:
            raise ValueError("Rename must include evidence and distinct paths")
    if not manifest["fixture_only"]:
        from oss_mentor.datasets.source_validation import validate_github_history
        validate_github_history(manifest, records, contents)
    return manifest, records, contents


def validate_snapshot(directory: Path) -> dict:
    """Read, verify and summarize a complete local draft fixture without network/DB."""
    manifest, records, _ = load_snapshot(directory)
    return {
        "status": "fixture_verified" if manifest["fixture_only"] else "source_verified",
        "fixture_only": manifest["fixture_only"],
        "schema_version": manifest["schema_version"],
        "dataset_version": manifest["dataset_version"],
        "entity_counts": {kind: len(rows) for kind, rows in records.items()},
        "quality_gaps": manifest["quality_gaps"],
    }
