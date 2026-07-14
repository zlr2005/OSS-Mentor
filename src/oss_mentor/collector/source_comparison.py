"""Compare Ecosyste.ms issue metadata with GitHub's source-of-truth API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from oss_mentor.collector.ecosystems_client import EcosystemsClient
from oss_mentor.collector.github_client import GitHubClient


COMMON_FIELDS = (
    "github_issue_id",
    "issue_number",
    "html_url",
    "created_at",
    "author_association",
    "title",
    "labels",
    "state",
    "assignment_state",
    "comment_count",
    "last_activity_at",
)

GITHUB_ONLY_FIELDS = (
    "body_text",
    "has_linked_open_pr",
    "participant_count",
    "has_reproduction_steps",
    "has_acceptance_criteria",
    "has_expected_behavior",
    "has_affected_module_hint",
)


def _labels(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            result.append(item["name"])
    return sorted(result, key=str.casefold)


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return parsed.astimezone(timezone.utc).isoformat()


def normalize_github_issue(issue: dict[str, Any]) -> dict[str, Any]:
    assignees = issue.get("assignees") or []
    return {
        "github_issue_id": issue.get("id"),
        "issue_number": issue.get("number"),
        "html_url": issue.get("html_url"),
        "created_at": _timestamp(issue.get("created_at")),
        "author_association": issue.get("author_association"),
        "title": issue.get("title"),
        "labels": _labels(issue.get("labels")),
        "state": issue.get("state"),
        "assignment_state": "assigned" if assignees else "unassigned",
        "is_locked": bool(issue.get("locked")),
        "comment_count": issue.get("comments"),
        "last_activity_at": _timestamp(issue.get("updated_at")),
        "body_text": issue.get("body"),
    }


def normalize_ecosystems_issue(issue: dict[str, Any]) -> dict[str, Any]:
    assignees = issue.get("assignees") or []
    return {
        # Ecosyste.ms `uuid` is its own integer record key, not GitHub's `id`.
        # Keep this absent so callers hydrate the authoritative numeric ID.
        "github_issue_id": None,
        "issue_number": issue.get("number"),
        "html_url": issue.get("html_url"),
        "created_at": _timestamp(issue.get("created_at")),
        "author_association": issue.get("author_association"),
        "title": issue.get("title"),
        "labels": _labels(issue.get("labels")),
        "state": issue.get("state"),
        "assignment_state": "assigned" if assignees else "unassigned",
        "is_locked": bool(issue.get("locked")),
        "comment_count": issue.get("comments_count", issue.get("comments")),
        "last_activity_at": _timestamp(issue.get("updated_at")),
    }


def _present(record: dict[str, Any], field: str) -> bool:
    return field in record and record[field] is not None


@dataclass(frozen=True, slots=True)
class SourceComparison:
    report: dict[str, Any]


class IssueSourceComparator:
    def __init__(
        self, ecosystems_client: EcosystemsClient, github_client: GitHubClient
    ) -> None:
        self.ecosystems_client = ecosystems_client
        self.github_client = github_client

    def compare(self, full_name: str, *, sample_size: int = 10) -> SourceComparison:
        eco_repository = self.ecosystems_client.get_repository(full_name)
        eco_response = self.ecosystems_client.get_issues(
            full_name, per_page=sample_size, pull_request=False
        )
        github_response = self.github_client.get(
            f"/repos/{full_name}/issues",
            params={
                "state": "open",
                "sort": "updated",
                "direction": "desc",
                "per_page": min(max(sample_size * 2, sample_size), 100),
            },
        )
        eco_payload = eco_response.payload if isinstance(eco_response.payload, list) else []
        github_payload = (
            github_response.payload if isinstance(github_response.payload, list) else []
        )
        github_payload = [
            item
            for item in github_payload
            if isinstance(item, dict) and "pull_request" not in item
        ][:sample_size]
        eco_records = [
            normalize_ecosystems_issue(item)
            for item in eco_payload
            if isinstance(item, dict)
        ]
        github_records = [normalize_github_issue(item) for item in github_payload]
        eco_by_number = {item["issue_number"]: item for item in eco_records}
        github_by_number = {item["issue_number"]: item for item in github_records}
        matched_numbers = sorted(set(eco_by_number).intersection(github_by_number))

        agreements: dict[str, dict[str, int]] = {}
        for field in COMMON_FIELDS:
            comparable = 0
            equal = 0
            for number in matched_numbers:
                left = eco_by_number[number].get(field)
                right = github_by_number[number].get(field)
                if left is None or right is None:
                    continue
                comparable += 1
                equal += left == right
            agreements[field] = {"equal": equal, "comparable": comparable}

        def coverage(records: list[dict[str, Any]], fields: tuple[str, ...]) -> dict[str, Any]:
            return {
                field: {
                    "present": sum(_present(record, field) for record in records),
                    "sampled": len(records),
                }
                for field in fields
            }

        repository_payload = (
            eco_repository.payload if isinstance(eco_repository.payload, dict) else {}
        )
        report = {
            "schema_version": "source-comparison-v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "repository": full_name,
            "requested_sample_size": sample_size,
            "ecosystems": {
                "repository_updated_at": repository_payload.get("updated_at"),
                "repository_last_synced_at": repository_payload.get("last_synced_at"),
                "fetched_at": eco_response.fetched_at.isoformat(),
                "sample_count": len(eco_records),
                "coverage": coverage(eco_records, COMMON_FIELDS + GITHUB_ONLY_FIELDS),
            },
            "github": {
                "fetched_at": github_response.fetched_at.isoformat(),
                "sample_count": len(github_records),
                "rate_limit_remaining": github_response.headers.get(
                    "x-ratelimit-remaining"
                ),
                "coverage": coverage(github_records, COMMON_FIELDS + GITHUB_ONLY_FIELDS),
            },
            "matched_issue_numbers": matched_numbers,
            "matched_count": len(matched_numbers),
            "field_agreement": agreements,
            "conclusion": {
                "ecosystems_role": "candidate_index_and_historical_metadata",
                "github_role": "current_text_and_detail_hydration",
                "ecosystems_missing_by_design": list(GITHUB_ONLY_FIELDS),
            },
        }
        return SourceComparison(report=report)
