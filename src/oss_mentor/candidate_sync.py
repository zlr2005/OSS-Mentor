"""Fetch, hydrate, classify, and persist current issue candidates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Iterator

from oss_mentor.candidate_rules import EligibilityResult, evaluate_candidate
from oss_mentor.collector.ecosystems_client import EcosystemsApiError, EcosystemsClient
from oss_mentor.collector.github_client import (
    GitHubApiError,
    GitHubClient,
    RateLimitExceeded,
)
from oss_mentor.collector.source_comparison import (
    normalize_ecosystems_issue,
    normalize_github_issue,
)
from oss_mentor.storage.candidates import CandidateStorage, as_candidate_storage


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
    not_modified: bool = False
    request_count: int = 0
    retry_count: int = 0
    rate_limit_remaining: int | None = None
    rate_limit_reset_at: str | None = None
    sync_cursor: str | None = None
    etag: str | None = None
    last_modified: str | None = None


class CandidateSynchronizer:
    def __init__(
        self,
        ecosystems_client: EcosystemsClient,
        github_client: GitHubClient,
        store: CandidateStorage,
    ) -> None:
        self.ecosystems_client = ecosystems_client
        self.github_client = github_client
        self.store = as_candidate_storage(store)
        self._repository_updated_at: dict[str, str | None] = {}

    def _github_get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        """Call real and lightweight test clients without weakening production use."""

        try:
            return self.github_client.get(
                path,
                params=params,
                extra_headers=extra_headers,
            )
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc):
                raise
            try:
                return self.github_client.get(path, params=params)
            except TypeError as fallback_exc:
                if "unexpected keyword argument" not in str(fallback_exc):
                    raise
                return self.github_client.get(path)

    def _github_pages(
        self,
        path: str,
        *,
        params: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> Iterator[Any]:
        pager = getattr(self.github_client, "iter_pages", None)
        if not callable(pager):
            yield self._github_get(
                path,
                params=params,
                extra_headers=extra_headers,
            )
            return
        try:
            yield from pager(
                path,
                params=params,
                extra_headers=extra_headers,
            )
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc):
                raise
            yield from pager(path, params=params)

    @staticmethod
    def _response_headers(response: Any) -> dict[str, str]:
        headers = getattr(response, "headers", None)
        if not isinstance(headers, dict):
            return {}
        return {str(key).casefold(): str(value) for key, value in headers.items()}

    def _client_metrics(self) -> tuple[int, int, int | None, str | None]:
        reset_at = getattr(self.github_client, "rate_limit_reset_at", None)
        if isinstance(reset_at, datetime):
            reset_value = reset_at.isoformat()
        else:
            reset_value = str(reset_at) if reset_at else None
        return (
            int(getattr(self.github_client, "request_count", 0) or 0),
            int(getattr(self.github_client, "retry_count", 0) or 0),
            getattr(self.github_client, "rate_limit_remaining", None),
            reset_value,
        )

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

        github_response = self._github_get(
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
        response = self._github_get(f"/repos/{full_name}")
        if not isinstance(response.payload, dict):
            raise ValueError(f"GitHub repository response was not an object: {full_name}")
        pushed_at = response.payload.get("pushed_at")
        maintenance_status, maintenance_reason = repository_maintenance_status(
            pushed_at,
            checked_at=response.fetched_at,
        )
        self._repository_updated_at[full_name] = response.payload.get("updated_at")
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
        request_start, retry_start, _, _ = self._client_metrics()
        checkpoint_reader = getattr(self.store, "get_sync_checkpoint", None)
        checkpoint = (
            checkpoint_reader(full_name) if callable(checkpoint_reader) else {}
        )
        repository_health = self.fetch_repository_health(full_name)
        github_updated_at = self._repository_updated_at.get(full_name)
        repository_values = {
            "full_name": full_name,
            "ecosystems_last_synced_at": None,
            "ecosystem": ecosystem,
            "primary_language": primary_language,
            "mark_synced": True,
            **repository_health,
        }
        if (
            repository_health["is_archived"]
            or repository_health["is_disabled"]
            or repository_health["maintenance_status"] == "inactive"
        ):
            self.store.save_repository_health(
                repository=repository_values,
                checkpoint={
                    "cursor": checkpoint.get("cursor"),
                    "etag": checkpoint.get("etag"),
                    "last_modified": checkpoint.get("last_modified"),
                    "github_updated_at": github_updated_at,
                },
            )
            request_end, retry_end, remaining, reset_at = self._client_metrics()
            return CandidateSyncResult(
                repository=full_name,
                discovered_count=0,
                hydrated_count=0,
                timeline_checked_count=0,
                summary=self.store.summary(),
                request_count=request_end - request_start,
                retry_count=retry_end - retry_start,
                rate_limit_remaining=remaining,
                rate_limit_reset_at=reset_at,
            )

        github_discovery_responses: list[Any] = []
        canonical_items: list[tuple[dict[str, Any], str]] = []
        canonical_response: Any = None
        if hydrate_github:
            conditional_headers: dict[str, str] = {}
            incremental_cursor = checkpoint.get("cursor")
            if not incremental_cursor and checkpoint.get("etag"):
                conditional_headers["If-None-Match"] = str(checkpoint["etag"])
            elif not incremental_cursor and checkpoint.get("last_modified"):
                conditional_headers["If-Modified-Since"] = str(
                    checkpoint["last_modified"]
                )
            canonical_params: dict[str, Any] = {
                "state": "all",
                "sort": "updated",
                "direction": "asc" if incremental_cursor else "desc",
                "per_page": limit,
            }
            if incremental_cursor:
                canonical_params["since"] = str(incremental_cursor)
                canonical_responses = self._github_pages(
                    f"/repos/{full_name}/issues",
                    params=canonical_params,
                )
            else:
                canonical_responses = iter(
                    [
                        self._github_get(
                            f"/repos/{full_name}/issues",
                            params=canonical_params,
                            extra_headers=conditional_headers or None,
                        )
                    ]
                )

            boundary_updated_at: str | None = None
            for response in canonical_responses:
                if canonical_response is None:
                    canonical_response = response
                if int(getattr(response, "status_code", 200)) == 304:
                    break
                if not isinstance(response.payload, list):
                    continue
                stop_after_page = False
                for item in response.payload:
                    if not isinstance(item, dict):
                        continue
                    updated_at = str(
                        item.get("updated_at")
                        or response.fetched_at.isoformat()
                    )
                    if len(canonical_items) < limit:
                        canonical_items.append(
                            (item, response.fetched_at.isoformat())
                        )
                        boundary_updated_at = updated_at
                    elif updated_at == boundary_updated_at:
                        canonical_items.append(
                            (item, response.fetched_at.isoformat())
                        )
                    else:
                        stop_after_page = True
                        break
                if stop_after_page or not incremental_cursor:
                    break

            if canonical_response is None:
                raise ValueError(
                    f"GitHub issue pagination returned no response: {full_name}"
                )
            if int(getattr(canonical_response, "status_code", 200)) == 304:
                headers = self._response_headers(canonical_response)
                self.store.save_repository_health(
                    repository=repository_values,
                    checkpoint={
                        "cursor": checkpoint.get("cursor"),
                        "etag": headers.get("etag") or checkpoint.get("etag"),
                        "last_modified": (
                            headers.get("last-modified")
                            or checkpoint.get("last_modified")
                        ),
                        "github_updated_at": github_updated_at,
                    },
                )
                request_end, retry_end, remaining, reset_at = self._client_metrics()
                return CandidateSyncResult(
                    repository=full_name,
                    discovered_count=0,
                    hydrated_count=0,
                    timeline_checked_count=0,
                    summary=self.store.summary(),
                    not_modified=True,
                    request_count=request_end - request_start,
                    retry_count=retry_end - retry_start,
                    rate_limit_remaining=remaining,
                    rate_limit_reset_at=reset_at,
                    sync_cursor=checkpoint.get("cursor"),
                    etag=headers.get("etag") or checkpoint.get("etag"),
                    last_modified=(
                        headers.get("last-modified")
                        or checkpoint.get("last_modified")
                    ),
                )
            for label in candidate_labels:
                label_params: dict[str, Any] = {
                    "state": "open",
                    "labels": label,
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": limit,
                }
                if checkpoint.get("cursor"):
                    label_params["since"] = str(checkpoint["cursor"])
                github_discovery_responses.append(
                    self._github_get(
                        f"/repos/{full_name}/issues",
                        params=label_params,
                    )
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
        repository_payload = (
            eco_repository_response.payload
            if isinstance(eco_repository_response.payload, dict)
            else {}
        )
        discovered_by_number: dict[int, tuple[dict[str, Any], str, str]] = {}
        for item, fetched_at in canonical_items:
            if (
                isinstance(item.get("number"), int)
                and "pull_request" not in item
            ):
                discovered_by_number.setdefault(
                    int(item["number"]),
                    (item, "github_rest", fetched_at),
                )
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
        discovered_limit = max(limit, len(canonical_items))
        discovered = list(discovered_by_number.values())[:discovered_limit]
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

        cursor_values = [
            str(item.get("updated_at"))
            for item, _ in canonical_items
            if item.get("updated_at")
        ]
        sync_cursor = max(cursor_values, default=checkpoint.get("cursor"))
        response_headers = self._response_headers(canonical_response)
        etag = response_headers.get("etag") or checkpoint.get("etag")
        last_modified = (
            response_headers.get("last-modified") or checkpoint.get("last_modified")
        )

        repository_values["ecosystems_last_synced_at"] = repository_payload.get(
            "last_synced_at"
        )
        self.store.save_repository_candidates(
            repository=repository_values,
            candidates=normalized_records,
            checkpoint={
                "cursor": sync_cursor,
                "etag": etag,
                "last_modified": last_modified,
                "github_updated_at": github_updated_at,
            },
        )

        request_end, retry_end, remaining, reset_at = self._client_metrics()
        return CandidateSyncResult(
            repository=full_name,
            discovered_count=len(discovered),
            hydrated_count=hydrated_count,
            timeline_checked_count=timeline_checked_count,
            summary=self.store.summary(),
            unavailable_count=unavailable_count,
            warnings=tuple(warnings),
            request_count=request_end - request_start,
            retry_count=retry_end - retry_start,
            rate_limit_remaining=remaining,
            rate_limit_reset_at=reset_at,
            sync_cursor=sync_cursor,
            etag=etag,
            last_modified=last_modified,
        )


@dataclass(frozen=True, slots=True)
class SyncBatchResult:
    sync_run_id: int
    status: str
    repository_count: int
    request_count: int
    success_count: int
    failure_count: int
    skipped_count: int
    retry_count: int
    rate_limit_remaining: int | None
    rate_limit_reset_at: str | None
    repositories: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class RefreshBatchResult:
    sync_run_id: int
    status: str
    selected_count: int
    refreshed_count: int
    unavailable_count: int
    failed_count: int
    timeline_checked_count: int
    rate_limited: bool
    repositories_refreshed: tuple[str, ...]
    errors: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class CandidateDetail:
    task_candidate_id: int
    repository_full_name: str
    issue_number: int
    title: str
    body_text: str | None
    html_url: str
    availability: str
    availability_reasons: tuple[str, ...]
    verified_at: str | None
    refreshed: bool


def _error_fields(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, RateLimitExceeded):
        return ("rate_limited", "GitHub rate limit exhausted")
    if isinstance(exc, GitHubApiError):
        status = exc.status_code
        return (
            f"github_http_{status}" if status else "github_upstream_error",
            f"GitHub request failed{f' with HTTP {status}' if status else ''}",
        )
    if isinstance(exc, EcosystemsApiError):
        status = exc.status_code
        return (
            f"ecosystems_http_{status}" if status else "ecosystems_upstream_error",
            f"Ecosyste.ms request failed{f' with HTTP {status}' if status else ''}",
        )
    if isinstance(exc, (TimeoutError, OSError)):
        return ("network_error", type(exc).__name__)
    return ("sync_error", type(exc).__name__)


class CandidateService:
    """Logical v0.5 service contract owned by the candidate data feature."""

    def __init__(
        self,
        *,
        synchronizer: CandidateSynchronizer,
        store: CandidateStorage,
        repositories: Iterable[Any],
    ) -> None:
        self.synchronizer = synchronizer
        self.store = as_candidate_storage(store)
        self.repositories = tuple(
            repository
            for repository in repositories
            if bool(getattr(repository, "enabled", True))
        )

    def sync_enabled_repositories(
        self,
        *,
        limit_per_repository: int,
        requested_by: str,
    ) -> SyncBatchResult:
        if not 1 <= limit_per_repository <= 100:
            raise ValueError("limit_per_repository must be between 1 and 100")
        if not requested_by.strip():
            raise ValueError("requested_by must not be empty")

        started_at = datetime.now(timezone.utc).isoformat()
        sync_run_id = self.store.create_sync_run(
            requested_by=requested_by,
            limit_per_repository=limit_per_repository,
            repository_count=len(self.repositories),
            started_at=started_at,
        )
        repository_results: list[dict[str, Any]] = []
        success_count = 0
        failure_count = 0
        skipped_count = 0
        request_count = 0
        retry_count = 0
        rate_limited = False

        for repository in self.repositories:
            full_name = str(repository.full_name)
            repository_started_at = datetime.now(timezone.utc).isoformat()
            before_requests, before_retries, _, _ = (
                self.synchronizer._client_metrics()
            )
            if rate_limited:
                skipped_count += 1
                result = {
                    "repository": full_name,
                    "status": "skipped",
                    "error_code": "rate_limited",
                }
                repository_results.append(result)
                self.store.record_repository_result(
                    sync_run_id=sync_run_id,
                    repository_full_name=full_name,
                    status="skipped",
                    started_at=repository_started_at,
                    skipped_count=1,
                    error_code="rate_limited",
                    error_summary="Skipped after an earlier rate-limit response",
                )
                continue

            try:
                synchronized = self.synchronizer.sync(
                    full_name,
                    limit=limit_per_repository,
                    hydrate_github=True,
                    candidate_labels=tuple(
                        getattr(repository, "candidate_labels", ()) or ()
                    ),
                    ecosystem=getattr(repository, "ecosystem", None),
                    primary_language=getattr(repository, "primary_language", None),
                )
            except (
                GitHubApiError,
                EcosystemsApiError,
                OSError,
                TimeoutError,
                ValueError,
            ) as exc:
                after_requests, after_retries, remaining, reset_at = (
                    self.synchronizer._client_metrics()
                )
                repository_request_count = max(0, after_requests - before_requests)
                repository_retry_count = max(0, after_retries - before_retries)
                request_count += repository_request_count
                retry_count += repository_retry_count
                failure_count += 1
                code, summary = _error_fields(exc)
                if isinstance(exc, RateLimitExceeded):
                    rate_limited = True
                result = {
                    "repository": full_name,
                    "status": "failed",
                    "error_code": code,
                    "error_summary": summary,
                }
                repository_results.append(result)
                self.store.record_repository_result(
                    sync_run_id=sync_run_id,
                    repository_full_name=full_name,
                    status="failed",
                    started_at=repository_started_at,
                    request_count=repository_request_count,
                    failure_count=1,
                    rate_limit_remaining=remaining,
                    rate_limit_reset_at=reset_at,
                    retry_count=repository_retry_count,
                    error_code=code,
                    error_summary=summary,
                )
                continue

            request_count += synchronized.request_count
            retry_count += synchronized.retry_count
            success_count += 1
            status = "not_modified" if synchronized.not_modified else "succeeded"
            result = {
                "repository": full_name,
                "status": status,
                "discovered_count": synchronized.discovered_count,
                "hydrated_count": synchronized.hydrated_count,
                "unavailable_count": synchronized.unavailable_count,
                "request_count": synchronized.request_count,
                "retry_count": synchronized.retry_count,
            }
            repository_results.append(result)
            self.store.record_repository_result(
                sync_run_id=sync_run_id,
                repository_full_name=full_name,
                status=status,
                started_at=repository_started_at,
                request_count=synchronized.request_count,
                discovered_count=synchronized.discovered_count,
                success_count=1,
                rate_limit_remaining=synchronized.rate_limit_remaining,
                rate_limit_reset_at=synchronized.rate_limit_reset_at,
                retry_count=synchronized.retry_count,
                sync_cursor=synchronized.sync_cursor,
                etag=synchronized.etag,
                last_modified=synchronized.last_modified,
            )

        _, _, remaining, reset_at = self.synchronizer._client_metrics()
        if failure_count:
            status = "partially_succeeded" if success_count else "failed"
        else:
            status = "succeeded"
        error_code = "rate_limited" if rate_limited else None
        error_summary = (
            "One or more repositories failed or were skipped after rate limiting"
            if rate_limited
            else None
        )
        self.store.complete_sync_run(
            sync_run_id=sync_run_id,
            status=status,
            request_count=request_count,
            success_count=success_count,
            failure_count=failure_count,
            skipped_count=skipped_count,
            rate_limit_remaining=remaining,
            rate_limit_reset_at=reset_at,
            retry_count=retry_count,
            error_code=error_code,
            error_summary=error_summary,
        )
        return SyncBatchResult(
            sync_run_id=sync_run_id,
            status=status,
            repository_count=len(self.repositories),
            request_count=request_count,
            success_count=success_count,
            failure_count=failure_count,
            skipped_count=skipped_count,
            retry_count=retry_count,
            rate_limit_remaining=remaining,
            rate_limit_reset_at=reset_at,
            repositories=tuple(repository_results),
        )

    def refresh_stale_candidates(
        self,
        *,
        older_than_hours: int,
        requested_by: str,
    ) -> RefreshBatchResult:
        if older_than_hours < 1:
            raise ValueError("older_than_hours must be at least 1")
        if not requested_by.strip():
            raise ValueError("requested_by must not be empty")
        from oss_mentor.candidate_refresh import CandidateRefresher

        started_at = datetime.now(timezone.utc).isoformat()
        sync_run_id = self.store.create_sync_run(
            requested_by=requested_by,
            limit_per_repository=100,
            repository_count=len(self.repositories),
            started_at=started_at,
            run_type="candidate_refresh",
        )
        request_start, retry_start, _, _ = self.synchronizer._client_metrics()
        result = CandidateRefresher(
            self.synchronizer,
            self.store,
        ).refresh(
            list(self.repositories),
            older_than_hours=older_than_hours,
        )
        request_end, retry_end, remaining, reset_at = (
            self.synchronizer._client_metrics()
        )
        failed_repositories = {
            str(error.get("target") or "").split("#", maxsplit=1)[0]
            for error in result.errors
        }
        refreshed_repositories = set(result.repositories_refreshed)
        successful_repository_count = 0
        failed_repository_count = 0
        skipped_repository_count = 0
        for repository in self.repositories:
            full_name = str(repository.full_name)
            if full_name in failed_repositories:
                repository_status = "failed"
                failed_repository_count += 1
                repository_error_code = "candidate_refresh_failed"
            elif full_name in refreshed_repositories:
                repository_status = "succeeded"
                successful_repository_count += 1
                repository_error_code = None
            else:
                repository_status = "skipped"
                skipped_repository_count += 1
                repository_error_code = (
                    "rate_limited" if result.rate_limited else "not_selected"
                )
            self.store.record_repository_result(
                sync_run_id=sync_run_id,
                repository_full_name=full_name,
                status=repository_status,
                started_at=started_at,
                success_count=1 if repository_status == "succeeded" else 0,
                failure_count=1 if repository_status == "failed" else 0,
                skipped_count=1 if repository_status == "skipped" else 0,
                rate_limit_remaining=remaining,
                rate_limit_reset_at=reset_at,
                error_code=repository_error_code,
                error_summary=(
                    "Candidate refresh failed"
                    if repository_status == "failed"
                    else None
                ),
            )
        if failed_repository_count:
            status = (
                "partially_succeeded"
                if successful_repository_count
                else "failed"
            )
        else:
            status = "succeeded"
        self.store.complete_sync_run(
            sync_run_id=sync_run_id,
            status=status,
            request_count=max(0, request_end - request_start),
            success_count=successful_repository_count,
            failure_count=failed_repository_count,
            skipped_count=skipped_repository_count,
            rate_limit_remaining=remaining,
            rate_limit_reset_at=reset_at,
            retry_count=max(0, retry_end - retry_start),
            error_code="rate_limited" if result.rate_limited else None,
            error_summary=(
                "Candidate refresh stopped by GitHub rate limiting"
                if result.rate_limited
                else None
            ),
        )
        return RefreshBatchResult(
            sync_run_id=sync_run_id,
            status=status,
            selected_count=result.selected_count,
            refreshed_count=result.refreshed_count,
            unavailable_count=result.unavailable_count,
            failed_count=result.failed_count,
            timeline_checked_count=result.timeline_checked_count,
            rate_limited=result.rate_limited,
            repositories_refreshed=result.repositories_refreshed,
            errors=result.errors,
        )

    def candidate_detail(self, task_candidate_id: int) -> CandidateDetail:
        row = self.store.candidate_detail_row(task_candidate_id)
        if row is None:
            raise KeyError(f"candidate {task_candidate_id} was not found")
        refreshed = False
        verified_at = row.get("github_verified_at")
        stale = True
        if verified_at:
            try:
                parsed = datetime.fromisoformat(
                    str(verified_at).replace("Z", "+00:00")
                )
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                stale = datetime.now(timezone.utc) - parsed > timedelta(hours=24)
            except ValueError:
                stale = True
        if stale:
            try:
                current = self.synchronizer.fetch_current_issue(
                    str(row["repository"]),
                    int(row["issue_number"]),
                )
            except GitHubApiError as exc:
                if exc.status_code in {404, 410}:
                    self.store.mark_candidate_unavailable(
                        task_candidate_id=task_candidate_id,
                        reason="github_unavailable",
                    )
                else:
                    raise
            else:
                self.store.save_candidate(
                    repository_id=int(row["repository_id"]),
                    record=current,
                    eligibility=evaluate_candidate(current),
                )
                refreshed = True
            row = self.store.candidate_detail_row(task_candidate_id)
            if row is None:
                raise KeyError(f"candidate {task_candidate_id} was not found")
        return CandidateDetail(
            task_candidate_id=int(row["task_candidate_id"]),
            repository_full_name=str(row["repository"]),
            issue_number=int(row["issue_number"]),
            title=str(row["title"]),
            body_text=row.get("body_text"),
            html_url=str(row["html_url"]),
            availability=str(row["candidate_availability"]),
            availability_reasons=tuple(row.get("availability_reasons") or ()),
            verified_at=row.get("github_verified_at"),
            refreshed=refreshed,
        )
