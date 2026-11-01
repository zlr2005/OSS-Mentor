"""Small, provenance-preserving GitHub history supplements for CODER data work.

This collects data only. It neither implements CODER nor claims membership of
the unavailable original experimental corpus. Network access is opt-in.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit

from oss_mentor.collector.github_client import GitHubClient, GitHubResponse, parse_link_header
from oss_mentor.collector.raw_store import RawStore
from oss_mentor.datasets.normalization import IDENTITY_FIELDS, canonical_json, normalize_records
from oss_mentor.datasets.snapshot import _utc, validate_snapshot
from oss_mentor.datasets.code_languages import code_language, LICENSE_PATHS
from oss_mentor.datasets.path_tree import collect_paths


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def opaque_key(kind: str, *values: object) -> str:
    return f"{kind}-" + digest(canonical_json(values).encode("utf-8"))


def decode_blob(payload: dict) -> bytes:
    if payload["encoding"] != "base64":
        raise ValueError("Unsupported Git blob encoding")
    content = base64.b64decode("".join(payload["content"].split()), validate=True)
    git_hash = hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()
    if git_hash != payload["sha"] or len(content) != payload["size"]:
        raise ValueError("Git blob hash/size mismatch")
    return content


class CachedGitHubSource:
    """Reuse verified raw envelopes and persist each successful request immediately."""

    def __init__(self, client: GitHubClient, directory: Path, repository: str, *, allow_network: bool):
        self.client, self.root, self.repository = client, directory.resolve(), repository
        self.allow_network = allow_network
        self.index_path = self.root / "requests.json"
        self.index = json.loads(self.index_path.read_text(encoding="utf-8")) if self.index_path.exists() else {}
        self.used: dict[str, bytes] = {}
        self.raw_store = RawStore(self.root / "raw", api_version=client.api_version)
        self.run_id = uuid4()

    def get(self, path: str, params: dict | None = None) -> tuple[GitHubResponse, str]:
        identity = digest(canonical_json([self.client.api_base, self.client.api_version, path, params]).encode())
        if identity in self.index:
            entry = self.index[identity]
            location = (self.root / entry["path"]).resolve()
            if not location.is_relative_to(self.root):
                raise ValueError("Source cache path escapes root")
            raw = location.read_bytes()
            if digest(raw) != entry["sha256"]:
                raise ValueError("Source cache checksum mismatch")
            envelope = json.loads(gzip.decompress(raw))
            metadata = envelope["metadata"]
            response = GitHubResponse(
                url=metadata["source_url"], status_code=metadata["status_code"] if metadata["status_code"] is not None else 200,
                headers=metadata["headers"], payload=envelope["payload"],
                fetched_at=datetime.fromisoformat(metadata["fetched_at"]),
            )
        else:
            if not self.allow_network:
                raise ValueError(f"Uncached source request; use --allow-network to collect: {path}")
            response = self.client.get(path, params=params)
            saved = self.raw_store.save(response, endpoint_name="midterm_history",
                                        repository_full_name=self.repository, collection_run_id=self.run_id)
            raw = saved.path.read_bytes()
            self.index[identity] = {"path": saved.path.relative_to(self.root).as_posix(), "sha256": digest(raw)}
            temporary = self.index_path.with_suffix(".tmp")
            temporary.write_text(canonical_json(self.index), encoding="utf-8")
            temporary.replace(self.index_path)
        asset_path = f"provenance/{identity}.json.gz"
        self.used[asset_path] = raw
        return response, asset_path

    def pages(self, path: str, params: dict, *, max_pages: int = 4):
        """Bounded, cached pagination; refuse cross-origin next links and cycles."""
        seen = set()
        for _ in range(max_pages):
            identity = canonical_json([path, params])
            if identity in seen:
                raise ValueError("Cyclic source pagination")
            seen.add(identity)
            response, evidence = self.get(path, params)
            yield response, evidence
            next_url = parse_link_header(response.headers.get("link")).get("next")
            if next_url is None:
                return
            parsed, allowed = urlsplit(next_url), urlsplit(self.client.api_base)
            if (parsed.scheme, parsed.netloc) != (allowed.scheme, allowed.netloc):
                raise ValueError("Cross-origin source pagination")
            path, params = next_url, None


def collect_history(source: CachedGitHubSource, *, until: str, commit_limit: int = 3,
                    files_per_commit: int = 2, max_code_bytes: int = 262144,
                    multilingual: bool = False, repair: bool = False) -> tuple[dict, dict, dict]:
    """Return manifest, normalized records and bytes; all code uses pinned Git blobs."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source.repository):
        raise ValueError("Repository must be owner/name")
    if not 1 <= commit_limit <= 10 or not 1 <= files_per_commit <= 10:
        raise ValueError("Small-sample limits must be between 1 and 10")
    cutoff = _utc(until)
    if cutoff is None:
        raise ValueError("Historical cutoff is required")
    prefix = f"/repos/{source.repository}"
    response, repository_evidence = source.get(prefix)
    repository = response.payload
    if repository.get("private") is not False:
        raise ValueError("Only public repositories are supported")
    declared_license = (repository.get('license') or {}).get('spdx_id')
    if repair and not multilingual:
        raise ValueError('Repair requires multilingual mode')
    if multilingual and not repair and declared_license in {None, '', 'NOASSERTION'}:
        raise ValueError('Repository license declaration unavailable')
    if not multilingual and declared_license != "MIT":
        raise ValueError("This first adapter supports repositories with verified MIT license text only")
    repository_key = opaque_key("repository", "github", repository["id"])
    listed, _ = source.get(prefix + "/commits", {"until": until, "per_page": 100 if repair else commit_limit})
    if not listed.payload:
        raise ValueError("No historical commits returned")
    commits, licenses, trees = [], [], []
    excluded_commits = []
    assets: dict[str, bytes] = {}
    for item in listed.payload:
        commit_response, commit_evidence = source.get(prefix + "/commits/" + item["sha"], {"per_page": 100})
        commit = commit_response.payload
        if commit["sha"] != item["sha"] or _utc(commit["commit"]["committer"]["date"]) > cutoff:
            raise ValueError("Commit differs from selected historical SHA/cutoff")
        tree_sha = commit["commit"]["tree"]["sha"]
        selected = [row for row in commit.get('files',[]) if
                    (code_language(row['filename']) if multilingual else row['filename'].endswith('.py'))][:files_per_commit]
        if repair:
            if not selected:continue
            entries, tree = collect_paths(source,prefix,tree_sha,[row['filename'] for row in selected])
            tree_evidence = tree['tree_evidence'][tree_sha]
            license_entry = next((entries[p] for p in tree['license_paths']),None)
        else:
            tree_response, tree_evidence = source.get(prefix + "/git/trees/" + tree_sha, {"recursive": 1})
            tree = tree_response.payload
            if tree["sha"] != tree_sha or tree.get("truncated") is not False:
                raise ValueError("A complete, matching historical tree is required")
            entries = {row["path"]: row for row in tree["tree"]}
            license_entry = next((entries[p] for p in (LICENSE_PATHS if multilingual else ("LICENSE", "LICENSE.txt", "LICENSE.rst")) if p in entries), None)
        if repair and license_entry is None:
            excluded_path=f'trees/excluded-{commit["sha"]}.json'
            assets[excluded_path]=(json.dumps(tree,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
            excluded_commits.append({'commit_sha':commit['sha'],'reason':'license_file_not_found_in_bounded_search',
                                     'commit_source_location':commit_evidence,'tree_proof_location':excluded_path})
            continue
        if license_entry is None or license_entry["type"] != "blob":
            raise ValueError("Historical license file not found")
        license_response, license_raw = source.get(prefix + "/git/blobs/" + license_entry["sha"])
        license_content = decode_blob(license_response.payload)
        if not license_content.strip() or (not multilingual and b"Permission is hereby granted, free of charge" not in license_content):
            raise ValueError("Historical license text does not match supported MIT grant")
        license_path = f"licenses/{commit['sha']}.txt"
        assets[license_path] = license_content
        licenses.append({"spdx_id": declared_license, "commit_sha": commit["sha"], "content_location": license_path,
                         "source_location": license_raw, "source_url": license_response.url})
        tree_path = f"trees/{commit['sha']}.json"
        assets[tree_path] = (json.dumps(tree, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        trees.append({"commit_sha": commit["sha"], "tree_sha": tree_sha,
                      "content_location": tree_path, "source_location": tree_evidence})
        selected = [row for row in commit.get("files", []) if
                    (code_language(row['filename']) if multilingual else row["filename"].endswith(".py"))][:files_per_commit]
        commits.append((commit_response, commit_evidence, entries, selected))
        if repair and len(commits)>=commit_limit:break

    if not commits:
        raise ValueError('No recognized source contributions in the bounded commit window')

    # Union paths only when GitHub supplies an explicit rename relationship.
    parents: dict[str, str] = {}

    def find(path: str) -> str:
        parents.setdefault(path, path)
        if parents[path] != path:
            parents[path] = find(parents[path])
        return parents[path]

    for _, _, _, selected in commits:
        for item in selected:
            path = item["filename"]
            find(path)
            if item["status"] == "renamed":
                a, b = find(path), find(item["previous_filename"])
                parents[max(a, b)] = min(a, b)

    records = {kind: [] for kind in IDENTITY_FIELDS}
    guide = next((path for path in ("CONTRIBUTING.rst", "CONTRIBUTING.md", ".github/CONTRIBUTING.md")
                  if path in commits[0][2]), None)
    records["repositories"].append({
        "repository_key": repository_key, "source_repository_id": str(repository["id"]),
        "full_name": repository["full_name"], "primary_language": repository.get("language"),
        "domains": [], "is_archived": repository["archived"], "maintenance_status": "unknown",
        "contribution_url": (repository["html_url"] + "/blob/" + commits[0][0].payload["sha"] + "/" + guide
                             if guide else repository["html_url"] + "/contribute"),
        "observed_at": response.fetched_at.isoformat(), "source_location": repository_evidence,
    })
    developer_rows, file_rows, times = {}, {}, []
    for commit_response, evidence, entries, selected in commits:
        commit = commit_response.payload
        sha = commit["sha"]
        observed = commit_response.fetched_at.isoformat()
        event = commit["commit"]["committer"]["date"]
        times.append(event)
        author = commit.get("author")
        numeric_id = author.get("id") if isinstance(author, dict) else None
        associated = isinstance(numeric_id, int) and not isinstance(numeric_id, bool)
        developer_key = opaque_key("developer", "github", numeric_id) if associated else opaque_key("developer", repository["id"], sha)
        developer_rows[developer_key] = {
            "developer_key": developer_key, "source": "github" if associated else "git_author_unconfirmed",
            "source_developer_id": str(numeric_id) if associated else f"commit-author:{repository['id']}:{sha}",
            "github_numeric_id": numeric_id, "identity_status": "verified" if associated else "unconfirmed",
            "identity_evidence_location": evidence,
        }
        if selected:
            records["project_behaviors"].append({
                "developer_key": developer_key, "repository_key": repository_key, "behavior_type": "contribution",
                "event_at": event, "event_time_unknown": False, "observed_at": observed, "source": "github_rest",
                "commit_sha": sha, "evidence_location": commit["html_url"], "source_location": evidence,
            })
        for item in selected:
            path = item["filename"]
            file_key = opaque_key("file", repository["id"], find(path))
            version_key = opaque_key("file-version", repository["id"], sha, path)
            file_rows[file_key] = {"file_key": file_key, "repository_key": repository_key}
            content_path, content_hash, blob_sha, blob_evidence = None, None, None, None
            reason = None
            entry = entries.get(path)
            if item["status"] == "removed":
                reason = "file_deleted_at_commit"
            elif not entry or entry["type"] != "blob" or entry["mode"] not in ("100644", "100755"):
                reason = "not_a_regular_blob_in_historical_tree"
            elif entry.get("size", max_code_bytes + 1) > max_code_bytes:
                reason = "code_size_limit"
            else:
                blob_sha = entry["sha"]
                blob_response, blob_evidence = source.get(prefix + "/git/blobs/" + blob_sha)
                content = decode_blob(blob_response.payload)
                if blob_response.payload["sha"] != blob_sha:
                    raise ValueError("Blob differs from pinned tree")
                try:
                    content.decode("utf-8")
                except UnicodeDecodeError:
                    reason = "non_utf8_source"
                else:
                    content_path = f"code/{sha}/{digest(path.encode())}.txt"
                    assets[content_path] = content
                    content_hash = digest(content)
            records["code_documents"].append({
                "file_version_key": version_key, "file_key": file_key, "repository_key": repository_key,
                "path": path, "commit_sha": sha, "language": code_language(path) if multilingual else "Python", "snapshot_at": event,
                "availability": "missing" if reason else "available", "exclusion_reason": reason,
                "content_location": content_path, "content_hash": content_hash, "blob_sha": blob_sha,
                "source_location": blob_evidence, "commit_source_location": evidence,
            })
            committer = commit.get("committer") or {}
            records["contributions"].append({
                "developer_key": developer_key, "repository_key": repository_key, "file_key": file_key,
                "file_version_key": version_key, "commit_sha": sha, "event_at": event,
                "author_at": commit["commit"]["author"]["date"], "observed_at": observed,
                "behavior_type": "commit_author", "author_association_status": "verified" if associated else "unconfirmed",
                "committer_source_id": str(committer["id"]) if committer.get("id") is not None else None,
                "evidence_location": commit["html_url"], "source_location": evidence,
            })
            if item["status"] == "renamed":
                records["file_aliases"].append({
                    "file_key": file_key, "old_path": item["previous_filename"], "new_path": path,
                    "commit_sha": sha, "available_at": event, "evidence_location": commit["html_url"],
                    "source_location": evidence,
                })
    records["developers"] = list(developer_rows.values())
    records["files"] = list(file_rows.values())
    records, _ = normalize_records(records)
    if not records["contributions"]:
        raise ValueError("No selected source file contributions; expand the sampling window")
    assets.update(source.used)
    selection = {"repository": source.repository, "until": until, "commit_limit": commit_limit,
                 "files_per_commit": files_per_commit, "max_code_bytes": max_code_bytes,
                 "commit_shas": [c[0].payload["sha"] for c in commits]}
    manifest = {
        "schema_version": "midterm-a-source-v1", "fixture_only": False,
        "dataset_version": "github-history-" + digest(canonical_json([selection, records, sorted((k, digest(v)) for k, v in assets.items())]).encode())[:20],
        "code_snapshot_version": "git-pinned-" + digest(canonical_json(selection["commit_shas"]).encode())[:20],
        "source_kind": "github_history_supplement", "source_versions": {"github": selection["commit_shas"]},
        "created_at": max(json.loads(gzip.decompress(raw))["metadata"]["fetched_at"] for raw in source.used.values()),
        "time_coverage": {"start": min(times), "end": max(times)},
        "entity_counts": {kind: len(rows) for kind, rows in records.items()},
        "license": "MIT (historical license files included); GitHub metadata is retained with source attribution.",
        "access_restrictions": "Public GitHub source; raw envelopes may contain public author emails. Keep raw artifacts outside Git.",
        "quality_gaps": [
            "Small default-branch history sample; not a full corpus or confirmed CODER original repository list.",
            "No historical stars/watchers/forks, Issue outcomes, or Issue-file mappings acquired.",
            "At most the first 100 changed paths per commit and selected Python files only; not complete author code ownership.",
            "Current repository metadata/identity observations must not be treated as historical model features.",
            "event_at uses the commit committer timestamp; contribution attribution uses the author; author_at is retained separately.",
            "No graph, frozen experiment split, embeddings, model, or experimental result is included.",
        ],
        "source_provenance": {"adapter_version": "github-history-v1", "selection": selection,
                              "repository_source_location": repository_evidence,
                              "license_evidence": licenses, "tree_snapshots": trees},
    }
    if multilingual:
        manifest['source_provenance']['adapter_version'] = 'github-history-v2'
        manifest['source_provenance']['license_basis'] = 'Current repository SPDX declaration; pinned historical text retained without asserting historical license classification.'
        manifest['license'] = 'Repository-declared ' + str(declared_license or 'unknown') + '; pinned historical license text retained. No redistribution authorization is inferred.'
        manifest['quality_gaps'] = [text.replace('Python files only', 'recognized source files only') for text in manifest['quality_gaps']]
        manifest['quality_gaps'].append('Historical license text is verified against the pinned blob; SPDX classification is the current repository declaration.')
    if repair:
        manifest['source_provenance']['adapter_version']='github-history-v3'
        manifest['source_provenance']['selection']['scanned_commit_limit']=100
        manifest['source_provenance']['selection']['excluded_commits']=excluded_commits
        manifest['source_provenance']['tree_coverage']='selected_paths_only'
        manifest['quality_gaps'].append('Only selected paths and root license files are proven by nonrecursive tree chains; no full-tree coverage claim. Unknown/NOASSERTION SPDX declarations are preserved.')
        manifest['quality_gaps'].append('Source-code commits without a license file in the bounded path search are excluded with retained commit/tree evidence; the sample is conditional on evidence availability.')
    return manifest, records, assets


def write_snapshot(target: Path, manifest: dict, records: dict, assets: dict[str, bytes]) -> dict:
    """Publish a new validated snapshot atomically; never overwrite existing data."""
    target = target.resolve()
    if target.exists():
        raise ValueError("Snapshot destination already exists; use a new directory")
    assets = dict(assets)
    assets["records.json"] = (json.dumps(records, ensure_ascii=False, indent=2) + "\n").encode()
    manifest = dict(manifest)
    manifest["files"] = [{"path": name, "format": "git-object" if name.endswith(".bin") else "gzip-json" if name.endswith(".gz") else "json" if name.endswith(".json") else "text",
                          "size_bytes": len(data), "sha256": digest(data)} for name, data in sorted(assets.items())]
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".history-", dir=target.parent) as temporary:
        staging = Path(temporary) / "snapshot"
        staging.mkdir()
        for name, data in assets.items():
            path = (staging / name).resolve()
            if not path.is_relative_to(staging):
                raise ValueError("Snapshot asset escapes directory")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (staging / "manifest.json").write_bytes((json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode())
        report = validate_snapshot(staging)
        staging.rename(target)
    return report
