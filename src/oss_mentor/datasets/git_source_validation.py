"""Verify portable Git-object evidence without a local Git executable or network."""

import gzip
import hashlib
import json
from datetime import datetime
from urllib.parse import urlsplit

from oss_mentor.datasets.git_objects import changed_files, flatten_tree, object_hash, parse_commit
from oss_mentor.datasets.normalization import canonical_json


def validate_git_history(manifest: dict, records: dict, contents: dict[str, bytes], *, allow_issue_enrichment=False) -> None:
    provenance = manifest["source_provenance"]
    selection = provenance["selection"]
    if manifest.get("dataset_lifecycle") != "working" or manifest.get("frozen") is not False:
        raise ValueError("This collector produces working data; experiment freeze is a separate action")

    def raw(location):
        envelope = json.loads(gzip.decompress(contents[location]))
        metadata = envelope["metadata"]
        url = urlsplit(metadata["source_url"])
        if (url.scheme, url.netloc) != ("https", "api.github.com") or metadata["status_code"] != 200:
            raise ValueError("GitHub identity evidence has wrong origin/status")
        if hashlib.sha256(canonical_json(envelope["payload"]).encode()).hexdigest() != metadata["response_sha256"]:
            raise ValueError("GitHub identity evidence checksum mismatch")
        return envelope["payload"]

    seen_objects = {}

    def read_object(kind, sha):
        key = (kind, sha)
        if key not in seen_objects:
            content = contents[f"git_objects/{kind}/{sha}.bin"]
            if object_hash(kind, content) != sha:
                raise ValueError("Portable Git object hash mismatch")
            seen_objects[key] = content
        return seen_objects[key]

    commits, trees = {}, {}

    def commit(sha):
        if sha not in commits:
            commits[sha] = parse_commit(read_object("commit", sha))
        return commits[sha]

    def tree(sha):
        if sha not in trees:
            trees[sha] = flatten_tree(sha, read_object)
        return trees[sha]

    repository = raw(provenance["repository_source_location"])
    if len(records["repositories"]) != 1 or (not allow_issue_enrichment and (records["issues"] or records["issue_file_links"])):
        raise ValueError("Working Git history expects one repository and no Issue data")
    normalized_repo = records["repositories"][0]
    if str(repository["id"]) != normalized_repo["source_repository_id"] or repository["full_name"] != normalized_repo["full_name"]:
        raise ValueError("Git history repository identity mismatch")
    mapping = json.loads(contents[provenance["identity_map_location"]])
    if mapping["repository_id"] != repository["id"]:
        raise ValueError("Persistent identity map belongs to another repository")
    selected = selection["commit_shas"]
    if len(set(selected)) != len(selected) or len(selected) > selection["commit_limit"]:
        raise ValueError("Git selection contains duplicate/excess commits")
    reachable, pending = set(), [selection["anchor_sha"]]
    while pending:
        sha = pending.pop()
        if sha in reachable or f"git_objects/commit/{sha}.bin" not in contents:
            continue
        reachable.add(sha)
        pending.extend(commit(sha)["parents"])
    if not set(selected).issubset(reachable):
        raise ValueError("Selected Git commit is not reachable from anchor evidence")
    changes = {}
    expected_paths = set()
    cutoff = datetime.fromisoformat(selection["until"].replace("Z", "+00:00"))
    for sha in selected:
        current = commit(sha)
        if datetime.fromisoformat(current["event_at"]) > cutoff:
            raise ValueError("Git object exceeds historical cutoff")
        if len(current["parents"]) > 1:
            changes[sha] = []
            continue
        before = tree(commit(current["parents"][0])["tree"]) if current["parents"] else {}
        changes[sha] = changed_files(before, tree(current["tree"]))
        expected_paths.update((sha, c["path"]) for c in [c for c in changes[sha] if c["path"].endswith(".py")][:selection["files_per_commit"]])
    if expected_paths != {(c["commit_sha"], c["path"]) for c in records["code_documents"]}:
        raise ValueError("Selected Python files differ from actual parent/tree changes")
    if set(selected) != {r["commit_sha"] for r in provenance["tree_snapshots"]}:
        raise ValueError("Selected commits require complete tree snapshots")
    for evidence in provenance["tree_snapshots"]:
        sha = evidence["commit_sha"]
        if evidence["tree_sha"] != commit(sha)["tree"] or json.loads(contents[evidence["content_location"]]) != tree(evidence["tree_sha"]):
            raise ValueError("Historical tree index differs from Git objects")
    if set(selected) != {r["commit_sha"] for r in provenance["license_evidence"]}:
        raise ValueError("Each selected Git commit requires a historical license")
    for evidence in provenance["license_evidence"]:
        entries = tree(commit(evidence["commit_sha"])["tree"])
        if not any(p in entries and entries[p]["sha"] == evidence["blob_sha"] for p in ("LICENSE", "LICENSE.txt", "LICENSE.rst")):
            raise ValueError("Historical license is absent from the pinned tree")
        data = read_object("blob", evidence["blob_sha"])
        if evidence["spdx_id"] != "MIT" or contents[evidence["content_location"]] != data or b"Permission is hereby granted, free of charge" not in data:
            raise ValueError("Historical MIT license mismatch")
    codes = {c["file_version_key"]: c for c in records["code_documents"]}
    developers = {d["developer_key"]: d for d in records["developers"]}
    for code in codes.values():
        current = commit(code["commit_sha"])
        if code["commit_source_location"] != f"git_objects/commit/{code['commit_sha']}.bin":
            raise ValueError("Code commit evidence location mismatch")
        if mapping["paths"][code["path"]] != code["file_key"] or code["snapshot_at"] != current["event_at"]:
            raise ValueError("File identity or snapshot time differs from Git evidence")
        if code["availability"] == "available":
            entry = tree(current["tree"])[code["path"]]
            if entry["sha"] != code["blob_sha"] or read_object("blob", entry["sha"]) != contents[code["content_location"]]:
                raise ValueError("Code content differs from pinned Git blob")
    expected_actors = set()
    for row in records["contributions"]:
        sha = row["commit_sha"]
        current = commit(sha)
        if row["source_location"] != f"git_objects/commit/{sha}.bin":
            raise ValueError("Contribution commit evidence location mismatch")
        if len(current["parents"]) > 1:
            raise ValueError("Merge code cannot be attributed as file authorship")
        if codes[row["file_version_key"]]["commit_sha"] != sha or row["event_at"] != current["event_at"] or row["author_at"] != current["author_at"]:
            raise ValueError("Contribution differs from Git commit/time")
        author_hash = hashlib.sha256(current["author_identity"]).hexdigest()
        if row["git_author_hash"] != author_hash:
            raise ValueError("Author/committer identity confusion in Git evidence")
        developer = developers[row["developer_key"]]
        metadata_location = row["github_identity_source_location"]
        api = None
        if metadata_location is not None:
            payload = raw(metadata_location)
            api = next((item for item in payload if item["sha"] == sha), None)
            if api is None or api["commit"]["tree"]["sha"] != current["tree"]:
                raise ValueError("GitHub identity metadata does not match Git commit")
        numeric_id = (api.get("author") or {}).get("id") if api else None
        committer_id = (api.get("committer") or {}).get("id") if api else None
        if row["committer_source_id"] != (str(committer_id) if committer_id is not None else None):
            raise ValueError("Committer ID differs from direct GitHub evidence")
        if developer["github_numeric_id"] != numeric_id:
            raise ValueError("GitHub author identity differs from direct API evidence")
        scope = f"github:{numeric_id}" if numeric_id is not None else f"git-author:{author_hash}"
        if mapping["developers"][scope] != row["developer_key"]:
            raise ValueError("Developer key differs from persistent source mapping")
        if (numeric_id is None) != (developer["identity_status"] == "unconfirmed"):
            raise ValueError("Unconfirmed source identity must remain explicit")
        expected_actors.add((sha, row["developer_key"]))
    if {(r["commit_sha"], r["developer_key"]) for r in records["project_behaviors"]} != expected_actors:
        raise ValueError("Project contribution behaviors differ from file evidence")
    for row in records["file_aliases"]:
        if not any(c["status"] == "renamed" and c["path"] == row["new_path"] and c["previous_path"] == row["old_path"]
                   for c in changes[row["commit_sha"]]):
            raise ValueError("Rename lacks unique identical-blob parent/tree evidence")
