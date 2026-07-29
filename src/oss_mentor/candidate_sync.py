"""Fetch, hydrate, classify, and persist current issue candidates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from oss_mentor.candidate_rules import EligibilityResult, evaluate_candidate
from oss_mentor.collector.ecosystems_client import EcosystemsClient
from oss_mentor.collector.github_client import GitHubApiError, GitHubClient
from oss_mentor.collector.source_comparison import (
    normalize_ecosystems_issue,
    normalize_github_issue,
)
from oss_mentor.sqlite_store import SQLiteCandidateStore


REPOSITORY_INACTIVITY_DAYS = 180


def repository_maintenance_status(
    pushed_at: str | None,
    *,
    checked_at: datetime,
) -> tuple[str, str | None]:
    """Classify repository maintenance using the latest repository push."""

    if not pushed_at:
        return ("unknown", "missing_pushed_at")
    try:
        parsed = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
    except ValueError:
        return ("unknown", "invalid_pushed_at")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if checked_at - parsed > timedelta(days=REPOSITORY_INACTIVITY_DAYS):
        return (
            "inactive",
            f"no_repository_push_within_{REPOSITORY_INACTIVITY_DAYS}_days",
        )
    return ("active", None)


@dataclass(frozen=True, slots=True)
class CandidateSyncResult:
    repository: str
    discovered_count: int
    hydrated_count: int
    timeline_checked_count: int
    summary: dict[str, Any]
    unavailable_count: int = 0
    warnings: tuple[dict[str, Any], ...] = ()


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

    def fetch_current_issue(self, full_name: str, issue_number: int) -> dict[str, Any]:
        """Fetch and normalize one current GitHub issue, including linked PR state."""

        github_response = self.github_client.get(
            f"/repos/{full_name}/issues/{issue_number}"
        )
        if not isinstance(github_response.payload, dict):
            raise ValueError(f"GitHub issue response was not an object: {full_name}#{issue_number}")
        payload_number = github_response.payload.get("number")
        repository_url = str(github_response.payload.get("repository_url") or "")
        expected_repository_url = f"https://api.github.com/repos/{full_name}"
        if payload_number != issue_number or (
            repository_url
            and repository_url.rstrip("/").casefold()
            != expected_repository_url.casefold()
        ):
            raise GitHubApiError(
                f"GitHub issue was transferred away from {full_name}#{issue_number}",
                status_code=410,
                url=f"/repos/{full_name}/issues/{issue_number}",
            )
        github_record = self._with_source(
            normalize_github_issue(github_response.payload),
            source_system="github_rest",
            fetched_at=github_response.fetched_at.isoformat(),
        )
        github_record["is_pull_request"] = "pull_request" in github_response.payload
        if (
            not github_record["is_pull_request"]
            and github_record["state"] == "open"
            and github_record["assignment_state"] == "unassigned"
            and not github_record["is_locked"]
        ):
            github_record["has_linked_open_pr"] = self._check_linked_open_pr(
                full_name, issue_number
            )
        return github_record

    def fetch_repository_health(self, full_name: str) -> dict[str, Any]:
        response = self.github_client.get(f"/repos/{full_name}")
        if not isinstance(response.payload, dict):
            raise ValueError(f"GitHub repository response was not an object: {full_name}")
        pushed_at = response.payload.get("pushed_at")
        maintenance_status, maintenance_reason = repository_maintenance_status(
            pushed_at,
            checked_at=response.fetched_at,
        )
        return {
            "github_repository_id": response.payload.get("id"),
            "html_url": response.payload.get("html_url") or f"https://github.com/{full_name}",
            "github_verified_at": response.fetched_at.isoformat(),
            "is_archived": bool(response.payload.get("archived")),
            "is_disabled": bool(response.payload.get("disabled")),
            "pushed_at": pushed_at,
            "maintenance_status": maintenance_status,
            "maintenance_reason": maintenance_reason,
            "activity_checked_at": response.fetched_at.isoformat(),
        }

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
        repository_health = self.fetch_repository_health(full_name)
        if (
            repository_health["is_archived"]
            or repository_health["is_disabled"]
            or repository_health["maintenance_status"] == "inactive"
        ):
            with self.store.connect() as connection:
                self.store.upsert_repository(
                    connection,
                    full_name=full_name,
                    ecosystems_last_synced_at=None,
                    ecosystem=ecosystem,
                    primary_language=primary_language,
                    mark_synced=True,
                    **repository_health,
                )
            return CandidateSyncResult(
                repository=full_name,
                discovered_count=0,
                hydrated_count=0,
                timeline_checked_count=0,
                summary=self.store.summary(),
            )
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
        github_discovery_responses = (
            [
                self.github_client.get(
                    f"/repos/{full_name}/issues",
                    params={
                        "state": "open",
                        "labels": label,
                        "sort": "updated",
                        "direction": "desc",
                        "per_page": limit,
                    },
                )
                for label in candidate_labels
            ]
            if hydrate_github
            else []
        )
        repository_payload = (
            eco_repository_response.payload
            if isinstance(eco_repository_response.payload, dict)
            else {}
        )
        discovered_by_number: dict[int, tuple[dict[str, Any], str, str]] = {}
        discovery_batches = [
            *((response, "github_rest") for response in github_discovery_responses),
            *((response, "ecosystems") for response in issue_responses),
        ]
        for response, source_system in discovery_batches:
            if not isinstance(response.payload, list):
                continue
            for item in response.payload:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("number"), int)
                    or "pull_request" in item
                ):
                    continue
                discovered_by_number.setdefault(
                    item["number"],
                    (item, source_system, response.fetched_at.isoformat()),
                )
        discovered = list(discovered_by_number.values())[:limit]
        hydrated_count = 0
        timeline_checked_count = 0
        unavailable_count = 0
        warnings: list[dict[str, Any]] = []
        normalized_records: list[tuple[dict[str, Any], EligibilityResult]] = []

        for raw_issue, discovery_source, discovery_fetched_at in discovered:
            discovery_record = self._with_source(
                (
                    normalize_github_issue(raw_issue)
                    if discovery_source == "github_rest"
                    else normalize_ecosystems_issue(raw_issue)
                ),
                source_system=discovery_source,
                fetched_at=discovery_fetched_at,
            )
            if not hydrate_github:
                normalized_records.append(
                    (discovery_record, evaluate_candidate(discovery_record))
                )
                continue
            issue_number = discovery_record["issue_number"]
            try:
                github_record = self.fetch_current_issue(full_name, issue_number)
            except GitHubApiError as exc:
                if exc.status_code not in {404, 410}:
                    raise
                unavailable_count += 1
                discovery_eligibility = evaluate_candidate(discovery_record)
                unavailable_record = {
                    **discovery_record,
                    "github_verified_at": datetime.now(timezone.utc).isoformat(),
                }
                normalized_records.append(
                    (
                        unavailable_record,
                        EligibilityResult(
                            eligibility="excluded",
                            reasons=("github_unavailable",),
                            warnings=(),
                            newcomer_label_signal=(
                                discovery_eligibility.newcomer_label_signal
                            ),
                        ),
                    )
                )
                warnings.append(
                    {
                        "repository": full_name,
                        "issue_number": issue_number,
                        "reason": "github_unavailable",
                        "status_code": exc.status_code,
                    }
                )
                continue
            if (
                not github_record["is_pull_request"]
                and github_record["state"] == "open"
                and github_record["assignment_state"] == "unassigned"
                and not github_record["is_locked"]
            ):
                timeline_checked_count += 1
            normalized_records.extend(
                (
                    (discovery_record, evaluate_candidate(discovery_record)),
                    (github_record, evaluate_candidate(github_record)),
                )
            )
            hydrated_count += 1

        with self.store.connect() as connection:
            repository_id = self.store.upsert_repository(
                connection,
                full_name=full_name,
                ecosystems_last_synced_at=repository_payload.get("last_synced_at"),
                ecosystem=ecosystem,
                primary_language=primary_language,
                mark_synced=True,
                **repository_health,
            )
            for record, eligibility in normalized_records:
                self.store.upsert_candidate(
                    connection,
                    repository_id=repository_id,
                    record=record,
                    eligibility=eligibility,
                )

        return CandidateSyncResult(
            repository=full_name,
            discovered_count=len(discovered),
            hydrated_count=hydrated_count,
            timeline_checked_count=timeline_checked_count,
            summary=self.store.summary(),
            unavailable_count=unavailable_count,
            warnings=tuple(warnings),
        )
