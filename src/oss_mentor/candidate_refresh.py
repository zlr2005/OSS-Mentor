"""Refresh stale candidate and repository state from GitHub."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from oss_mentor.candidate_rules import evaluate_candidate
from oss_mentor.candidate_sync import CandidateSynchronizer
from oss_mentor.collector.config import RepositoryConfig
from oss_mentor.collector.github_client import GitHubApiError, RateLimitExceeded
from oss_mentor.sqlite_store import SQLiteCandidateStore


@dataclass(frozen=True, slots=True)
class CandidateRefreshResult:
    selected_count: int
    refreshed_count: int
    unavailable_count: int
    failed_count: int
    timeline_checked_count: int
    rate_limited: bool
    repositories_refreshed: tuple[str, ...]
    errors: tuple[dict[str, Any], ...]


class CandidateRefresher:
    """Refresh repository health, then stale candidates, without long DB locks."""

    def __init__(
        self,
        synchronizer: CandidateSynchronizer,
        store: SQLiteCandidateStore,
    ) -> None:
        self.synchronizer = synchronizer
        self.store = store

    def refresh(
        self,
        repositories: list[RepositoryConfig],
        *,
        older_than_hours: int = 24,
        limit: int = 500,
    ) -> CandidateRefreshResult:
        self.store.initialize()
        errors: list[dict[str, Any]] = []
        active_names: list[str] = []
        checked_names: list[str] = []
        rate_limited = False

        for repository in repositories:
            try:
                health = self.synchronizer.fetch_repository_health(
                    repository.full_name
                )
            except RateLimitExceeded as exc:
                errors.append(self._error("repository", repository.full_name, exc))
                rate_limited = True
                break
            except (GitHubApiError, OSError, ValueError) as exc:
                errors.append(self._error("repository", repository.full_name, exc))
                continue
            with self.store.connect() as connection:
                self.store.upsert_repository(
                    connection,
                    full_name=repository.full_name,
                    ecosystems_last_synced_at=None,
                    ecosystem=repository.ecosystem,
                    primary_language=repository.primary_language,
                    **health,
                )
            checked_names.append(repository.full_name)
            if not health["is_archived"] and not health["is_disabled"]:
                active_names.append(repository.full_name)

        candidates = self.store.stale_candidates(
            repositories=active_names,
            older_than_hours=older_than_hours,
            limit=limit,
        ) if active_names else []
        refreshed_count = 0
        unavailable_count = 0
        timeline_checked_count = 0

        for candidate in candidates:
            repository = str(candidate["repository"])
            issue_number = int(candidate["issue_number"])
            try:
                record = self.synchronizer.fetch_current_issue(
                    repository, issue_number
                )
            except RateLimitExceeded as exc:
                errors.append(
                    self._error("candidate", f"{repository}#{issue_number}", exc)
                )
                rate_limited = True
                break
            except GitHubApiError as exc:
                if exc.status_code in {404, 410}:
                    self.store.mark_candidate_unavailable(
                        task_candidate_id=int(candidate["task_candidate_id"])
                    )
                    unavailable_count += 1
                else:
                    errors.append(
                        self._error("candidate", f"{repository}#{issue_number}", exc)
                    )
                continue
            except (OSError, ValueError) as exc:
                errors.append(
                    self._error("candidate", f"{repository}#{issue_number}", exc)
                )
                continue

            if record.get("has_linked_open_pr") is not None:
                timeline_checked_count += 1
            with self.store.connect() as connection:
                self.store.upsert_candidate(
                    connection,
                    repository_id=int(candidate["repository_id"]),
                    record=record,
                    eligibility=evaluate_candidate(record),
                )
            refreshed_count += 1

        self.store.mark_repositories_refreshed(checked_names)
        return CandidateRefreshResult(
            selected_count=len(candidates),
            refreshed_count=refreshed_count,
            unavailable_count=unavailable_count,
            failed_count=len(errors),
            timeline_checked_count=timeline_checked_count,
            rate_limited=rate_limited,
            repositories_refreshed=tuple(checked_names),
            errors=tuple(errors),
        )

    @staticmethod
    def _error(scope: str, target: str, exc: Exception) -> dict[str, Any]:
        return {
            "scope": scope,
            "target": target,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
