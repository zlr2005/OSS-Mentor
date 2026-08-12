"""SQLite persistence for candidate synchronization and availability."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol

from oss_mentor.candidate_rules import (
    AvailabilityResult,
    EligibilityResult,
    evaluate_availability,
)
from oss_mentor.sqlite_store import SQLiteCandidateStore


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    current = value or _utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat()


def _json_list(value: str | None) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _safe_summary(value: str | None, *, limit: int = 500) -> str | None:
    """Keep persisted errors concise and strip common credential-shaped text."""

    if not value:
        return None
    sanitized = value.replace("\r", " ").replace("\n", " ")
    sanitized = re.sub(
        r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)\b",
        "[redacted]",
        sanitized,
        flags=re.IGNORECASE,
    )
    sanitized = re.sub(
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "[redacted-email]",
        sanitized,
        flags=re.IGNORECASE,
    )
    lowered = sanitized.casefold()
    for marker in ("authorization:", "bearer ", "token=", "access_token="):
        index = lowered.find(marker)
        if index >= 0:
            sanitized = sanitized[:index] + "[redacted]"
            lowered = sanitized.casefold()
    return sanitized[:limit]


class CandidateStorage(Protocol):
    """Business-facing candidate storage contract.

    D can later place the shared transaction protocol in ``storage/base.py``
    without requiring candidate services to import the legacy SQLite facade.
    """

    def initialize(self) -> None: ...

    def save_repository_health(
        self,
        *,
        repository: dict[str, Any],
        checkpoint: dict[str, Any] | None = None,
    ) -> int: ...

    def save_repository_candidates(
        self,
        *,
        repository: dict[str, Any],
        candidates: Iterable[tuple[dict[str, Any], EligibilityResult]],
        checkpoint: dict[str, Any] | None = None,
    ) -> int: ...

    def save_candidate(
        self,
        *,
        repository_id: int,
        record: dict[str, Any],
        eligibility: EligibilityResult,
    ) -> None: ...

    def summary(self) -> dict[str, Any]: ...

    def stale_candidates(
        self,
        *,
        repositories: list[str],
        older_than_hours: int,
        limit: int,
    ) -> list[dict[str, Any]]: ...

    def mark_candidate_unavailable(
        self,
        *,
        task_candidate_id: int,
        reason: str = "github_unavailable",
        verified_at: str | None = None,
    ) -> None: ...

    def mark_repositories_refreshed(self, full_names: list[str]) -> None: ...

    def candidate_report_rows(self) -> dict[str, list[dict[str, Any]]]: ...

    def create_sync_run(
        self,
        *,
        requested_by: str,
        limit_per_repository: int,
        repository_count: int,
        started_at: str | None = None,
        run_type: str = "repository_sync",
    ) -> int: ...

    def fail_abandoned_sync_runs(
        self,
        *,
        older_than_hours: int = 6,
        now: datetime | None = None,
    ) -> int: ...

    def record_repository_result(self, **values: Any) -> None: ...

    def complete_sync_run(self, **values: Any) -> None: ...

    def candidate_detail_row(
        self,
        task_candidate_id: int,
    ) -> dict[str, Any] | None: ...


class SyncAlreadyRunningError(RuntimeError):
    """Raised when another candidate synchronization batch is still active."""


class LegacyCandidateStorageAdapter:
    """Expose the high-level candidate port over the v0.4 SQLite facade."""

    def __init__(self, store: SQLiteCandidateStore) -> None:
        self._store = store

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)

    def save_repository_health(
        self,
        *,
        repository: dict[str, Any],
        checkpoint: dict[str, Any] | None = None,
    ) -> int:
        del checkpoint
        with self._store.connect() as connection:
            return self._store.upsert_repository(connection, **repository)

    def save_repository_candidates(
        self,
        *,
        repository: dict[str, Any],
        candidates: Iterable[tuple[dict[str, Any], EligibilityResult]],
        checkpoint: dict[str, Any] | None = None,
    ) -> int:
        del checkpoint
        with self._store.connect() as connection:
            repository_id = self._store.upsert_repository(connection, **repository)
            for record, eligibility in candidates:
                self._store.upsert_candidate(
                    connection,
                    repository_id=repository_id,
                    record=record,
                    eligibility=eligibility,
                )
        return repository_id

    def save_candidate(
        self,
        *,
        repository_id: int,
        record: dict[str, Any],
        eligibility: EligibilityResult,
    ) -> None:
        with self._store.connect() as connection:
            self._store.upsert_candidate(
                connection,
                repository_id=repository_id,
                record=record,
                eligibility=eligibility,
            )


def as_candidate_storage(store: Any) -> CandidateStorage:
    """Adapt the legacy facade while keeping business code backend-neutral."""

    if all(
        hasattr(store, name)
        for name in (
            "save_repository_health",
            "save_repository_candidates",
            "save_candidate",
        )
    ):
        return store
    return LegacyCandidateStorageAdapter(store)


class SQLiteCandidateStorage(SQLiteCandidateStore):
    """Candidate-owned extension of the v0.4 compatibility store."""

    def __init__(self, database_path: Path, migration_path: Path) -> None:
        super().__init__(database_path, migration_path)

    def save_repository_health(
        self,
        *,
        repository: dict[str, Any],
        checkpoint: dict[str, Any] | None = None,
    ) -> int:
        with self.connect() as connection:
            repository_id = self.upsert_repository(connection, **repository)
            if checkpoint:
                self.save_sync_checkpoint(
                    connection,
                    repository_id=repository_id,
                    **checkpoint,
                )
        return repository_id

    def save_repository_candidates(
        self,
        *,
        repository: dict[str, Any],
        candidates: Iterable[tuple[dict[str, Any], EligibilityResult]],
        checkpoint: dict[str, Any] | None = None,
    ) -> int:
        with self.connect() as connection:
            repository_id = self.upsert_repository(connection, **repository)
            for record, eligibility in candidates:
                self.upsert_candidate(
                    connection,
                    repository_id=repository_id,
                    record=record,
                    eligibility=eligibility,
                )
            if checkpoint:
                self.save_sync_checkpoint(
                    connection,
                    repository_id=repository_id,
                    **checkpoint,
                )
        return repository_id

    def save_candidate(
        self,
        *,
        repository_id: int,
        record: dict[str, Any],
        eligibility: EligibilityResult,
    ) -> None:
        with self.connect() as connection:
            self.upsert_candidate(
                connection,
                repository_id=repository_id,
                record=record,
                eligibility=eligibility,
            )

    def upsert_repository(
        self,
        connection: sqlite3.Connection,
        **values: Any,
    ) -> int:
        repository_id = super().upsert_repository(connection, **values)
        inactive = (
            bool(values.get("is_archived"))
            or bool(values.get("is_disabled"))
            or values.get("maintenance_status") == "inactive"
        )
        if inactive:
            connection.execute(
                """
                UPDATE task_candidate
                SET candidate_availability = 'repository_inactive',
                    availability_reasons_json = '["repository_inactive"]'
                WHERE repository_id = ?
                """,
                (repository_id,),
            )
        return repository_id

    def upsert_candidate(
        self,
        connection: sqlite3.Connection,
        *,
        repository_id: int,
        record: dict[str, Any],
        eligibility: EligibilityResult,
    ) -> None:
        super().upsert_candidate(
            connection,
            repository_id=repository_id,
            record=record,
            eligibility=eligibility,
        )
        repository = connection.execute(
            """
            SELECT is_archived, is_disabled, maintenance_status
            FROM repository
            WHERE repository_id = ?
            """,
            (repository_id,),
        ).fetchone()
        if "github_unavailable" in eligibility.reasons:
            availability = AvailabilityResult(
                availability="closed",
                reasons=("github_unavailable",),
                verified_at=record.get("github_verified_at"),
            )
        else:
            availability = evaluate_availability(
                record,
                repository=dict(repository) if repository is not None else None,
            )
        connection.execute(
            """
            UPDATE task_candidate
            SET candidate_availability = ?,
                availability_reasons_json = ?
            WHERE repository_id = ? AND issue_number = ?
            """,
            (
                availability.availability,
                json.dumps(
                    availability.reasons,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                repository_id,
                int(record["issue_number"]),
            ),
        )

    def get_sync_checkpoint(self, full_name: str) -> dict[str, Any]:
        self.initialize()
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT candidate_sync_cursor AS cursor,
                       candidate_sync_etag AS etag,
                       candidate_sync_last_modified AS last_modified,
                       github_updated_at,
                       last_candidate_sync_at
                FROM repository
                WHERE full_name = ?
                """,
                (full_name,),
            ).fetchone()
        return dict(row) if row is not None else {}

    def save_sync_checkpoint(
        self,
        connection: sqlite3.Connection,
        *,
        repository_id: int,
        cursor: str | None,
        etag: str | None,
        last_modified: str | None,
        github_updated_at: str | None = None,
    ) -> None:
        connection.execute(
            """
            UPDATE repository
            SET candidate_sync_cursor = COALESCE(?, candidate_sync_cursor),
                candidate_sync_etag = COALESCE(?, candidate_sync_etag),
                candidate_sync_last_modified =
                    COALESCE(?, candidate_sync_last_modified),
                github_updated_at = COALESCE(?, github_updated_at),
                updated_at = ?
            WHERE repository_id = ?
            """,
            (
                cursor,
                etag,
                last_modified,
                github_updated_at,
                _iso(),
                repository_id,
            ),
        )

    def create_sync_run(
        self,
        *,
        requested_by: str,
        limit_per_repository: int,
        repository_count: int,
        started_at: str | None = None,
        run_type: str = "repository_sync",
    ) -> int:
        self.initialize()
        self.fail_abandoned_sync_runs()
        timestamp = started_at or _iso()
        try:
            with self.connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO sync_run (
                        run_type,
                        status,
                        requested_by,
                        limit_per_repository,
                        started_at,
                        repository_count,
                        created_at
                    ) VALUES (?, 'running', ?, ?, ?, ?, ?)
                    """,
                    (
                        run_type,
                        requested_by,
                        limit_per_repository,
                        timestamp,
                        repository_count,
                        timestamp,
                    ),
                )
                return int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            if "sync_run.status" not in str(exc):
                raise
            raise SyncAlreadyRunningError(
                "another candidate synchronization batch is already running"
            ) from exc

    def fail_abandoned_sync_runs(
        self,
        *,
        older_than_hours: int = 6,
        now: datetime | None = None,
    ) -> int:
        if older_than_hours < 1:
            raise ValueError("older_than_hours must be at least 1")
        cutoff = _iso((now or _utc_now()) - timedelta(hours=older_than_hours))
        finished_at = _iso(now)
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE sync_run
                SET status = 'failed',
                    finished_at = ?,
                    error_code = 'abandoned_run',
                    error_summary = 'Recovered an abandoned running batch'
                WHERE status = 'running' AND started_at < ?
                """,
                (finished_at, cutoff),
            )
            return int(cursor.rowcount)

    def record_repository_result(
        self,
        *,
        sync_run_id: int,
        repository_full_name: str,
        status: str,
        started_at: str,
        finished_at: str | None = None,
        request_count: int = 0,
        discovered_count: int = 0,
        success_count: int = 0,
        failure_count: int = 0,
        skipped_count: int = 0,
        rate_limit_remaining: int | None = None,
        rate_limit_reset_at: str | None = None,
        retry_count: int = 0,
        sync_cursor: str | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
        error_code: str | None = None,
        error_summary: str | None = None,
    ) -> None:
        self.initialize()
        with self.connect() as connection:
            repository = connection.execute(
                "SELECT repository_id FROM repository WHERE full_name = ?",
                (repository_full_name,),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO sync_repository_result (
                    sync_run_id,
                    repository_id,
                    repository_full_name,
                    status,
                    started_at,
                    finished_at,
                    request_count,
                    discovered_count,
                    success_count,
                    failure_count,
                    skipped_count,
                    rate_limit_remaining,
                    rate_limit_reset_at,
                    retry_count,
                    sync_cursor,
                    etag,
                    last_modified,
                    error_code,
                    error_summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sync_run_id, repository_full_name) DO UPDATE SET
                    repository_id = excluded.repository_id,
                    status = excluded.status,
                    finished_at = excluded.finished_at,
                    request_count = excluded.request_count,
                    discovered_count = excluded.discovered_count,
                    success_count = excluded.success_count,
                    failure_count = excluded.failure_count,
                    skipped_count = excluded.skipped_count,
                    rate_limit_remaining = excluded.rate_limit_remaining,
                    rate_limit_reset_at = excluded.rate_limit_reset_at,
                    retry_count = excluded.retry_count,
                    sync_cursor = excluded.sync_cursor,
                    etag = excluded.etag,
                    last_modified = excluded.last_modified,
                    error_code = excluded.error_code,
                    error_summary = excluded.error_summary
                """,
                (
                    sync_run_id,
                    int(repository[0]) if repository is not None else None,
                    repository_full_name,
                    status,
                    started_at,
                    finished_at or _iso(),
                    request_count,
                    discovered_count,
                    success_count,
                    failure_count,
                    skipped_count,
                    rate_limit_remaining,
                    rate_limit_reset_at,
                    retry_count,
                    sync_cursor,
                    etag,
                    last_modified,
                    error_code,
                    _safe_summary(error_summary),
                ),
            )

    def complete_sync_run(
        self,
        *,
        sync_run_id: int,
        status: str,
        request_count: int,
        success_count: int,
        failure_count: int,
        skipped_count: int,
        rate_limit_remaining: int | None,
        rate_limit_reset_at: str | None,
        retry_count: int,
        error_code: str | None = None,
        error_summary: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE sync_run
                SET status = ?,
                    finished_at = ?,
                    request_count = ?,
                    success_count = ?,
                    failure_count = ?,
                    skipped_count = ?,
                    rate_limit_remaining = ?,
                    rate_limit_reset_at = ?,
                    retry_count = ?,
                    error_code = ?,
                    error_summary = ?
                WHERE sync_run_id = ?
                """,
                (
                    status,
                    finished_at or _iso(),
                    request_count,
                    success_count,
                    failure_count,
                    skipped_count,
                    rate_limit_remaining,
                    rate_limit_reset_at,
                    retry_count,
                    error_code,
                    _safe_summary(error_summary),
                    sync_run_id,
                ),
            )

    def mark_candidate_unavailable(
        self,
        *,
        task_candidate_id: int,
        reason: str = "github_unavailable",
        verified_at: str | None = None,
    ) -> None:
        super().mark_candidate_unavailable(
            task_candidate_id=task_candidate_id,
            reason=reason,
            verified_at=verified_at,
        )
        availability = "closed" if reason == "github_unavailable" else "temporarily_unverified"
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE task_candidate
                SET candidate_availability = ?,
                    availability_reasons_json = ?
                WHERE task_candidate_id = ?
                """,
                (
                    availability,
                    json.dumps([reason], separators=(",", ":")),
                    task_candidate_id,
                ),
            )

    def mark_stale_candidates(
        self,
        *,
        older_than_hours: int = 24,
        now: datetime | None = None,
    ) -> int:
        cutoff = _iso((now or _utc_now()) - timedelta(hours=older_than_hours))
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE task_candidate
                SET candidate_availability = 'temporarily_unverified',
                    availability_reasons_json = '["verification_older_than_limit"]'
                WHERE candidate_availability = 'available'
                  AND (
                      github_verified_at IS NULL
                      OR github_verified_at < ?
                  )
                """,
                (cutoff,),
            )
            return int(cursor.rowcount)

    def available_candidates(
        self,
        *,
        older_than_hours: int = 24,
        limit: int = 100,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        self.initialize()
        self.mark_stale_candidates(older_than_hours=older_than_hours, now=now)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT tc.*, r.full_name AS repository, r.primary_language
                FROM task_candidate AS tc
                JOIN repository AS r USING (repository_id)
                WHERE tc.candidate_availability = 'available'
                  AND tc.candidate_eligibility = 'eligible'
                  AND COALESCE(r.is_archived, 0) = 0
                  AND COALESCE(r.is_disabled, 0) = 0
                  AND COALESCE(r.maintenance_status, 'active') != 'inactive'
                ORDER BY tc.last_activity_at DESC, tc.task_candidate_id
                LIMIT ?
                """,
                (max(1, min(limit, 100)),),
            ).fetchall()
        return [self._candidate_dict(row) for row in rows]

    def candidate_detail_row(self, task_candidate_id: int) -> dict[str, Any] | None:
        self.initialize()
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT tc.*, r.full_name AS repository,
                       r.primary_language,
                       r.is_archived,
                       r.is_disabled,
                       r.maintenance_status
                FROM task_candidate AS tc
                JOIN repository AS r USING (repository_id)
                WHERE tc.task_candidate_id = ?
                """,
                (task_candidate_id,),
            ).fetchone()
        return self._candidate_dict(row) if row is not None else None

    def candidate_pool_status(self, *, now: datetime | None = None) -> dict[str, Any]:
        self.initialize()
        self.mark_stale_candidates(now=now)
        with self.connect() as connection:
            availability = {
                str(row["candidate_availability"]): int(row["count"])
                for row in connection.execute(
                    """
                    SELECT candidate_availability, COUNT(*) AS count
                    FROM task_candidate
                    GROUP BY candidate_availability
                    """
                )
            }
            total = int(
                connection.execute("SELECT COUNT(*) FROM task_candidate").fetchone()[0]
            )
            recommendable = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM task_candidate AS tc
                    JOIN repository AS r USING (repository_id)
                    WHERE tc.candidate_availability = 'available'
                      AND tc.candidate_eligibility = 'eligible'
                      AND COALESCE(r.is_archived, 0) = 0
                      AND COALESCE(r.is_disabled, 0) = 0
                      AND COALESCE(r.maintenance_status, 'active') != 'inactive'
                    """
                ).fetchone()[0]
            )
            newcomer = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM task_candidate AS tc
                    JOIN repository AS r USING (repository_id)
                    WHERE tc.candidate_availability = 'available'
                      AND tc.candidate_eligibility = 'eligible'
                      AND tc.newcomer_label_signal = 1
                      AND COALESCE(r.is_archived, 0) = 0
                      AND COALESCE(r.is_disabled, 0) = 0
                      AND COALESCE(r.maintenance_status, 'active') != 'inactive'
                    """
                ).fetchone()[0]
            )
            latest = connection.execute(
                """
                SELECT sync_run_id, run_type, status, started_at, finished_at,
                       repository_count, request_count, success_count,
                       failure_count, skipped_count, rate_limit_remaining,
                       rate_limit_reset_at, retry_count
                FROM sync_run
                WHERE run_type = 'repository_sync'
                ORDER BY sync_run_id DESC
                LIMIT 1
                """
            ).fetchone()
            latest_refresh = connection.execute(
                """
                SELECT sync_run_id, run_type, status, started_at, finished_at,
                       repository_count, request_count, success_count,
                       failure_count, skipped_count, rate_limit_remaining,
                       rate_limit_reset_at, retry_count
                FROM sync_run
                WHERE run_type = 'candidate_refresh'
                ORDER BY sync_run_id DESC
                LIMIT 1
                """
            ).fetchone()
            failed_repositories = [
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT repository_full_name
                    FROM sync_repository_result
                    WHERE sync_run_id = (
                        SELECT MAX(sync_run_id)
                        FROM sync_run
                        WHERE run_type = 'repository_sync'
                    ) AND status = 'failed'
                    ORDER BY repository_full_name
                    """
                )
            ]
            distributions = self._distributions(connection)
        latest_dict = dict(latest) if latest is not None else None
        if latest_dict and latest_dict["repository_count"]:
            average_cost = round(
                latest_dict["request_count"] / latest_dict["repository_count"],
                4,
            )
        else:
            average_cost = 0.0
        return {
            "candidate_total": total,
            "recommendable_count": recommendable,
            "newcomer_count": newcomer,
            "availability_counts": {
                name: int(availability.get(name, 0))
                for name in (
                    "available",
                    "closed",
                    "assigned",
                    "linked_open_pr",
                    "locked",
                    "repository_inactive",
                    "temporarily_unverified",
                )
            },
            "by_language": distributions["by_language"],
            "by_task_type": distributions["by_task_type"],
            "by_repository": distributions["by_repository"],
            "latest_sync": latest_dict,
            "latest_refresh": (
                dict(latest_refresh) if latest_refresh is not None else None
            ),
            "failed_repositories": failed_repositories,
            "average_github_requests_per_repository": average_cost,
        }

    @staticmethod
    def _candidate_dict(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        for source, target in (
            ("labels_json", "labels"),
            ("ineligibility_reasons_json", "ineligibility_reasons"),
            ("warnings_json", "warnings"),
            ("availability_reasons_json", "availability_reasons"),
            ("task_types_json", "task_types"),
        ):
            if source in value:
                value[target] = _json_list(value.get(source))
        return value

    @staticmethod
    def _distributions(connection: sqlite3.Connection) -> dict[str, dict[str, int]]:
        by_repository = {
            str(row["full_name"]): int(row["count"])
            for row in connection.execute(
                """
                SELECT r.full_name, COUNT(*) AS count
                FROM task_candidate AS tc
                JOIN repository AS r USING (repository_id)
                GROUP BY r.full_name
                ORDER BY r.full_name
                """
            )
        }
        by_language = {
            str(row["language"]): int(row["count"])
            for row in connection.execute(
                """
                SELECT COALESCE(r.primary_language, 'unknown') AS language,
                       COUNT(*) AS count
                FROM task_candidate AS tc
                JOIN repository AS r USING (repository_id)
                GROUP BY COALESCE(r.primary_language, 'unknown')
                ORDER BY language
                """
            )
        }
        task_types: dict[str, int] = {}
        for row in connection.execute(
            "SELECT task_types_json FROM task_candidate"
        ):
            for task_type in _json_list(row[0]) or ["unclassified"]:
                task_types[task_type] = task_types.get(task_type, 0) + 1
        return {
            "by_repository": by_repository,
            "by_language": by_language,
            "by_task_type": dict(sorted(task_types.items())),
        }

    def sync_run(self, sync_run_id: int) -> dict[str, Any] | None:
        self.initialize()
        with self.connect() as connection:
            run = connection.execute(
                "SELECT * FROM sync_run WHERE sync_run_id = ?",
                (sync_run_id,),
            ).fetchone()
            results = connection.execute(
                """
                SELECT * FROM sync_repository_result
                WHERE sync_run_id = ?
                ORDER BY repository_full_name
                """,
                (sync_run_id,),
            ).fetchall()
        if run is None:
            return None
        value = dict(run)
        value["repositories"] = [dict(row) for row in results]
        return value

    def repositories_by_name(
        self,
        full_names: Iterable[str],
    ) -> list[dict[str, Any]]:
        names = tuple(dict.fromkeys(full_names))
        if not names:
            return []
        placeholders = ",".join("?" for _ in names)
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM repository WHERE full_name IN ({placeholders})",
                names,
            ).fetchall()
        return [dict(row) for row in rows]
