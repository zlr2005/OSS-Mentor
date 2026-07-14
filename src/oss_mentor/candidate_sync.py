"""Fetch, hydrate, classify, and persist current issue candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from oss_mentor.candidate_rules import evaluate_candidate
from oss_mentor.collector.ecosystems_client import EcosystemsClient
from oss_mentor.collector.github_client import GitHubClient
from oss_mentor.collector.source_comparison import (
    normalize_ecosystems_issue,
    normalize_github_issue,
)
from oss_mentor.sqlite_store import SQLiteCandidateStore


@dataclass(frozen=True, slots=True)
class CandidateSyncResult:
    repository: str
    discovered_count: int
    hydrated_count: int
    timeline_checked_count: int
    summary: dict[str, Any]


class CandidateSynchronizer:
    def __init__(
        self,
        ecosystems_client: EcosystemsClient,
        github_client: GitHubClient,
        store: SQLiteCandidateStore,
    ) -> None:
        self.ecosystems_client = ecosystems_client
        self.github_client = github_client
        self.store = store

    @staticmethod
    def _with_source(
        record: dict[str, Any], *, source_system: str, fetched_at: str
    ) -> dict[str, Any]:
        return {
            **record,
            "source_system": source_system,
            "source_fetched_at": fetched_at,
            "github_verified_at": fetched_at if source_system == "github_rest" else None,
            "is_pull_request": False,
            "has_linked_open_pr": None,
        }

    @staticmethod
    def _has_linked_open_pr(events: list[Any]) -> bool:
        for event in events:
            if not isinstance(event, dict) or event.get("event") != "cross-referenced":
                continue
            source = event.get("source")
            issue = source.get("issue") if isinstance(source, dict) else None
            if not isinstance(issue, dict):
                continue
            if "pull_request" in issue and issue.get("state") == "open":
                return True
        return False

    def _check_linked_open_pr(self, full_name: str, issue_number: int) -> bool:
        events: list[Any] = []
        for response in self.github_client.iter_pages(
            f"/repos/{full_name}/issues/{issue_number}/timeline",
            params={"per_page": 100},
        ):
            if isinstance(response.payload, list):
                events.extend(response.payload)
        return self._has_linked_open_pr(events)

    def sync(
        self,
        full_name: str,
        *,
        limit: int = 20,
        hydrate_github: bool = True,
        candidate_labels: tuple[str, ...] = (),
        ecosystem: str | None = None,
        primary_language: str | None = None,
    ) -> CandidateSyncResult:
        self.store.initialize()
        eco_repository_response = self.ecosystems_client.get_repository(full_name)
        issue_responses = [
            self.ecosystems_client.get_issues(
                full_name,
                per_page=limit,
                pull_request=False,
                label=label,
            )
            for label in candidate_labels
        ]
        issue_responses.append(
            self.ecosystems_client.get_issues(
                full_name, per_page=limit, pull_request=False
            )
        )
        repository_payload = (
            eco_repository_response.payload
            if isinstance(eco_repository_response.payload, dict)
            else {}
        )
        discovered_by_number: dict[int, dict[str, Any]] = {}
        for response in issue_responses:
            if not isinstance(response.payload, list):
                continue
            for item in response.payload:
                if not isinstance(item, dict) or not isinstance(item.get("number"), int):
                    continue
                discovered_by_number.setdefault(item["number"], item)
        discovered = list(discovered_by_number.values())[:limit]
        discovery_fetched_at = max(
            response.fetched_at for response in issue_responses
        ).isoformat()
        hydrated_count = 0
        timeline_checked_count = 0

        with self.store.connect() as connection:
            repository_id = self.store.upsert_repository(
                connection,
                full_name=full_name,
                github_repository_id=None,
                html_url=f"https://github.com/{full_name}",
                ecosystems_last_synced_at=repository_payload.get("last_synced_at"),
                ecosystem=ecosystem,
                primary_language=primary_language,
            )
            for raw_issue in discovered:
                eco_record = self._with_source(
                    normalize_ecosystems_issue(raw_issue),
                    source_system="ecosystems",
                    fetched_at=discovery_fetched_at,
                )
                self.store.upsert_candidate(
                    connection,
                    repository_id=repository_id,
                    record=eco_record,
                    eligibility=evaluate_candidate(eco_record),
                )

                if not hydrate_github:
                    continue
                issue_number = eco_record["issue_number"]
                github_response = self.github_client.get(
                    f"/repos/{full_name}/issues/{issue_number}"
                )
                if not isinstance(github_response.payload, dict):
                    continue
                github_record = self._with_source(
                    normalize_github_issue(github_response.payload),
                    source_system="github_rest",
                    fetched_at=github_response.fetched_at.isoformat(),
                )
                github_record["is_pull_request"] = (
                    "pull_request" in github_response.payload
                )
                if (
                    not github_record["is_pull_request"]
                    and github_record["state"] == "open"
                    and github_record["assignment_state"] == "unassigned"
                    and not github_record["is_locked"]
                ):
                    github_record["has_linked_open_pr"] = self._check_linked_open_pr(
                        full_name, issue_number
                    )
                    timeline_checked_count += 1
                self.store.upsert_candidate(
                    connection,
                    repository_id=repository_id,
                    record=github_record,
                    eligibility=evaluate_candidate(github_record),
                )
                hydrated_count += 1

        return CandidateSyncResult(
            repository=full_name,
            discovered_count=len(discovered),
            hydrated_count=hydrated_count,
            timeline_checked_count=timeline_checked_count,
            summary=self.store.summary(),
        )
