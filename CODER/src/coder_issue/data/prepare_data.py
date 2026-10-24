"""Full stage-1 streaming preparation pipeline for the GFI-Bot BSON exports."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any, Iterable, Iterator
import uuid

import pyarrow as pa
import pyarrow.parquet as pq

from coder_issue.errors import CoderIssueError, DataFileError
from coder_issue.logging_utils import configure_logging
from coder_issue.runtime import build_run_metadata

from .bson_reader import iter_bson_documents
from .normalize import (
    developer_key,
    issue_key,
    label_key,
    normalize_labels,
    object_id_text,
    repository_key,
    safe_numeric_features,
    stable_json,
    timestamp_text,
    utc_datetime,
)
from .schema import (
    SCHEMA_VERSION,
    TABLE_SCHEMAS,
    PreparationSettings,
    load_preparation_settings,
    schema_with_metadata,
)
from .temporal import classify_snapshot, split_interaction
from .validation import FieldProfiler, validate_output_directories, verify_raw_inputs

LOGGER = logging.getLogger(__name__)


class BufferedParquetWriter:
    """Write dictionaries to Parquet in bounded record batches."""

    def __init__(
        self,
        path: Path,
        schema: pa.Schema,
        *,
        batch_size: int,
        compression: str,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.schema = schema
        self.batch_size = batch_size
        self.rows: list[dict[str, Any]] = []
        self.writer = pq.ParquetWriter(path, schema=schema, compression=compression)
        self.row_count = 0

    def append(self, row: dict[str, Any]) -> None:
        self.rows.append(row)
        if len(self.rows) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        if not self.rows:
            return
        table = pa.Table.from_pylist(self.rows, schema=self.schema)
        self.writer.write_table(table)
        self.row_count += len(self.rows)
        self.rows.clear()

    def close(self) -> None:
        self.flush()
        self.writer.close()

    def __enter__(self) -> BufferedParquetWriter:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


def _connect_index(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA temp_store=FILE")
    connection.executescript(
        """
        CREATE TABLE repositories (
            repository_key TEXT PRIMARY KEY,
            owner TEXT NOT NULL,
            name TEXT NOT NULL,
            spelling_variants INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE dataset_issues (
            issue_key TEXT PRIMARY KEY,
            repository_key TEXT NOT NULL,
            number INTEGER NOT NULL,
            created_at TEXT,
            closed_at TEXT,
            snapshot_count INTEGER NOT NULL DEFAULT 0,
            creation_count INTEGER NOT NULL DEFAULT 0,
            closure_count INTEGER NOT NULL DEFAULT 0,
            unknown_count INTEGER NOT NULL DEFAULT 0,
            created_conflicts INTEGER NOT NULL DEFAULT 0,
            closed_conflicts INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE labels (
            label_key TEXT PRIMARY KEY,
            label TEXT NOT NULL
        );
        CREATE TABLE resolved_records (
            source_order INTEGER NOT NULL,
            source_object_id TEXT NOT NULL,
            issue_key TEXT NOT NULL,
            repository_key TEXT NOT NULL,
            resolver_key TEXT NOT NULL,
            created_at TEXT,
            resolved_at TEXT NOT NULL
        );
        CREATE INDEX resolved_records_issue_idx ON resolved_records(issue_key);
        CREATE TABLE developer_spellings (
            developer_key TEXT NOT NULL,
            developer_login TEXT NOT NULL,
            PRIMARY KEY(developer_key, developer_login)
        );
        CREATE TABLE canonical_resolutions (
            issue_key TEXT PRIMARY KEY,
            resolver_key TEXT NOT NULL,
            created_at TEXT,
            resolved_at TEXT NOT NULL,
            source_object_id TEXT NOT NULL,
            duplicate_count INTEGER NOT NULL
        );
        """
    )
    return connection


def _upsert_repository(
    connection: sqlite3.Connection,
    repo_key: str,
    owner: str,
    name: str,
) -> None:
    connection.execute(
        """
        INSERT INTO repositories(repository_key, owner, name) VALUES (?, ?, ?)
        ON CONFLICT(repository_key) DO UPDATE SET
            spelling_variants = spelling_variants +
                CASE WHEN owner <> excluded.owner OR name <> excluded.name THEN 1 ELSE 0 END,
            owner = CASE WHEN excluded.owner < owner THEN excluded.owner ELSE owner END,
            name = CASE WHEN excluded.name < name THEN excluded.name ELSE name END
        """,
        (repo_key, owner, name),
    )


def _upsert_dataset_issue(
    connection: sqlite3.Connection,
    *,
    key: str,
    repo_key: str,
    number: int,
    created_at: str | None,
    closed_at: str | None,
    snapshot_type: str,
) -> None:
    connection.execute(
        """
        INSERT INTO dataset_issues(
            issue_key, repository_key, number, created_at, closed_at,
            snapshot_count, creation_count, closure_count, unknown_count
        ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
        ON CONFLICT(issue_key) DO UPDATE SET
            created_conflicts = created_conflicts + CASE
                WHEN created_at IS NOT NULL AND excluded.created_at IS NOT NULL
                     AND created_at <> excluded.created_at THEN 1 ELSE 0 END,
            closed_conflicts = closed_conflicts + CASE
                WHEN closed_at IS NOT NULL AND excluded.closed_at IS NOT NULL
                     AND closed_at <> excluded.closed_at THEN 1 ELSE 0 END,
            created_at = CASE
                WHEN created_at IS NULL THEN excluded.created_at
                WHEN excluded.created_at IS NULL THEN created_at
                WHEN excluded.created_at < created_at THEN excluded.created_at
                ELSE created_at END,
            closed_at = CASE
                WHEN closed_at IS NULL THEN excluded.closed_at
                WHEN excluded.closed_at IS NULL THEN closed_at
                WHEN excluded.closed_at < closed_at THEN excluded.closed_at
                ELSE closed_at END,
            snapshot_count = snapshot_count + 1,
            creation_count = creation_count + excluded.creation_count,
            closure_count = closure_count + excluded.closure_count,
            unknown_count = unknown_count + excluded.unknown_count
        """,
        (
            key,
            repo_key,
            number,
            created_at,
            closed_at,
            int(snapshot_type == "creation_snapshot"),
            int(snapshot_type == "closure_snapshot"),
            int(snapshot_type == "unknown_snapshot"),
        ),
    )


def _scan_dataset(
    settings: PreparationSettings,
    connection: sqlite3.Connection,
    profiler: FieldProfiler,
    counters: Counter[str],
) -> None:
    started = time.monotonic()
    for index, document in enumerate(
        iter_bson_documents(
            settings.dataset_path,
            max_document_bytes=settings.processing.max_document_bytes,
        ),
        start=1,
    ):
        profiler.observe("dataset", document)
        counters["dataset_records"] += 1
        try:
            key = issue_key(document.get("owner"), document.get("name"), document.get("number"))
            repo_key = repository_key(document.get("owner"), document.get("name"))
            number = int(document["number"])
        except (KeyError, TypeError, ValueError, OverflowError):
            counters["dataset_invalid_issue_key"] += 1
            continue
        try:
            created = utc_datetime(document.get("created_at"))
        except (TypeError, ValueError):
            created = None
            counters["dataset_invalid_created_at"] += 1
        try:
            closed = utc_datetime(document.get("closed_at"))
        except (TypeError, ValueError):
            closed = None
            counters["dataset_invalid_closed_at"] += 1
        try:
            before = utc_datetime(document.get("before"))
        except (TypeError, ValueError):
            before = None
            counters["dataset_invalid_before"] += 1
        snapshot_type = classify_snapshot(before=before, created_at=created, closed_at=closed)
        counters[f"snapshot_{snapshot_type}"] += 1
        if before is None:
            counters["snapshot_missing_time"] += 1
        if created and closed and created > closed:
            counters["dataset_created_after_closed"] += 1
        if before and created and before < created:
            counters["snapshot_before_creation"] += 1
        if before and closed and before > closed:
            counters["snapshot_after_closure"] += 1
        owner = str(document["owner"]).strip()
        name = str(document["name"]).strip()
        _upsert_repository(connection, repo_key, owner, name)
        _upsert_dataset_issue(
            connection,
            key=key,
            repo_key=repo_key,
            number=number,
            created_at=timestamp_text(created),
            closed_at=timestamp_text(closed),
            snapshot_type=snapshot_type,
        )
        labels, invalid_labels = normalize_labels(document.get("labels"))
        counters["invalid_label_values"] += invalid_labels
        for label in labels:
            normalized = label_key(label)
            connection.execute(
                """
                INSERT INTO labels(label_key, label) VALUES (?, ?)
                ON CONFLICT(label_key) DO UPDATE SET
                    label = CASE WHEN excluded.label < label THEN excluded.label ELSE label END
                """,
                (normalized, label),
            )
        events = document.get("events")
        if isinstance(events, list):
            counters["dataset_scalar_events"] += sum(not isinstance(item, dict) for item in events)
            counters["dataset_structured_events"] += sum(isinstance(item, dict) for item in events)
        if index % settings.processing.batch_size == 0:
            connection.commit()
        if index % settings.processing.progress_every == 0:
            LOGGER.info("dataset schema/index scan: %d records", index)
    connection.commit()
    LOGGER.info("dataset scan complete in %.1fs", time.monotonic() - started)


def _scan_resolved(
    settings: PreparationSettings,
    connection: sqlite3.Connection,
    profiler: FieldProfiler,
    counters: Counter[str],
) -> None:
    started = time.monotonic()
    for index, document in enumerate(
        iter_bson_documents(
            settings.resolved_issue_path,
            max_document_bytes=settings.processing.max_document_bytes,
        ),
        start=1,
    ):
        profiler.observe("resolved_issue", document)
        counters["resolved_records"] += 1
        try:
            key = issue_key(document.get("owner"), document.get("name"), document.get("number"))
            repo_key = repository_key(document.get("owner"), document.get("name"))
        except (TypeError, ValueError, OverflowError):
            counters["resolved_invalid_issue_key"] += 1
            continue
        try:
            resolver = developer_key(document.get("resolver"))
        except ValueError:
            counters["resolved_invalid_resolver"] += 1
            continue
        resolver_login = str(document["resolver"]).strip()
        try:
            created = utc_datetime(document.get("created_at"))
            resolved = utc_datetime(document.get("resolved_at"))
        except (TypeError, ValueError):
            counters["resolved_invalid_timestamp"] += 1
            continue
        if resolved is None:
            counters["resolved_missing_resolved_at"] += 1
            continue
        if created and created > resolved:
            counters["resolved_created_after_resolved"] += 1
            continue
        events = document.get("events")
        if not isinstance(events, list):
            counters["resolved_events_not_array"] += 1
        else:
            counters["resolved_event_count"] += len(events)
            counters["resolved_reopened_events"] += sum(
                isinstance(event, dict) and event.get("type") == "reopened" for event in events
            )
        connection.execute(
            """
            INSERT INTO resolved_records(
                source_order, source_object_id, issue_key, repository_key,
                resolver_key, created_at, resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                index,
                object_id_text(document.get("_id")),
                key,
                repo_key,
                resolver,
                timestamp_text(created),
                timestamp_text(resolved),
            ),
        )
        connection.execute(
            "INSERT OR IGNORE INTO developer_spellings(developer_key, developer_login) VALUES (?, ?)",
            (resolver, resolver_login),
        )
        if index % settings.processing.batch_size == 0:
            connection.commit()
        if index % settings.processing.progress_every == 0:
            LOGGER.info("resolved schema/index scan: %d records", index)
    connection.commit()
    LOGGER.info("resolved scan complete in %.1fs", time.monotonic() - started)


def _canonicalize_resolutions(
    connection: sqlite3.Connection,
    counters: Counter[str],
) -> None:
    cursor = connection.execute(
        """
        SELECT issue_key, resolver_key, created_at, resolved_at, source_object_id
        FROM resolved_records
        ORDER BY issue_key, resolver_key, resolved_at, source_object_id
        """
    )
    current_key: str | None = None
    group: list[tuple[str, str | None, str, str]] = []

    def emit(key: str | None, values: list[tuple[str, str | None, str, str]]) -> None:
        if key is None:
            return
        pairs = {(item[0], item[2]) for item in values}
        if len(pairs) != 1:
            counters["resolved_conflicting_issue_keys"] += 1
            counters["resolved_conflicting_records"] += len(values)
            return
        resolver, created, resolved, source_id = min(values, key=lambda item: item[3])
        connection.execute(
            """
            INSERT INTO canonical_resolutions(
                issue_key, resolver_key, created_at, resolved_at, source_object_id, duplicate_count
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (key, resolver, created, resolved, source_id, len(values)),
        )
        counters["resolved_duplicate_records"] += len(values) - 1

    for key, resolver, created, resolved, source_id in cursor:
        if current_key is not None and key != current_key:
            emit(current_key, group)
            group = []
        current_key = key
        group.append((resolver, created, resolved, source_id))
    emit(current_key, group)
    connection.commit()


def _create_mappings(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE repository_mapping (
            repository_id INTEGER PRIMARY KEY,
            repository_key TEXT NOT NULL UNIQUE
        );
        INSERT INTO repository_mapping(repository_id, repository_key)
        SELECT ROW_NUMBER() OVER (ORDER BY repository_key) - 1, repository_key
        FROM repositories ORDER BY repository_key;

        CREATE TABLE issue_mapping (
            issue_id INTEGER PRIMARY KEY,
            issue_key TEXT NOT NULL UNIQUE
        );
        INSERT INTO issue_mapping(issue_id, issue_key)
        SELECT ROW_NUMBER() OVER (ORDER BY issue_key) - 1, issue_key
        FROM dataset_issues ORDER BY issue_key;

        CREATE TABLE developer_mapping (
            developer_id INTEGER PRIMARY KEY,
            developer_key TEXT NOT NULL UNIQUE
        );
        INSERT INTO developer_mapping(developer_id, developer_key)
        SELECT ROW_NUMBER() OVER (ORDER BY c.resolver_key) - 1, c.resolver_key
        FROM canonical_resolutions c
        INNER JOIN dataset_issues d ON d.issue_key = c.issue_key
        GROUP BY c.resolver_key ORDER BY c.resolver_key;

        CREATE TABLE label_mapping (
            label_id INTEGER PRIMARY KEY,
            label_key TEXT NOT NULL UNIQUE
        );
        INSERT INTO label_mapping(label_id, label_key)
        SELECT ROW_NUMBER() OVER (ORDER BY label_key) - 1, label_key
        FROM labels ORDER BY label_key;
        """
    )
    connection.commit()


def _rows(cursor: sqlite3.Cursor, size: int) -> Iterator[tuple[Any, ...]]:
    while True:
        rows = cursor.fetchmany(size)
        if not rows:
            return
        yield from rows


def _writer(
    path: Path,
    table_name: str,
    settings: PreparationSettings,
) -> BufferedParquetWriter:
    return BufferedParquetWriter(
        path,
        schema_with_metadata(TABLE_SCHEMAS[table_name], table_name=table_name, settings=settings),
        batch_size=settings.processing.batch_size,
        compression=settings.processing.parquet_compression,
    )


def _write_entities_and_mappings(
    settings: PreparationSettings,
    connection: sqlite3.Connection,
    normalized: Path,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    mappings = normalized / "mappings"
    mappings.mkdir(parents=True, exist_ok=True)

    with _writer(normalized / "developers.parquet", "developers", settings) as writer:
        for developer_id, key, login in _rows(
            connection.execute(
                """
                SELECT m.developer_id, m.developer_key, MIN(s.developer_login)
                FROM developer_mapping m
                JOIN developer_spellings s USING(developer_key)
                GROUP BY m.developer_id, m.developer_key
                ORDER BY m.developer_id
                """
            ),
            settings.processing.batch_size,
        ):
            writer.append(
                {
                    "developer_id": developer_id,
                    "developer_key": key,
                    "developer_login": login,
                }
            )
    counts["developers"] = writer.row_count

    with _writer(normalized / "repositories.parquet", "repositories", settings) as writer:
        query = """
            SELECT m.repository_id, m.repository_key, r.owner, r.name
            FROM repository_mapping m JOIN repositories r USING(repository_key)
            ORDER BY m.repository_id
        """
        for repository_id, key, owner, name in _rows(
            connection.execute(query), settings.processing.batch_size
        ):
            writer.append(
                {
                    "repository_id": repository_id,
                    "repository_key": key,
                    "owner": owner,
                    "name": name,
                }
            )
    counts["repositories"] = writer.row_count
    with _writer(mappings / "repository_mapping.parquet", "repository_mapping", settings) as writer:
        for repository_id, key in _rows(
            connection.execute(
                "SELECT repository_id, repository_key FROM repository_mapping ORDER BY repository_id"
            ),
            settings.processing.batch_size,
        ):
            writer.append({"repository_id": repository_id, "repository_key": key})

    with _writer(normalized / "issues.parquet", "issues", settings) as writer:
        query = """
            SELECT im.issue_id, im.issue_key, rm.repository_id, d.number,
                   d.created_at, d.closed_at, d.snapshot_count,
                   CASE WHEN c.issue_key IS NULL THEN 0 ELSE 1 END
            FROM issue_mapping im
            JOIN dataset_issues d USING(issue_key)
            JOIN repository_mapping rm ON rm.repository_key = d.repository_key
            LEFT JOIN canonical_resolutions c USING(issue_key)
            ORDER BY im.issue_id
        """
        for row in _rows(connection.execute(query), settings.processing.batch_size):
            writer.append(
                {
                    "issue_id": row[0],
                    "issue_key": row[1],
                    "repository_id": row[2],
                    "number": row[3],
                    "created_at": utc_datetime(row[4]),
                    "closed_at": utc_datetime(row[5]),
                    "snapshot_count": row[6],
                    "has_resolution": bool(row[7]),
                }
            )
    counts["issues"] = writer.row_count
    with _writer(mappings / "issue_mapping.parquet", "issue_mapping", settings) as writer:
        for issue_id, key in _rows(
            connection.execute("SELECT issue_id, issue_key FROM issue_mapping ORDER BY issue_id"),
            settings.processing.batch_size,
        ):
            writer.append({"issue_id": issue_id, "issue_key": key})

    with _writer(mappings / "developer_mapping.parquet", "developer_mapping", settings) as writer:
        for developer_id, key, login in _rows(
            connection.execute(
                """
                SELECT m.developer_id, m.developer_key, MIN(s.developer_login)
                FROM developer_mapping m
                JOIN developer_spellings s USING(developer_key)
                GROUP BY m.developer_id, m.developer_key
                ORDER BY m.developer_id
                """
            ),
            settings.processing.batch_size,
        ):
            writer.append(
                {
                    "developer_id": developer_id,
                    "developer_key": key,
                    "developer_login": login,
                }
            )

    with _writer(mappings / "label_mapping.parquet", "label_mapping", settings) as writer:
        query = """
            SELECT m.label_id, m.label_key, l.label
            FROM label_mapping m JOIN labels l USING(label_key)
            ORDER BY m.label_id
        """
        for label_id, key, display in _rows(
            connection.execute(query), settings.processing.batch_size
        ):
            writer.append({"label_id": label_id, "label_key": key, "label": display})
    counts["labels"] = writer.row_count

    with _writer(normalized / "issue_repo_edges.parquet", "issue_repo_edges", settings) as writer:
        query = """
            SELECT im.issue_id, rm.repository_id
            FROM issue_mapping im
            JOIN dataset_issues d USING(issue_key)
            JOIN repository_mapping rm ON rm.repository_key = d.repository_key
            ORDER BY im.issue_id
        """
        for issue_id, repository_id in _rows(
            connection.execute(query), settings.processing.batch_size
        ):
            writer.append({"issue_id": issue_id, "repository_id": repository_id})
    counts["issue_repo_edges"] = writer.row_count
    return counts


def _load_mapping(connection: sqlite3.Connection, table: str, id_name: str, key_name: str) -> dict[str, int]:
    return {
        key: identifier
        for identifier, key in connection.execute(
            f"SELECT {id_name}, {key_name} FROM {table}"  # controlled identifiers
        )
    }


def _write_snapshots(
    settings: PreparationSettings,
    connection: sqlite3.Connection,
    normalized: Path,
    counters: Counter[str],
) -> dict[str, int]:
    issue_ids = _load_mapping(connection, "issue_mapping", "issue_id", "issue_key")
    label_ids = _load_mapping(connection, "label_mapping", "label_id", "label_key")
    started = time.monotonic()
    with ExitStack() as stack:
        snapshots = stack.enter_context(
            _writer(normalized / "issue_snapshots.parquet", "issue_snapshots", settings)
        )
        label_edges = stack.enter_context(
            _writer(normalized / "issue_label_edges.parquet", "issue_label_edges", settings)
        )
        for index, document in enumerate(
            iter_bson_documents(
                settings.dataset_path,
                max_document_bytes=settings.processing.max_document_bytes,
            ),
            start=1,
        ):
            try:
                key = issue_key(document.get("owner"), document.get("name"), document.get("number"))
                issue_id = issue_ids[key]
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            try:
                created = utc_datetime(document.get("created_at"))
            except (TypeError, ValueError):
                created = None
            try:
                closed = utc_datetime(document.get("closed_at"))
            except (TypeError, ValueError):
                closed = None
            try:
                before = utc_datetime(document.get("before"))
            except (TypeError, ValueError):
                before = None
            snapshot_type = classify_snapshot(before=before, created_at=created, closed_at=closed)
            labels, _ = normalize_labels(document.get("labels"))
            source_id = object_id_text(document.get("_id"))
            label_category = document.get("label_category")
            snapshots.append(
                {
                    "issue_id": issue_id,
                    "source_object_id": source_id,
                    "snapshot_at": before,
                    "snapshot_type": snapshot_type,
                    "created_at": created,
                    "closed_at": closed if snapshot_type == "closure_snapshot" else None,
                    "title": document.get("title") if isinstance(document.get("title"), str) else None,
                    "body": document.get("body") if isinstance(document.get("body"), str) else None,
                    "labels": labels,
                    "label_category_json": (
                        stable_json(label_category) if isinstance(label_category, dict) else None
                    ),
                    "features_json": stable_json(safe_numeric_features(document)),
                }
            )
            for label in labels:
                normalized_label = label_key(label)
                label_id = label_ids.get(normalized_label)
                if label_id is None:
                    counters["snapshot_missing_label_mapping"] += 1
                    continue
                label_edges.append(
                    {
                        "issue_id": issue_id,
                        "label_id": label_id,
                        "observed_at": before,
                        "snapshot_type": snapshot_type,
                        "source_object_id": source_id,
                    }
                )
            if index % settings.processing.progress_every == 0:
                LOGGER.info("snapshot parquet pass: %d records", index)
    LOGGER.info("snapshot output complete in %.1fs", time.monotonic() - started)
    return {
        "issue_snapshots": snapshots.row_count,
        "issue_label_edges": label_edges.row_count,
    }


def _joined_resolution_query() -> str:
    return """
        SELECT dm.developer_id, im.issue_id, rm.repository_id,
               c.resolver_key, c.resolved_at, c.created_at, c.source_object_id,
               d.created_at, d.closed_at, c.issue_key
        FROM canonical_resolutions c
        JOIN dataset_issues d USING(issue_key)
        JOIN developer_mapping dm ON dm.developer_key = c.resolver_key
        JOIN issue_mapping im ON im.issue_key = c.issue_key
        JOIN repository_mapping rm ON rm.repository_key = d.repository_key
        ORDER BY c.resolved_at, c.issue_key
    """


def _write_interactions_and_splits(
    settings: PreparationSettings,
    connection: sqlite3.Connection,
    normalized: Path,
    graphs: Path,
    counters: Counter[str],
) -> tuple[dict[str, int], dict[str, Any]]:
    split_user_counts: dict[str, Counter[int]] = {
        name: Counter() for name in ("train", "valid", "test")
    }
    split_repositories: dict[str, set[int]] = {
        name: set() for name in ("train", "valid", "test")
    }
    split_issues: dict[str, set[int]] = {name: set() for name in ("train", "valid", "test")}
    split_user_repos: dict[str, dict[int, set[int]]] = {
        name: defaultdict(set) for name in ("train", "valid", "test")
    }
    split_counts: Counter[str] = Counter()
    all_user_counts: Counter[int] = Counter()
    all_user_repos: dict[int, set[int]] = defaultdict(set)
    candidate_scenes: Counter[str] = Counter()

    with ExitStack() as stack:
        developer_issue = stack.enter_context(
            _writer(normalized / "developer_issue_edges.parquet", "developer_issue_edges", settings)
        )
        developer_repo = stack.enter_context(
            _writer(normalized / "developer_repo_edges.parquet", "developer_repo_edges", settings)
        )
        split_writers = {
            name: stack.enter_context(_writer(graphs / f"{name}.parquet", "split", settings))
            for name in ("train", "valid", "test")
        }
        for row in _rows(
            connection.execute(_joined_resolution_query()), settings.processing.batch_size
        ):
            (
                developer_id,
                issue_id,
                repository_id,
                _resolver,
                resolved_text,
                resolved_created_text,
                source_id,
                dataset_created_text,
                closed_text,
                _key,
            ) = row
            interaction_at = utc_datetime(resolved_text)
            if interaction_at is None:
                counters["canonical_missing_interaction_time"] += 1
                continue
            split_name = split_interaction(
                interaction_at,
                train_before=settings.split.train_before,
                valid_before=settings.split.valid_before,
            )
            developer_issue.append(
                {
                    "developer_id": developer_id,
                    "issue_id": issue_id,
                    "interaction_type": "resolved",
                    "interaction_at": interaction_at,
                    "time_source": settings.split.time_source,
                    "source_object_id": source_id,
                }
            )
            developer_repo.append(
                {
                    "developer_id": developer_id,
                    "repository_id": repository_id,
                    "source_issue_id": issue_id,
                    "interaction_type": "contributed_via_issue",
                    "interaction_at": interaction_at,
                }
            )
            split_writers[split_name].append(
                {
                    "developer_id": developer_id,
                    "issue_id": issue_id,
                    "repository_id": repository_id,
                    "interaction_at": interaction_at,
                    "time_source": settings.split.time_source,
                }
            )
            split_counts[split_name] += 1
            split_user_counts[split_name][developer_id] += 1
            split_repositories[split_name].add(repository_id)
            split_issues[split_name].add(issue_id)
            split_user_repos[split_name][developer_id].add(repository_id)
            all_user_counts[developer_id] += 1
            all_user_repos[developer_id].add(repository_id)
            dataset_created = utc_datetime(dataset_created_text)
            resolved_created = utc_datetime(resolved_created_text)
            created = dataset_created or resolved_created
            closed = utc_datetime(closed_text)
            if dataset_created and resolved_created and dataset_created != resolved_created:
                counters["joined_created_at_mismatch"] += 1
            if closed and closed != interaction_at:
                counters["joined_closed_resolved_mismatch"] += 1
            if created and created <= interaction_at and (closed is None or closed >= interaction_at):
                candidate_scenes[split_name] += 1
            else:
                counters["positive_not_historically_eligible"] += 1

    train_users = set(split_user_counts["train"])
    train_user_repos = split_user_repos["train"]
    split_statistics: dict[str, Any] = {}
    for name in ("train", "valid", "test"):
        users = set(split_user_counts[name])
        cold = users - train_users if name != "train" else set()
        represented = users & train_users if name != "train" else users
        new_repo_behaviors = sum(
            count
            for user, repo_counts in _edge_repo_counts(connection, settings, name).items()
            for repository_id, count in repo_counts.items()
            if repository_id not in train_user_repos.get(user, set())
        ) if name != "train" else 0
        project_eval_users = {
            user
            for user, repos in split_user_repos[name].items()
            if train_user_repos.get(user) and bool(repos - train_user_repos[user])
        } if name != "train" else set()
        split_statistics[name] = {
            "interaction_count": split_counts[name],
            "developer_count": len(users),
            "repository_count": len(split_repositories[name]),
            "issue_count": len(split_issues[name]),
            "repeat_contribution_user_count": sum(
                count > 1 for count in split_user_counts[name].values()
            ),
            "single_interaction_user_count": sum(
                count == 1 for count in split_user_counts[name].values()
            ),
            "cold_start_user_count": len(cold),
            "train_graph_represented_user_count": len(represented),
            "train_graph_user_coverage": len(represented) / len(users) if users else 0.0,
            "cross_repository_developer_count": sum(
                len(repos) > 1 for repos in split_user_repos[name].values()
            ),
            "new_repository_behavior_vs_train_count": new_repo_behaviors,
            "project_recommendation_evaluable_user_count": len(project_eval_users),
            "historically_eligible_positive_scene_count": candidate_scenes[name],
        }

    interaction_stats = {
        "developer_issue_edges": sum(split_counts.values()),
        "developer_repo_edges": sum(split_counts.values()),
        "mean_resolved_issues_per_developer": (
            sum(all_user_counts.values()) / len(all_user_counts) if all_user_counts else 0.0
        ),
        "repeat_contribution_user_count": sum(count > 1 for count in all_user_counts.values()),
        "repeat_contribution_user_ratio": (
            sum(count > 1 for count in all_user_counts.values()) / len(all_user_counts)
            if all_user_counts
            else 0.0
        ),
        "cross_repository_developer_count": sum(
            len(repos) > 1 for repos in all_user_repos.values()
        ),
        "cross_repository_developer_ratio": (
            sum(len(repos) > 1 for repos in all_user_repos.values()) / len(all_user_repos)
            if all_user_repos
            else 0.0
        ),
    }
    return interaction_stats, split_statistics


def _edge_repo_counts(
    connection: sqlite3.Connection,
    settings: PreparationSettings,
    split_name: str,
) -> dict[int, Counter[int]]:
    result: dict[int, Counter[int]] = defaultdict(Counter)
    for developer_id, _issue_id, repository_id, _resolver, resolved_text, *_rest in _rows(
        connection.execute(_joined_resolution_query()), settings.processing.batch_size
    ):
        interaction_at = utc_datetime(resolved_text)
        assert interaction_at is not None
        current = split_interaction(
            interaction_at,
            train_before=settings.split.train_before,
            valid_before=settings.split.valid_before,
        )
        if current == split_name:
            result[developer_id][repository_id] += 1
    return result


def _safe_identity(value: Any) -> str | None:
    try:
        return developer_key(value)
    except ValueError:
        return None


def _write_events(
    settings: PreparationSettings,
    connection: sqlite3.Connection,
    normalized: Path,
    counters: Counter[str],
) -> int:
    canonical = {
        key: (issue_id, source_id, utc_datetime(created_at), utc_datetime(resolved_at))
        for key, issue_id, source_id, created_at, resolved_at in connection.execute(
            """
            SELECT c.issue_key, im.issue_id, c.source_object_id, c.created_at, c.resolved_at
            FROM canonical_resolutions c
            JOIN issue_mapping im USING(issue_key)
            JOIN dataset_issues d USING(issue_key)
            """
        )
    }
    with _writer(normalized / "issue_events.parquet", "issue_events", settings) as writer:
        for index, document in enumerate(
            iter_bson_documents(
                settings.resolved_issue_path,
                max_document_bytes=settings.processing.max_document_bytes,
            ),
            start=1,
        ):
            try:
                key = issue_key(document.get("owner"), document.get("name"), document.get("number"))
            except (TypeError, ValueError, OverflowError):
                continue
            match = canonical.get(key)
            if match is None or object_id_text(document.get("_id")) != match[1]:
                continue
            issue_id = match[0]
            events = document.get("events")
            if not isinstance(events, list):
                continue
            for event in events:
                if not isinstance(event, dict):
                    counters["event_invalid_structure"] += 1
                    continue
                event_type = event.get("type")
                if not isinstance(event_type, str) or not event_type.strip():
                    counters["event_invalid_type"] += 1
                    continue
                try:
                    event_at = utc_datetime(event.get("time"))
                except (TypeError, ValueError):
                    event_at = None
                if event_at is None:
                    counters["event_invalid_time"] += 1
                    continue
                if match[2] is not None and event_at < match[2]:
                    counters["event_before_issue_creation"] += 1
                if match[3] is not None and event_at > match[3]:
                    counters["event_after_resolution"] += 1
                writer.append(
                    {
                        "issue_id": issue_id,
                        "actor_key": _safe_identity(event.get("actor")),
                        "commenter_key": _safe_identity(event.get("commenter")),
                        "assignee_key": _safe_identity(event.get("assignee")),
                        "event_type": event_type.strip().casefold(),
                        "event_at": event_at,
                        "label": event.get("label") if isinstance(event.get("label"), str) else None,
                        "commit": event.get("commit") if isinstance(event.get("commit"), str) else None,
                        "has_comment": event.get("comment") is not None,
                    }
                )
            if index % settings.processing.progress_every == 0:
                LOGGER.info("resolved event parquet pass: %d records", index)
    return writer.row_count


def _database_statistics(connection: sqlite3.Connection, counters: Counter[str]) -> dict[str, Any]:
    scalar = lambda query: connection.execute(query).fetchone()[0]
    unique_dataset = scalar("SELECT COUNT(*) FROM dataset_issues")
    canonical = scalar("SELECT COUNT(*) FROM canonical_resolutions")
    joined = scalar(
        """SELECT COUNT(*) FROM canonical_resolutions c
           JOIN dataset_issues d USING(issue_key)"""
    )
    return {
        "dataset_record_count": counters["dataset_records"],
        "dataset_unique_issue_count": unique_dataset,
        "resolved_record_count": counters["resolved_records"],
        "resolved_canonical_issue_count": canonical,
        "successfully_joined_issue_count": joined,
        "unmatched_resolved_issue_count": canonical - joined,
        "duplicate_resolved_record_count": counters["resolved_duplicate_records"],
        "conflicting_resolved_issue_count": counters["resolved_conflicting_issue_keys"],
        "invalid_dataset_issue_count": counters["dataset_invalid_issue_key"],
        "invalid_resolved_record_count": sum(
            counters[key]
            for key in (
                "resolved_invalid_issue_key",
                "resolved_invalid_resolver",
                "resolved_invalid_timestamp",
                "resolved_missing_resolved_at",
                "resolved_created_after_resolved",
            )
        ),
        "single_snapshot_issue_count": scalar(
            "SELECT COUNT(*) FROM dataset_issues WHERE snapshot_count = 1"
        ),
        "multiple_snapshot_issue_count": scalar(
            "SELECT COUNT(*) FROM dataset_issues WHERE snapshot_count > 1"
        ),
        "issue_created_at_conflict_count": scalar(
            "SELECT COUNT(*) FROM dataset_issues WHERE created_conflicts > 0"
        ),
        "issue_closed_at_conflict_count": scalar(
            "SELECT COUNT(*) FROM dataset_issues WHERE closed_conflicts > 0"
        ),
        "repository_spelling_collision_count": scalar(
            "SELECT COUNT(*) FROM repositories WHERE spelling_variants > 1"
        ),
        "developer_case_collision_count": scalar(
            """
            SELECT COUNT(*) FROM (
                SELECT developer_key FROM developer_spellings
                GROUP BY developer_key HAVING COUNT(*) > 1
            )
            """
        ),
    }


def _schema_inventory(normalized: Path, graphs: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    paths = [
        (path, f"normalized/{path.relative_to(normalized).as_posix()}")
        for path in sorted(normalized.rglob("*.parquet"))
    ]
    paths.extend((path, f"graphs/{path.name}") for path in sorted(graphs.glob("*.parquet")))
    for path, relative_path in paths:
        parquet = pq.ParquetFile(path)
        try:
            result[relative_path] = {
                "path": relative_path,
                "row_count": parquet.metadata.num_rows,
                "columns": parquet.schema_arrow.names,
                "schema_version": SCHEMA_VERSION,
            }
        finally:
            parquet.close()
    return result


def _risk_register() -> dict[str, Any]:
    return {
        "allowed_first_version_inputs": [
            "creation snapshot title/body/labels",
            "interactions strictly before prediction time",
            "snapshot-local repository statistics only when timestamp-valid",
        ],
        "forbidden_target_or_future_fields": [
            "resolver",
            "resolved_at as model input",
            "closed_at as model input",
            "resolver_commit_num",
            "prev_resolver_commits",
            "closure-only labels/features for creation-time prediction",
            "validation/test target edges in the training graph",
        ],
        "temporarily_excluded_fields": {
            "reporter_feat": "collection time semantics are not proven historical",
            "owner_feat": "collection time semantics are not proven historical",
            "dataset.comment_users": "identity features have no per-event timestamp",
            "dataset.event_users": "identity features have no per-event timestamp",
            "dataset.comments": "text entries have no per-comment timestamp",
            "dataset.events": "event type strings have no event timestamp",
            "resolved_issue.resolver_commit_num": "describes the target resolver",
            "dataset project counters as static repository features": "values vary by snapshot",
        },
        "candidate_limitations": [
            "candidate_scope=historical_resolved_issue_subset",
            "the source is not a complete archive of all historically open GitHub issues",
            "unobserved interactions are not verified negative feedback",
            "resolved_at is only a proxy for the unavailable contribution-start time",
        ],
        "project_supervision_limit": (
            "developer-repository edges are derived from resolved issues, not independent "
            "Star/Fork behavior"
        ),
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def prepare(settings: PreparationSettings) -> dict[str, Any]:
    """Run all stage-1 passes and publish outputs only after validation."""

    final_directories = (
        settings.normalized_output,
        settings.graph_output,
        settings.validation_output,
    )
    existing = [str(path) for path in final_directories if path.exists()]
    if existing:
        raise DataFileError(
            f"refusing to overwrite existing version directories: {existing}; choose a new output_version"
        )

    raw_verification = verify_raw_inputs(settings)
    staging_root = (
        settings.base.paths.artifact_root
        / ".staging"
        / f"{settings.output_version}-{uuid.uuid4().hex}"
    )
    normalized = staging_root / "normalized"
    graphs = staging_root / "graphs"
    validation = staging_root / "validation"
    normalized.mkdir(parents=True)
    graphs.mkdir(parents=True)
    validation.mkdir(parents=True)
    database_path = staging_root / "stage1-index.sqlite3"
    profiler = FieldProfiler()
    counters: Counter[str] = Counter()
    started = time.monotonic()

    connection = _connect_index(database_path)
    try:
        _scan_dataset(settings, connection, profiler, counters)
        _scan_resolved(settings, connection, profiler, counters)
        _canonicalize_resolutions(connection, counters)
        _create_mappings(connection)
        entity_counts = _write_entities_and_mappings(settings, connection, normalized)
        entity_counts.update(_write_snapshots(settings, connection, normalized, counters))
        interaction_stats, split_stats = _write_interactions_and_splits(
            settings, connection, normalized, graphs, counters
        )
        entity_counts["issue_events"] = _write_events(
            settings, connection, normalized, counters
        )
        join_stats = _database_statistics(connection, counters)
    finally:
        connection.close()

    split_manifest = {
        "schema_version": 1,
        "dataset_version": settings.output_version,
        "source_dataset_version": settings.source_dataset_version,
        "protocol": settings.split.protocol,
        "time_source": settings.split.time_source,
        "train": f"interaction_at < {settings.split.train_before.isoformat()}",
        "valid": (
            f"{settings.split.train_before.isoformat()} <= interaction_at < "
            f"{settings.split.valid_before.isoformat()}"
        ),
        "test": f"interaction_at >= {settings.split.valid_before.isoformat()}",
        "candidate_protocol": {
            "scope": settings.candidates.scope,
            "modes": list(settings.candidates.supported_modes),
            "seed": settings.candidates.seed,
            "materialization": "eligibility function; no Cartesian product persisted",
        },
        "statistics": split_stats,
    }
    _write_json(graphs / "split_manifest.json", split_manifest)

    pre_validation = validate_output_directories(
        settings, normalized_dir=normalized, graph_dir=graphs
    )
    run_settings = replace(
        settings.base,
        config_name=settings.config_name,
        dataset_version=settings.output_version,
    )
    quality = {
        "schema_version": 1,
        "dataset_version": settings.output_version,
        "source_dataset_version": settings.source_dataset_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run": build_run_metadata(run_settings).as_dict(),
        "raw_inputs": raw_verification,
        "raw_data": join_stats,
        "field_profile": profiler.as_dict(),
        "entities_and_relations": entity_counts,
        "interactions": interaction_stats,
        "time_splits": split_stats,
        "anomalies_and_filters": dict(sorted(counters.items())),
        "risk_register": _risk_register(),
        "validation": pre_validation,
        "elapsed_seconds": time.monotonic() - started,
    }
    _write_json(validation / "data_quality.json", quality)
    _write_json(validation / "schema_manifest.json", _schema_inventory(normalized, graphs))

    for source, destination in zip(
        (normalized, graphs, validation), final_directories, strict=True
    ):
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)
    success = {
        "dataset_version": settings.output_version,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "validation": "passed",
    }
    for directory in final_directories:
        _write_json(directory / "_SUCCESS", success)
    try:
        database_path.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            Path(str(database_path) + suffix).unlink(missing_ok=True)
        staging_root.rmdir()
    except OSError:
        LOGGER.warning("staging directory could not be fully removed: %s", staging_root)
    return quality


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    default = Path(__file__).resolve().parents[3] / "configs" / "data_preparation.toml"
    parser.add_argument("--config", type=Path, default=default)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = load_preparation_settings(args.config)
        configure_logging(settings.base.logging.level, settings.base.logging.format)
        quality = prepare(settings)
        LOGGER.info(
            "stage-1 preparation complete: %s joined issues",
            quality["raw_data"]["successfully_joined_issue_count"],
        )
    except (CoderIssueError, OSError, sqlite3.Error, ValueError) as exc:
        LOGGER.exception("stage-1 preparation failed: %s", exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
