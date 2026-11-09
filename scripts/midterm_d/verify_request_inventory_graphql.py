"""Batch-verify GitHub availability for a gated request inventory."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from oss_mentor.candidate_freshness import inventory_assumption
from oss_mentor.candidate_rules import has_beginner_label
from oss_mentor.candidate_sync import repository_maintenance_status
from oss_mentor.gated_scoring import content_hash, issue_key
from oss_mentor.services.profile_service import as_developer_profile_v2
from oss_mentor.services.recommendation_service import RecommendationService
from oss_mentor.storage.candidates import SQLiteCandidateStorage


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _request_bindings(path: Path) -> tuple[dict, dict[str, dict]]:
    request = json.loads(path.read_text(encoding="utf-8"))
    if request.get("feature_schema") != "coder-gated-128+16-v1":
        raise ValueError("unsupported request feature schema")
    candidates = request.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("request has no candidates")
    bindings = {}
    for candidate in candidates:
        key = issue_key(candidate)
        if key in bindings:
            raise ValueError(f"duplicate request candidate: {key}")
        bindings[key] = {
            "repository": candidate["repository"],
            "issue_number": int(candidate["issue_number"]),
            "content_hash": content_hash(candidate),
            "task_candidate_id": int(candidate["task_candidate_id"]),
        }
    return request, bindings


def _database_candidates(
    store: SQLiteCandidateStorage,
    bindings: dict[str, dict],
) -> list[dict]:
    store.initialize()
    with store.connect() as connection:
        rows = connection.execute(
            """
            SELECT tc.*, r.full_name AS repository, r.primary_language,
                   r.maintenance_status
            FROM task_candidate AS tc
            JOIN repository AS r USING(repository_id)
            """
        ).fetchall()
    by_key = {
        f"{str(row['repository']).casefold()}#{int(row['issue_number'])}": dict(row)
        for row in rows
    }
    return [by_key[key] for key in bindings if key in by_key]


def _strict_matching(
    store: SQLiteCandidateStorage,
    profile,
    bindings: dict[str, dict],
) -> tuple[list[dict], list[str], list[str]]:
    with inventory_assumption(False):
        eligible = RecommendationService(store).eligible_candidates(profile)
    eligible_by_key = {issue_key(candidate): candidate for candidate in eligible}
    request_keys = set(bindings)
    request_eligible = [
        candidate for key, candidate in eligible_by_key.items() if key in request_keys
    ]
    uncached = sorted(set(eligible_by_key) - request_keys)
    changed = sorted(
        key
        for key, candidate in eligible_by_key.items()
        if key in request_keys and content_hash(candidate) != bindings[key]["content_hash"]
    )
    return request_eligible, uncached, changed


def _graphql_query(items: list[dict]) -> str:
    fields = []
    for alias, candidate in enumerate(items):
        repository = str(candidate["repository"])
        if repository.count("/") != 1:
            raise ValueError(f"invalid repository: {repository}")
        owner, name = repository.split("/", 1)
        number = int(candidate["issue_number"])
        fields.append(
            "q%d: repository(owner:%s,name:%s) {"
            " isArchived isDisabled pushedAt"
            " issue(number:%d) {"
            " databaseId number state locked title body createdAt updatedAt"
            " assignees(first:1) { totalCount }"
            " comments { totalCount }"
            " labels(first:30) { pageInfo { hasNextPage } nodes { name } }"
            " timelineItems(first:100,itemTypes:[CROSS_REFERENCED_EVENT]) {"
            " pageInfo { hasNextPage }"
            " nodes { __typename ... on CrossReferencedEvent {"
            " source { __typename ... on PullRequest { state url } } } }"
            " }"
            " }"
            "}"
            % (alias, json.dumps(owner), json.dumps(name), number)
        )
    return "query { " + " ".join(fields) + " }"


def _run_graphql(query: str) -> dict:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".json", delete=False
    ) as stream:
        json.dump({"query": query}, stream, ensure_ascii=False)
        request_path = Path(stream.name)
    try:
        result = subprocess.run(
            ["gh", "api", "graphql", "--input", str(request_path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        request_path.unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh api graphql failed: {result.stderr.strip()[:300]}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("gh api graphql returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("gh api graphql returned a non-object")
    return payload


def _availability(
    *,
    expected_content_hash: str,
    repository: dict | None,
    issue: dict | None,
    timeline_errors: bool,
) -> tuple[str, tuple[str, ...], str]:
    if not repository or not issue:
        return "temporarily_unverified", ("issue_not_found_or_transferred",), "unknown"
    if repository.get("isArchived") or repository.get("isDisabled"):
        return "repository_inactive", ("repository_inactive",), "excluded"
    if issue.get("locked"):
        return "locked", ("issue_locked",), "temporarily_ineligible"
    if issue.get("state") != "OPEN":
        return "closed", ("issue_closed",), "excluded"
    if int((issue.get("assignees") or {}).get("totalCount") or 0):
        return "assigned", ("issue_assigned",), "temporarily_ineligible"
    timeline = issue.get("timelineItems") or {}
    if timeline_errors or (timeline.get("pageInfo") or {}).get("hasNextPage"):
        return "temporarily_unverified", ("timeline_incomplete",), "unknown"
    if any(
        ((node.get("source") or {}).get("state") == "OPEN")
        for node in timeline.get("nodes") or []
        if isinstance(node, dict)
    ):
        return "linked_open_pr", ("linked_open_pr",), "temporarily_ineligible"
    if (issue.get("labels") or {}).get("pageInfo", {}).get("hasNextPage"):
        return "temporarily_unverified", ("labels_incomplete",), "unknown"
    if content_hash({
        "title": issue.get("title") or "",
        "body_text": issue.get("body") or "",
    }) != expected_content_hash:
        return "temporarily_unverified", ("content_changed_since_handoff",), "unknown"
    return "available", (), "eligible"


def _update_candidate(
    connection,
    *,
    candidate: dict,
    repository: dict,
    issue: dict,
    availability: str,
    reasons: tuple[str, ...],
    eligibility: str,
    observed_at: datetime,
) -> None:
    now = observed_at.isoformat()
    expiry = (observed_at + timedelta(hours=24)).isoformat()
    pushed_at = repository.get("pushedAt")
    maintenance, _ = repository_maintenance_status(pushed_at, checked_at=observed_at)
    connection.execute(
        """
        UPDATE repository
        SET is_archived = ?, is_disabled = ?, pushed_at = ?,
            maintenance_status = ?, github_verified_at = ?,
            activity_checked_at = ?, updated_at = ?
        WHERE repository_id = ?
        """,
        (
            int(bool(repository.get("isArchived"))),
            int(bool(repository.get("isDisabled"))),
            pushed_at,
            maintenance,
            now,
            now,
            now,
            int(candidate["repository_id"]),
        ),
    )
    labels = [
        str(node.get("name"))
        for node in (issue.get("labels") or {}).get("nodes") or []
        if node and node.get("name")
    ]
    title = str(issue.get("title") or candidate.get("title") or "")
    body = str(issue.get("body") or "")
    connection.execute(
        """
        UPDATE task_candidate SET
            title = ?, body_text = ?, created_at = ?, last_activity_at = ?,
            labels_json = ?, state = ?, assignment_state = ?, is_locked = ?,
            has_linked_open_pr = ?, comment_count = ?, source_system = 'github_rest',
            source_fetched_at = ?, github_verified_at = ?,
            verification_expires_at = ?, candidate_availability = ?,
            availability_reasons_json = ?, candidate_eligibility = ?,
            ineligibility_reasons_json = ?, warnings_json = '[]',
            newcomer_label_signal = ?, normalized_at = ?,
            inventory_review_status = 'online_github_reverified',
            inventory_source = 'github_graphql'
        WHERE task_candidate_id = ?
        """,
        (
            title,
            body,
            issue.get("createdAt") or candidate.get("created_at") or "",
            issue.get("updatedAt") or candidate.get("last_activity_at"),
            json.dumps(labels, ensure_ascii=False),
            "open" if issue.get("state") == "OPEN" else "closed",
            "assigned"
            if int((issue.get("assignees") or {}).get("totalCount") or 0)
            else "unassigned",
            int(bool(issue.get("locked"))),
            int(availability == "linked_open_pr"),
            int((issue.get("comments") or {}).get("totalCount") or 0),
            now,
            now,
            expiry,
            availability,
            json.dumps(reasons, separators=(",", ":")),
            eligibility,
            json.dumps(reasons, separators=(",", ":")),
            int(has_beginner_label(labels)),
            now,
            int(candidate["task_candidate_id"]),
        ),
    )


def run(
    *,
    database: Path,
    request_json: Path,
    target: int,
    batch_size: int,
) -> dict:
    request, bindings = _request_bindings(request_json)
    store = SQLiteCandidateStorage(database, ROOT / "db/sqlite/001_mvp.sql")
    profile = as_developer_profile_v2(request["profile"])
    candidates = _database_candidates(store, bindings)
    if len(candidates) != len(bindings):
        raise ValueError("request candidates are missing from the database")
    order = {key: index for index, key in enumerate(bindings)}
    candidates.sort(key=lambda candidate: order[issue_key(candidate)])

    counts: Counter[str] = Counter()
    errors: list[dict] = []
    started_at = datetime.now(timezone.utc)
    for start in range(0, len(candidates), batch_size):
        request_eligible, _, _ = _strict_matching(store, profile, bindings)
        if len(request_eligible) >= target:
            break
        batch = candidates[start : start + batch_size]
        query = _graphql_query(batch)
        try:
            payload = _run_graphql(query)
        except Exception as exc:
            errors.append({
                "batch": start // batch_size + 1,
                "error_type": type(exc).__name__,
                "error": str(exc)[:300],
            })
            continue
        data = payload.get("data") or {}
        batch_errors = payload.get("errors") or []
        observed_at = datetime.now(timezone.utc)
        with store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for index, candidate in enumerate(batch):
                alias = f"q{index}"
                repository = data.get(alias)
                issue = (repository or {}).get("issue")
                alias_errors = any(
                    error.get("path", [None])[0] == alias
                    for error in batch_errors
                    if isinstance(error, dict)
                )
                availability, reasons, eligibility = _availability(
                    expected_content_hash=bindings[issue_key(candidate)]["content_hash"],
                    repository=repository,
                    issue=issue,
                    timeline_errors=alias_errors,
                )
                counts[availability] += 1
                if repository and issue:
                    _update_candidate(
                        connection,
                        candidate=candidate,
                        repository=repository,
                        issue=issue,
                        availability=availability,
                        reasons=reasons,
                        eligibility=eligibility,
                        observed_at=observed_at,
                    )
                else:
                    connection.execute(
                        """
                        UPDATE task_candidate
                        SET candidate_availability = 'temporarily_unverified',
                            availability_reasons_json = ?,
                            candidate_eligibility = 'unknown',
                            ineligibility_reasons_json = ?,
                            github_verified_at = ?, normalized_at = ?,
                            inventory_review_status = 'online_github_reverify_failed',
                            inventory_source = 'github_graphql'
                        WHERE task_candidate_id = ?
                        """,
                        (
                            json.dumps(reasons, separators=(",", ":")),
                            json.dumps(reasons, separators=(",", ":")),
                            observed_at.isoformat(),
                            observed_at.isoformat(),
                            int(candidate["task_candidate_id"]),
                        ),
                    )

    request_eligible, uncached, changed = _strict_matching(store, profile, bindings)
    cache_compatible = [
        candidate for candidate in request_eligible
        if content_hash(candidate) == bindings[issue_key(candidate)]["content_hash"]
    ]
    report = {
        "status": "passed"
        if len(cache_compatible) >= target and not uncached and not changed
        else "incomplete",
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "request_json": str(request_json),
        "context_key": request.get("context_key"),
        "target_strict_available": target,
        "strict_request_available": len(request_eligible),
        "cache_compatible_available": len(cache_compatible),
        "uncached_eligible_count": len(uncached),
        "uncached_eligible_issue_keys": uncached[:50],
        "changed_content_eligible_count": len(changed),
        "changed_content_eligible_issue_keys": changed[:50],
        "availability_counts": dict(counts),
        "graphql_batches": (len(candidates) + batch_size - 1) // batch_size,
        "errors": errors,
        "inventory_availability_assumed": False,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--request-json", type=Path, required=True)
    parser.add_argument("--target", type=int, default=500)
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-network", action="store_true", required=True)
    args = parser.parse_args()
    report = run(
        database=args.database.resolve(),
        request_json=args.request_json.resolve(),
        target=args.target,
        batch_size=args.batch_size,
    )
    _write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
