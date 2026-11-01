"""Working Git history datasets: batch retrieval, explicit identities and tree evidence."""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone

from oss_mentor.datasets.github_history import CachedGitHubSource, digest, opaque_key
from oss_mentor.datasets.git_objects import GitObjectReader, changed_files, flatten_tree, parse_commit
from oss_mentor.datasets.identity_map import IdentityMap
from oss_mentor.datasets.normalization import IDENTITY_FIELDS, canonical_json, normalize_records
from oss_mentor.datasets.snapshot import _utc


def collect_git_history(reader: GitObjectReader, source: CachedGitHubSource, identities: IdentityMap, *,
                        anchor: str, until: str, commit_limit: int = 100, files_per_commit: int = 2,
                        max_code_bytes: int = 262144) -> tuple[dict, dict, dict]:
    if not 1 <= files_per_commit <= 20:
        raise ValueError("files_per_commit must be between 1 and 20")
    prefix = "/repos/" + source.repository
    response, repository_evidence = source.get(prefix)
    repository = response.payload
    if repository["private"] is not False or (repository.get("license") or {}).get("spdx_id") != "MIT":
        raise ValueError("This collector supports public MIT repositories only")
    if identities.repository_id != repository["id"]:
        raise ValueError("Repository and persistent identity map disagree")
    selected = reader.select(anchor, commit_limit)
    if not selected or selected[0] != anchor:
        raise ValueError("Historical selection does not start at the anchor")
    api_records, api_evidence, duplicates = {}, {}, 0
    for page, evidence in source.pages(prefix + "/commits", {"sha": anchor, "per_page": 50}, max_pages=4):
        for row in page.payload:
            if row["sha"] in api_records:
                duplicates += 1
                if canonical_json(api_records[row["sha"]]) != canonical_json(row):
                    raise ValueError("Conflicting paginated commit metadata")
            api_records[row["sha"]], api_evidence[row["sha"]] = row, evidence
        if set(selected).issubset(api_records):
            break
    commits, full_trees = {}, {}

    def commit(sha):
        if sha not in commits:
            commits[sha] = parse_commit(reader.read("commit", sha))
        return commits[sha]

    def tree(sha):
        if sha not in full_trees:
            full_trees[sha] = flatten_tree(sha, reader.read)
        return full_trees[sha]

    contexts, identity_changes, excluded, license_evidence, tree_snapshots = [], [], [], [], []
    assets = {}
    for sha in selected:
        current = commit(sha)
        if _utc(current["event_at"]) > _utc(until):
            raise ValueError("Git commit exceeds the requested historical cutoff")
        entries = tree(current["tree"])
        license_entry = next((entries[p] for p in ("LICENSE", "LICENSE.txt", "LICENSE.rst") if p in entries), None)
        if license_entry is None or license_entry["type"] != "blob":
            raise ValueError("Historical MIT license file is missing")
        license_text = reader.read("blob", license_entry["sha"])
        if b"Permission is hereby granted, free of charge" not in license_text:
            raise ValueError("Unsupported historical license text")
        location = f"git_objects/blob/{license_entry['sha']}.bin"
        license_evidence.append({"commit_sha": sha, "blob_sha": license_entry["sha"], "content_location": location, "spdx_id": "MIT"})
        tree_path = f"trees/{sha}.json"
        assets[tree_path] = (json.dumps(entries, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        tree_snapshots.append({"commit_sha": sha, "tree_sha": current["tree"], "content_location": tree_path})
        if len(current["parents"]) > 1:
            excluded.append({"commit_sha": sha, "reason": "merge_commit_not_attributed_as_code_authorship"})
            continue
        previous = tree(commit(current["parents"][0])["tree"]) if current["parents"] else {}
        changes = changed_files(previous, entries)
        identity_changes.append((sha, changes))
        paths = [c for c in changes if c["path"].endswith(".py")][:files_per_commit]
        contexts.append((sha, current, entries, paths))
    # Reverse topological order registers older paths first. Existing keys from a
    # previous batch remain stable even when newly discovered aliases conflict.
    for sha, changes in reversed(identity_changes):
        for change in changes:
            if change["path"].endswith(".py"):
                identities.file(change["path"], previous_path=change.get("previous_path"), commit_sha=sha)
    repository_key = opaque_key("repository", "github", repository["id"])
    records = {kind: [] for kind in IDENTITY_FIELDS}
    records["repositories"].append({
        "repository_key": repository_key, "source_repository_id": str(repository["id"]),
        "full_name": repository["full_name"], "primary_language": repository.get("language"), "domains": [],
        "is_archived": repository["archived"], "maintenance_status": "unknown",
        "contribution_url": repository["html_url"] + "/contribute", "observed_at": response.fetched_at.isoformat(),
        "source_location": repository_evidence,
    })
    developers, files, used_paths, used_developers = {}, {}, {}, {}
    missing_metadata = []
    observed = max(json.loads(gzip.decompress(raw))["metadata"]["fetched_at"] for raw in source.used.values())
    for sha, current, entries, paths in contexts:
        if not paths:
            continue
        api = api_records.get(sha)
        if api is not None:
            if api["commit"]["tree"]["sha"] != current["tree"] or _utc(api["commit"]["committer"]["date"]) != _utc(current["event_at"]):
                raise ValueError("Git/API commit metadata mismatch")
        else:
            missing_metadata.append(sha)
        github_id = (api.get("author") or {}).get("id") if api else None
        author_hash = digest(current["author_identity"])
        developer_key = identities.developer(author_hash, github_id=github_id)
        scope = f"github:{github_id}" if github_id is not None else f"git-author:{author_hash}"
        used_developers[scope] = developer_key
        association = "verified" if github_id is not None else "unconfirmed"
        developers[developer_key] = {"developer_key": developer_key, "source": "github" if github_id is not None else "git_author_unconfirmed",
                                     "source_developer_id": str(github_id) if github_id is not None else author_hash,
                                     "github_numeric_id": github_id, "identity_status": association}
        commit_location = f"git_objects/commit/{sha}.bin"
        committer_id = (api.get("committer") or {}).get("id") if api else None
        url = repository["html_url"] + "/commit/" + sha
        records["project_behaviors"].append({
            "developer_key": developer_key, "repository_key": repository_key, "behavior_type": "contribution",
            "event_at": current["event_at"], "event_time_unknown": False, "observed_at": observed,
            "source": "github_git", "commit_sha": sha, "evidence_location": url, "source_location": commit_location,
        })
        for item in paths:
            path = item["path"]
            file_key = identities.file(path)
            used_paths[path] = file_key
            files[file_key] = {"file_key": file_key, "repository_key": repository_key}
            version_key = opaque_key("file-version", repository["id"], sha, path)
            reason, content_hash, content_location, blob_sha = None, None, None, None
            entry = entries.get(path)
            if item["status"] == "removed":
                reason = "file_deleted_at_commit"
            elif not entry or entry["type"] != "blob" or entry["mode"] not in ("100644", "100755"):
                reason = "not_a_regular_blob_in_historical_tree"
            else:
                blob_sha = entry["sha"]
                content = reader.read("blob", blob_sha)
                if len(content) > max_code_bytes:
                    reason = "code_size_limit"
                else:
                    try:
                        content.decode("utf-8")
                    except UnicodeDecodeError:
                        reason = "non_utf8_source"
                    else:
                        content_location, content_hash = f"git_objects/blob/{blob_sha}.bin", digest(content)
            records["code_documents"].append({
                "file_version_key": version_key, "file_key": file_key, "repository_key": repository_key,
                "path": path, "commit_sha": sha, "language": "Python", "snapshot_at": current["event_at"],
                "availability": "missing" if reason else "available", "exclusion_reason": reason,
                "content_location": content_location, "content_hash": content_hash, "blob_sha": blob_sha,
                "source_location": content_location, "commit_source_location": commit_location,
            })
            records["contributions"].append({
                "developer_key": developer_key, "repository_key": repository_key, "file_key": file_key,
                "file_version_key": version_key, "commit_sha": sha, "event_at": current["event_at"],
                "author_at": current["author_at"], "observed_at": observed, "behavior_type": "commit_author",
                "author_association_status": association, "committer_source_id": str(committer_id) if committer_id is not None else None,
                "evidence_location": url, "source_location": commit_location,
                "github_identity_source_location": api_evidence.get(sha), "git_author_hash": author_hash,
            })
            if item["status"] == "renamed":
                old = item["previous_path"]
                used_paths[old] = identities.file(old)
                records["file_aliases"].append({
                    "file_key": file_key, "previous_file_key": identities.file(old), "old_path": old, "new_path": path,
                    "commit_sha": sha, "available_at": current["event_at"], "evidence_location": url,
                    "source_location": commit_location, "evidence_kind": "parent_tree_unique_identical_blob_move",
                })
    records["developers"], records["files"] = list(developers.values()), list(files.values())
    records, _ = normalize_records(records)
    if not records["contributions"]:
        raise ValueError("No Python file contributions in this selection")
    assets.update(reader.assets)
    assets.update(source.used)
    mapping = {"schema_version": "midterm-a-identity-map-subset-v1", "repository_id": repository["id"],
               "developers": used_developers, "paths": used_paths,
               "rename_conflicts": [r for r in identities.data["rename_conflicts"] if r["commit_sha"] in selected]}
    assets["identity_map.json"] = (json.dumps(mapping, sort_keys=True, indent=2) + "\n").encode()
    selection = {"anchor_sha": anchor, "until": until, "commit_limit": commit_limit, "commit_shas": selected,
                 "order": "git_rev_list_topo_order", "files_per_commit": files_per_commit,
                 "max_code_bytes": max_code_bytes, "merge_policy": "exclude_from_file_authorship"}
    dates = [commit(sha)["event_at"] for sha in selected]
    version = digest(canonical_json([selection, records, sorted((p, digest(b)) for p, b in assets.items())]).encode())[:20]
    manifest = {
        "schema_version": "midterm-a-source-v1", "fixture_only": False,
        "dataset_version": "git-history-" + version, "code_snapshot_version": "git-trees-" + version,
        "dataset_lifecycle": "working", "frozen": False, "team_acceptance": "not_requested",
        "source_kind": "github_history_supplement", "source_versions": {"github_git": selected},
        "created_at": observed, "time_coverage": {"start": min(dates), "end": max(dates)},
        "entity_counts": {kind: len(rows) for kind, rows in records.items()},
        "license": "Historical MIT license objects included for each selected commit; metadata retains GitHub attribution.",
        "access_restrictions": "Raw Git/API evidence contains public author identities; keep data artifacts outside Git.",
        "quality_gaps": ["Working sample; no experiment freeze or team acceptance requested.",
                         "Single anchor ancestry, capped Python changed-file sample, not a full code-authorship history.",
                         "Merge commits retained as context but excluded from file authorship to avoid attributing merged code to the merger.",
                         "Only unique identical-blob moves link renamed paths; edits-with-renames remain separate identities.",
                         "No Issues, historical stars, model, experiment split, or confirmed original CODER corpus membership."],
        "source_provenance": {"adapter_version": "github-git-history-v1", "selection": selection,
                              "repository_source_location": repository_evidence,
                              "remote_url": f"https://github.com/{repository['full_name']}.git",
                              "license_evidence": license_evidence, "tree_snapshots": tree_snapshots,
                              "identity_map_location": "identity_map.json", "excluded_commits": excluded,
                              "missing_api_identity_metadata": missing_metadata,
                              "duplicate_api_commits_removed": duplicates},
    }
    return manifest, records, assets
