"""Evidence-based, unfrozen Issue/PR/file enrichment of a Git history sample."""

from __future__ import annotations

import copy
import gzip
import json
import re
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit

from oss_mentor.collector.github_client import GitHubApiError
from oss_mentor.datasets.github_history import CachedGitHubSource, digest, opaque_key
from oss_mentor.datasets.normalization import canonical_json
from oss_mentor.datasets.snapshot import _utc, load_snapshot


def closing_references(text: str, repository: str) -> set[int]:
    """Candidate discovery only; never enough on its own to label a resolution."""
    found, fenced = set(), False
    pattern = re.compile(r"\b(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?)\s*:?\s*"
                         r"(?:(?P<repo>[\w.-]+/[\w.-]+))?#(?P<number>\d+)\b", re.I)
    url_pattern = re.compile(r"\b(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?)\s*:?\s*"
                             r"https://github\.com/(?P<repo>[\w.-]+/[\w.-]+)/issues/(?P<number>\d+)\b", re.I)
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced or line.lstrip().startswith(">"):
            continue
        line = re.sub(r"`[^`]*`", "", line)
        for pattern_used in (pattern, url_pattern):
            for match in pattern_used.finditer(line):
                if match["repo"] is None or match["repo"].casefold() == repository.casefold():
                    found.add(int(match["number"]))
    return found


class _EmbeddedJSON(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside, self.parts, self.documents = False, [], []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type") == "application/json":
            self.inside, self.parts = True, []

    def handle_data(self, data):
        if self.inside:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.inside:
            self.inside = False
            try:
                self.documents.append(json.loads("".join(self.parts)))
            except ValueError:
                pass


def issue_page_record(html: str, issue_url: str, number: int) -> dict | None:
    """Use the Issue viewer's explicit closing-PR field, never arbitrary page links."""
    parser = _EmbeddedJSON()
    parser.feed(html)
    for document in parser.documents:
        if not isinstance(document, dict):
            continue
        issue = (document.get("payload", {}).get("issueViewerRoute", {}).get("data", {})
                 .get("repository", {}).get("issue"))
        if isinstance(issue, dict) and issue.get("url") == issue_url and issue.get("number") == number:
            return issue
    return None


def get_issue_page(source: CachedGitHubSource, number: int) -> str:
    url = f"https://github.com/{source.repository}/issues/{number}"
    identity = digest(canonical_json(["github_issue_html", url]).encode())
    if identity in source.index:
        entry = source.index[identity]
        path = (source.root / entry["path"]).resolve()
        if not path.is_relative_to(source.root):
            raise ValueError("Issue HTML cache path escapes source root")
        data = path.read_bytes()
        if digest(data) != entry["sha256"]:
            raise ValueError("Issue HTML cache checksum mismatch")
    else:
        if not source.allow_network:
            raise ValueError("Uncached source request; issue page needs --allow-network")
        request = urllib.request.Request(url, headers={"User-Agent": "OSS-Mentor-issue-evidence/0.1"})
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.geturl().rstrip("/") != url:
                raise ValueError("Issue page redirected to another resource")
            html = response.read(8 * 1024 * 1024 + 1)
            if len(html) > 8 * 1024 * 1024:
                raise ValueError("Issue page exceeds collection limit")
            payload = {"html": html.decode("utf-8")}
            envelope = {"metadata": {"source_system": "github_issue_html", "source_url": url,
                                      "status_code": response.status,
                                      "fetched_at": datetime.now(timezone.utc).isoformat(),
                                      "response_sha256": digest(canonical_json(payload).encode())}, "payload": payload}
        data = gzip.compress(canonical_json(envelope).encode(), mtime=0)
        path = source.root / "raw_html" / f"{identity}.json.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
        source.index[identity] = {"path": path.relative_to(source.root).as_posix(), "sha256": digest(data)}
        temporary = source.index_path.with_suffix(".tmp")
        temporary.write_text(canonical_json(source.index), encoding="utf-8")
        temporary.replace(source.index_path)
        source.html_request_count = getattr(source, "html_request_count", 0) + 1
    location = f"provenance_html/{identity}.json.gz"
    source.used[location] = data
    return location


def read_evidence(contents: dict, location: str, expected_url_path: str | None = None) -> dict:
    envelope = json.loads(gzip.decompress(contents[location]))
    metadata = envelope["metadata"]
    url = urlsplit(metadata["source_url"])
    host = "github.com" if metadata["source_system"] == "github_issue_html" else "api.github.com"
    successful = metadata.get("tool_success") is True if metadata["source_system"] == "github_connector" else metadata["status_code"] == 200
    if (url.scheme, url.netloc) != ("https", host) or not successful:
        raise ValueError("Issue evidence origin/status mismatch")
    if expected_url_path is not None and url.path.casefold() != expected_url_path.casefold():
        raise ValueError("Issue evidence points to another endpoint/repository")
    if digest(canonical_json(envelope["payload"]).encode()) != metadata["response_sha256"]:
        raise ValueError("Issue evidence payload checksum mismatch")
    return envelope


def resolution_proof(pr: dict, issue: dict, events: list[dict], page: dict | None,
                     associated_shas: set[str], cutoff: str) -> dict | None:
    """A mention alone, a matching time alone, or a closed PR is insufficient."""
    if not pr.get("merged") or not pr.get("merged_at") or issue.get("state") != "closed" or not issue.get("closed_at"):
        return None
    if issue.get("state_reason") == "not_planned":
        return None
    state_events = [e for e in events if e.get("event") in ("closed", "reopened") and e.get("created_at")]
    if state_events and max(state_events, key=lambda e: _utc(e["created_at"]))["event"] != "closed":
        return None
    closed = [e for e in events if e.get("event") == "closed" and e.get("created_at") and
              _utc(e["created_at"]) == _utc(issue["closed_at"])]
    if not closed or _utc(issue["closed_at"]) > _utc(cutoff):
        return None
    possible = associated_shas | ({pr["merge_commit_sha"]} if pr.get("merge_commit_sha") else set())
    explicit_page = page is not None and page.get("state") == "CLOSED" and any(
        node.get("number") == pr["number"] and node.get("url") == pr["html_url"] and node.get("state") == "MERGED"
        for node in page.get("closedByPullRequestsReferences", {}).get("nodes", []))
    matching = next((e for e in closed if e.get("commit_id") in possible), None)
    if matching is None and not explicit_page:
        return None
    resolved = max(_utc(issue["closed_at"]), _utc(pr["merged_at"]))
    if resolved > _utc(cutoff):
        return None
    return {"proof_kind": "closed_event_commit" if matching else "explicit_github_closed_by_pr",
            "resolved_at": resolved.isoformat(), "closed_event_id": (matching or closed[-1]).get("id")}


def assemble_issue_records(base_records: dict, cases: list, contents: dict, *, repository: str,
                           cutoff: str) -> tuple[list, list, dict]:
    repository_record = base_records["repositories"][0]
    repo_key, repo_id = repository_record["repository_key"], repository_record["source_repository_id"]
    codes = base_records["code_documents"]
    issues, links, confirmed_cases, candidate_issues = {}, {}, [], set()
    selected_with_pr, associated_prs, linked_prs = set(), set(), set()
    for case in cases:
        sha = case["commit_sha"]
        associated = []
        for location in case.get("associated_pr_pages", []):
            associated.extend(read_evidence(contents, location, f"/repos/{repository}/commits/{sha}/pulls")["payload"])
        if associated:
            selected_with_pr.add(sha)
        for item in case.get("pull_requests", []):
            pr_number = item["number"]
            if pr_number not in {pr["number"] for pr in associated}:
                raise ValueError("PR is not associated with this commit in source evidence")
            associated_prs.add(pr_number)
            pr = read_evidence(contents, item["pr_source"], f"/repos/{repository}/pulls/{pr_number}")["payload"]
            if pr["base"]["repo"]["full_name"].casefold() != repository.casefold():
                raise ValueError("Associated PR targets another repository")
            pr_files, pr_commits = [], []
            for location in item.get("file_pages", []):
                pr_files.extend(read_evidence(contents, location, f"/repos/{repository}/pulls/{pr_number}/files")["payload"])
            for location in item.get("commit_pages", []):
                pr_commits.extend(read_evidence(contents, location, f"/repos/{repository}/pulls/{pr_number}/commits")["payload"])
            known_commit_shas = {r["sha"] for r in pr_commits} | {sha}
            paths = {f["filename"] for f in pr_files}
            for entry in item.get("issues", []):
                number = entry["number"]
                candidate_issues.add(number)
                evidence = read_evidence(contents, entry["issue_source"], f"/repos/{repository}/issues/{number}")
                issue = evidence["payload"]
                if "pull_request" in issue or issue["number"] != number:
                    raise ValueError("Referenced number must be an Issue, not a PR")
                events = []
                for location in entry.get("timeline_pages", []):
                    events.extend(read_evidence(contents, location, f"/repos/{repository}/issues/{number}/timeline")["payload"])
                page = None
                if entry.get("page_source"):
                    page_raw = read_evidence(contents, entry["page_source"], f"/{repository}/issues/{number}")
                    page = issue_page_record(page_raw["payload"]["html"], issue["html_url"], number)
                proof = resolution_proof(pr, issue, events, page, known_commit_shas, cutoff)
                issue_key = opaque_key("issue", "github", issue["id"])
                observed = evidence["metadata"]["fetched_at"]
                text_path = f"issue_text/{issue['id']}.json"
                expected_text = (json.dumps({"title": issue["title"], "body": issue.get("body") or ""},
                                            ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
                if text_path in contents and contents[text_path] != expected_text:
                    raise ValueError("Issue text differs from source snapshot")
                outcome = {"final_pr_number": pr_number, "resolved_at": proof["resolved_at"],
                           "resolver_developer_key": None, "label_kind": "explicit_issue_resolution",
                           "proof_kind": proof["proof_kind"]} if proof else None
                row = {"issue_key": issue_key, "source_issue_id": str(issue["id"]), "number": number,
                       "repository_key": repo_key, "text_location": text_path,
                       "labels": [label["name"] for label in issue.get("labels", [])],
                       "created_at": issue["created_at"], "closed_at": issue.get("closed_at"),
                       "observed_at": observed, "state": issue["state"], "current_availability": "excluded",
                       "availability_checked_at": observed, "availability_reason": "historical_dataset_not_online_inventory",
                       "text_observed_at": observed, "historical_text_verified": False,
                       "source_location": entry["issue_source"], "outcome": outcome}
                if issue_key not in issues or outcome is not None:
                    issues[issue_key] = row
                if proof:
                    linked_prs.add(pr_number)
                    confirmed_cases.append({"commit_sha": sha, "pr_number": pr_number, "issue_number": number,
                                            "proof_kind": proof["proof_kind"]})
                    for code in codes:
                        if code["commit_sha"] != sha or code["path"] not in paths:
                            continue
                        available = max(_utc(proof["resolved_at"]), _utc(code["snapshot_at"])).isoformat()
                        link = {"issue_key": issue_key, "file_key": code["file_key"], "evidence_kind": "resolution_pr",
                                "available_at": available, "confidence": None, "confidence_status": "not_calibrated",
                                "source": "github_issue_evidence", "feature_role": "outcome_only",
                                "pr_number": pr_number, "commit_sha": sha, "path": code["path"],
                                "mapping_scope": "pr_changed_files_overlap_sample", "causal_file_attribution_verified": False}
                        links[canonical_json([issue_key, code["file_key"], pr_number, sha])] = link
                # Current body is an observed hint, never backdated to Issue creation.
                body = issue.get("body") or ""
                for code in codes:
                    if re.search(r"(?<![\w./-])" + re.escape(code["path"]) + r"(?![\w./-])", body):
                        link = {"issue_key": issue_key, "file_key": code["file_key"], "evidence_kind": "issue_body_path",
                                "available_at": observed, "confidence": None, "source": "github_issue_evidence",
                                "feature_role": "observed_text_hint", "source_location": entry["issue_source"], "path": code["path"]}
                        links[canonical_json([issue_key, code["file_key"], "observed_text_hint"])] = link
    stats = {"sampled_commits": len(cases), "sampled_commits_with_pr": len(selected_with_pr),
             "associated_unique_prs": len(associated_prs), "candidate_unique_issues": len(candidate_issues),
             "prs_with_explicit_resolution": len(linked_prs), "confirmed_cases": confirmed_cases,
             "resolved_unique_issues": sum(row["outcome"] is not None for row in issues.values()),
             "outcome_file_links": sum(row["feature_role"] == "outcome_only" for row in links.values()),
             "observed_text_hints": sum(row["feature_role"] == "observed_text_hint" for row in links.values())}
    return list(issues.values()), list(links.values()), stats


def enrich_issue_links(directory, source: CachedGitHubSource, *, commit_limit=4) -> tuple[dict, dict, dict]:
    manifest, records, assets = load_snapshot(directory)
    if manifest.get("dataset_lifecycle") != "working" or manifest.get("frozen") is not False:
        raise ValueError("Issue enrichment requires unfrozen working input")
    repository = records["repositories"][0]["full_name"]
    if source.repository != repository:
        raise ValueError("Issue source and input repository differ")
    prefix = "/repos/" + repository
    candidates = []
    for sha in dict.fromkeys(row["commit_sha"] for row in records["contributions"]):
        message = assets[f"git_objects/commit/{sha}.bin"].split(b"\n\n", 1)[1].decode("utf-8", errors="replace")
        candidates.append((sha, message, closing_references(message, repository)))
    positives = [item for item in candidates if item[2]]
    controls = [item for item in candidates if not item[2]]
    selection = positives[:max(0, commit_limit - 1)] + controls[:1]
    cases = []

    def pages(path):
        payload, locations = [], []
        for response, location in source.pages(path, {"per_page": 100}, max_pages=3):
            payload.extend(response.payload)
            locations.append(location)
        return payload, locations

    for sha, message, commit_refs in selection:
        case = {"commit_sha": sha, "selection_role": "closing_keyword_candidate" if commit_refs else "control",
                "associated_pr_pages": [], "pull_requests": [], "errors": []}
        cases.append(case)
        try:
            associated, case["associated_pr_pages"] = pages(prefix + f"/commits/{sha}/pulls")
            for association in associated[:2]:
                number = association["number"]
                pr_response, pr_source = source.get(prefix + f"/pulls/{number}")
                pr = pr_response.payload
                item = {"number": number, "pr_source": pr_source, "issues": []}
                case["pull_requests"].append(item)
                _, item["commit_pages"] = pages(prefix + f"/pulls/{number}/commits")
                _, item["file_pages"] = pages(prefix + f"/pulls/{number}/files")
                for issue_number in sorted(commit_refs | closing_references(pr.get("body") or "", repository))[:3]:
                    issue_response, issue_source = source.get(prefix + f"/issues/{issue_number}")
                    if "pull_request" in issue_response.payload:
                        continue
                    entry = {"number": issue_number, "issue_source": issue_source, "timeline_pages": []}
                    item["issues"].append(entry)
                    _, entry["timeline_pages"] = pages(prefix + f"/issues/{issue_number}/timeline")
                    entry["page_source"] = get_issue_page(source, issue_number)
        except (GitHubApiError, OSError, ValueError) as error:
            if isinstance(error, ValueError) and not str(error).startswith("Uncached source request"):
                raise
            case["errors"].append({"error_type": type(error).__name__, "status_code": getattr(error, "status_code", None)})
    assets.update(source.used)
    # Materialize latest observed text with no claim to historical body versions.
    for case in cases:
        for pr in case["pull_requests"]:
            for entry in pr["issues"]:
                issue = read_evidence(assets, entry["issue_source"])["payload"]
                assets[f"issue_text/{issue['id']}.json"] = (json.dumps(
                    {"title": issue["title"], "body": issue.get("body") or ""}, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    records = copy.deepcopy(records)
    issues, links, stats = assemble_issue_records(records, cases, assets, repository=repository,
                                                  cutoff=manifest["source_provenance"]["selection"]["until"])
    records["issues"], records["issue_file_links"] = issues, links
    result = copy.deepcopy(manifest)
    result.pop("files")
    result["parent_dataset_version"] = result.pop("dataset_version")
    result["source_provenance"]["adapter_version"] = "github-git-issue-history-v1"
    result["source_provenance"]["issue_enrichment"] = {
        "version": "issue-links-v1", "selection_policy": "closing_keyword_priority_plus_control",
        "requested_commit_limit": commit_limit, "base_contribution_commits": len(candidates), "cases": cases, "summary": stats}
    result["entity_counts"] = {kind: len(rows) for kind, rows in records.items()}
    if issues:
        result["time_coverage"]["start"] = min(_utc(value) for value in
            [result["time_coverage"]["start"]] + [row["created_at"] for row in issues]).isoformat()
        result["time_coverage"]["end"] = max(_utc(value) for value in
            [result["time_coverage"]["end"]] + [row["outcome"]["resolved_at"] for row in issues if row["outcome"]]).isoformat()
    if source.used:
        result["created_at"] = max(read_evidence(assets, loc)["metadata"]["fetched_at"] for loc in source.used)
    result["quality_gaps"] = [gap for gap in result["quality_gaps"] if not gap.startswith("No Issues,")]
    result["quality_gaps"] += ["Issue audit is closing-keyword-biased with one control; its rates do not estimate full-repository coverage.",
                               "Latest Issue text/labels are observed now; text hints are not historical pre-resolution features.",
                               "Outcome file links are PR-file overlap evidence, not proof that every linked file caused the fix.",
                               "Resolver identity is not inferred from PR authors or closing actors; missing resolver stays null.",
                               "No historical stars, model, experiment split, or confirmed original CODER corpus membership."]
    result["dataset_version"] = "git-issue-history-" + digest(canonical_json(
        [result, records, sorted((p, digest(b)) for p, b in assets.items())]).encode())[:20]
    return result, records, assets
