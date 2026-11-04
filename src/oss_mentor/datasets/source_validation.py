"""Local provenance verification for the first GitHub history adapter.

Checks cached source evidence; does not claim cryptographic server attestation,
current online freshness, or authenticity for arbitrary user-supplied packages.
"""

import base64
import gzip
import hashlib
import json
from datetime import datetime
from urllib.parse import urlsplit

from oss_mentor.datasets.normalization import canonical_json
from oss_mentor.datasets.code_languages import LICENSE_PATHS, code_language
from oss_mentor.datasets.path_tree import verify_paths, license_name


def validate_github_history(manifest: dict, records: dict, contents: dict[str, bytes]) -> None:
    if manifest["source_kind"] != "github_history_supplement":
        raise ValueError("Unsupported real source kind")
    provenance = manifest["source_provenance"]
    if provenance["adapter_version"] == "github-git-issue-history-v1":
        from oss_mentor.datasets.git_source_validation import validate_git_history
        from oss_mentor.datasets.issue_links import assemble_issue_records
        validate_git_history(manifest, records, contents, allow_issue_enrichment=True)
        audit = provenance["issue_enrichment"]
        issues, links, summary = assemble_issue_records(
            records, audit["cases"], contents, repository=records["repositories"][0]["full_name"],
            cutoff=provenance["selection"]["until"])
        if (canonical_json(issues) != canonical_json(records["issues"]) or
                canonical_json(links) != canonical_json(records["issue_file_links"]) or
                canonical_json(summary) != canonical_json(audit["summary"])):
            raise ValueError("Issue records/links/audit differ from source evidence")
        return
    if provenance["adapter_version"] == "github-git-history-v1":
        from oss_mentor.datasets.git_source_validation import validate_git_history
        validate_git_history(manifest, records, contents)
        return
    if provenance["adapter_version"] not in {"github-history-v1", "github-history-v2", "github-history-v3"}:
        raise ValueError("Unsupported source adapter version")
    repair = provenance['adapter_version'] == 'github-history-v3'
    multilingual = provenance['adapter_version'] in {'github-history-v2','github-history-v3'}

    def raw(location: str) -> dict:
        envelope = json.loads(gzip.decompress(contents[location]))
        metadata = envelope["metadata"]
        url = urlsplit(metadata["source_url"])
        if (url.scheme, url.netloc) != ("https", "api.github.com") or metadata["status_code"] != 200:
            raise ValueError("Source evidence must be a successful public GitHub API response")
        if hashlib.sha256(canonical_json(envelope["payload"]).encode()).hexdigest() != metadata["response_sha256"]:
            raise ValueError("Source payload checksum mismatch")
        return envelope

    def blob(location: str) -> bytes:
        payload = raw(location)["payload"]
        if payload["encoding"] != "base64":
            raise ValueError("Unsupported source blob encoding")
        data = base64.b64decode("".join(payload["content"].split()), validate=True)
        if len(data) != payload["size"] or hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest() != payload["sha"]:
            raise ValueError("Source blob hash/size mismatch")
        return data

    if len(records["repositories"]) != 1 or records["issues"] or records["issue_file_links"]:
        raise ValueError("History adapter expects one repository and no invented Issue data")
    repository = raw(provenance["repository_source_location"])["payload"]
    row = records["repositories"][0]
    if str(repository["id"]) != row["source_repository_id"] or repository["full_name"] != row["full_name"]:
        raise ValueError("Repository identity differs from source")
    if repository["private"] is not False:
        raise ValueError("Real source repository must be public")
    expected_prefix = f"/repos/{repository['full_name']}/"
    if repair:
        for excluded in provenance['selection'].get('excluded_commits',[]):
            envelope=raw(excluded['commit_source_location'])
            commit=envelope['payload'];sha=excluded['commit_sha']
            if urlsplit(envelope['metadata']['source_url']).path.casefold() != (expected_prefix+'commits/'+sha).casefold() or commit['sha']!=sha:
                raise ValueError('Excluded commit identity differs from source')
            proof=json.loads(contents[excluded['tree_proof_location']])
            if proof['sha']!=commit['commit']['tree']['sha'] or proof['license_paths'] or excluded['reason']!='license_file_not_found_in_bounded_search':
                raise ValueError('Excluded license search differs from evidence')
            verify_paths(proof,raw,expected_prefix)
    developers = {r["developer_key"]: r for r in records["developers"]}
    codes = {r["file_version_key"]: r for r in records["code_documents"]}
    trees = {}
    licenses = set()
    for item in provenance["license_evidence"]:
        data = blob(item["source_location"])
        expected_license = (repository.get('license') or {}).get('spdx_id') if multilingual else 'MIT'
        if (not repair and expected_license in {None, '', 'NOASSERTION'}) or item["spdx_id"] != expected_license or data != contents[item["content_location"]]:
            raise ValueError("Historical license differs from source")
        if not data.strip() or (not multilingual and b"Permission is hereby granted, free of charge" not in data):
            raise ValueError("Unsupported historical license grant")
        licenses.add(item["commit_sha"])
    for item in provenance["tree_snapshots"]:
        if repair:
            proof=json.loads(contents[item['content_location']])
            if proof['sha']!=item['tree_sha']:raise ValueError('Pinned path proof SHA differs')
            trees[item['commit_sha']]=verify_paths(proof,raw,expected_prefix)
            continue
        tree = raw(item["source_location"])["payload"]
        if tree["sha"] != item["tree_sha"] or tree["truncated"] is not False:
            raise ValueError("Historical tree is incomplete or mismatched")
        if tree != json.loads(contents[item["content_location"]]):
            raise ValueError("Tree snapshot differs from source")
        trees[item["commit_sha"]] = tree
    if set(provenance["selection"]["commit_shas"]) != set(trees) or set(trees) != licenses:
        raise ValueError("Every selected commit needs tree and license evidence")
    for item in provenance["license_evidence"]:
        license_sha = raw(item["source_location"])["payload"]["sha"]
        if not any((license_name(f['path']) if repair else f["path"] in (LICENSE_PATHS if multilingual else ("LICENSE", "LICENSE.txt", "LICENSE.rst"))) and f["sha"] == license_sha
                   for f in trees[item["commit_sha"]]["tree"]):
            raise ValueError("License blob is not in the historical tree")

    def commit_evidence(location: str, sha: str) -> dict:
        envelope = raw(location)
        source_url = urlsplit(envelope["metadata"]["source_url"])
        if source_url.path.casefold() != (expected_prefix + "commits/" + sha).casefold():
            raise ValueError("Commit evidence belongs to another repository/SHA")
        commit = envelope["payload"]
        if commit["sha"] != sha or commit["commit"]["tree"]["sha"] != trees[sha]["sha"]:
            raise ValueError("Commit differs from historical tree")
        event = datetime.fromisoformat(commit["commit"]["committer"]["date"].replace("Z", "+00:00"))
        cutoff = datetime.fromisoformat(provenance["selection"]["until"].replace("Z", "+00:00"))
        if event > cutoff:
            raise ValueError("Source commit exceeds the historical cutoff")
        return commit

    for code in records["code_documents"]:
        if multilingual and (not code_language(code['path']) or code['language'] != code_language(code['path'])):
            raise ValueError('Source language does not match supported file extension')
        sha = code["commit_sha"]
        commit = commit_evidence(code["commit_source_location"], sha)
        change = next((f for f in commit["files"] if f["filename"] == code["path"]), None)
        if change is None:
            raise ValueError("Code path is not a changed file in source commit")
        if code["snapshot_at"] != commit["commit"]["committer"]["date"]:
            raise ValueError("Historical code time differs from commit")
        if code["availability"] == "available":
            tree_entry = next((f for f in trees[sha]["tree"] if f["path"] == code["path"]), None)
            if tree_entry is None or tree_entry["sha"] != code["blob_sha"]:
                raise ValueError("Code blob is not in pinned historical tree")
            if raw(code["source_location"])["payload"]["sha"] != code["blob_sha"]:
                raise ValueError("Code evidence differs from pinned blob")
            if blob(code["source_location"]) != contents[code["content_location"]]:
                raise ValueError("Code content differs from source blob")
    for contribution in records["contributions"]:
        commit = commit_evidence(contribution["source_location"], contribution["commit_sha"])
        code = codes[contribution["file_version_key"]]
        if code["commit_source_location"] != contribution["source_location"]:
            raise ValueError("Contribution and code use different commit evidence")
        author = commit.get("author") or {}
        developer = developers[contribution["developer_key"]]
        if developer["github_numeric_id"] != author.get("id"):
            raise ValueError("Contribution author differs from GitHub-linked author")
        if contribution["event_at"] != commit["commit"]["committer"]["date"]:
            raise ValueError("Contribution time differs from source commit")
        if contribution["author_at"] != commit["commit"]["author"]["date"]:
            raise ValueError("Author time differs from source commit")
        if contribution["evidence_location"] != commit["html_url"]:
            raise ValueError("Contribution URL differs from source commit")
        committer = commit.get("committer") or {}
        committer_id = str(committer["id"]) if committer.get("id") is not None else None
        if contribution["committer_source_id"] != committer_id:
            raise ValueError("Committer identity differs from source")
    for behavior in records["project_behaviors"]:
        commit = commit_evidence(behavior["source_location"], behavior["commit_sha"])
        developer = developers[behavior["developer_key"]]
        if behavior["behavior_type"] != "contribution" or developer["github_numeric_id"] != (commit.get("author") or {}).get("id"):
            raise ValueError("Project behavior differs from source contribution")
        if behavior["event_at"] != commit["commit"]["committer"]["date"]:
            raise ValueError("Project behavior time differs from source")
    for alias in records["file_aliases"]:
        commit = commit_evidence(alias["source_location"], alias["commit_sha"])
        if not any(f["status"] == "renamed" and f["filename"] == alias["new_path"] and
                   f["previous_filename"] == alias["old_path"] for f in commit["files"]):
            raise ValueError("File rename lacks source evidence")
