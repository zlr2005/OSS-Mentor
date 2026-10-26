"""Deterministic normalization for A's draft snapshot, before source adapters land."""

import hashlib
import json


# A source adapter must supply canonical opaque entity keys. These fields identify
# events/relations for deduplication; observation metadata is not a new event.
IDENTITY_FIELDS = {
    "repositories": ("repository_key",),
    "developers": ("developer_key",),
    "files": ("file_key",),
    "code_documents": ("file_version_key",),
    "contributions": ("developer_key", "repository_key", "file_version_key", "commit_sha", "behavior_type"),
    "project_behaviors": ("developer_key", "repository_key", "behavior_type", "event_at", "source"),
    "issues": ("issue_key",),
    "issue_file_links": ("issue_key", "file_key", "evidence_kind", "available_at", "source"),
    "file_aliases": ("file_key", "commit_sha", "old_path", "new_path"),
}


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def record_key(kind: str, row: dict) -> str:
    values = [row[name] for name in IDENTITY_FIELDS[kind]]
    if kind == "project_behaviors" and row.get("commit_sha"):
        values.append(row["commit_sha"])
    if kind == "issue_file_links" and row.get("commit_sha"):
        values.extend([row.get("pr_number"), row["commit_sha"]])
    return hashlib.sha256(canonical_json(values).encode("utf-8")).hexdigest()


def normalize_records(records: dict) -> tuple[dict, dict]:
    """Remove exact duplicate events; fail on conflicting rows with one identity.

    This does not infer GitHub identities or merge authors by name/email. Unknown
    source authors and unknown event timestamps remain unchanged.
    """
    result, duplicates = {}, {}
    for kind in IDENTITY_FIELDS:
        seen = {}
        for row in records[kind]:
            key, payload = record_key(kind, row), canonical_json(row)
            if key in seen and seen[key] != payload:
                raise ValueError(f"Conflicting records in {kind}: {key}")
            seen[key] = payload
        result[kind] = [json.loads(payload) for payload in seen.values()]
        duplicates[kind] = len(records[kind]) - len(seen)
    return result, duplicates
