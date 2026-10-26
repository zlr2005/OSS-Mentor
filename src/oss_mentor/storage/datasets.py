"""A-owned SQLite persistence for immutable draft datasets and resumable imports.

The caller supplies an initialized connection (D owns connection configuration).
This module never opens a database or changes the live candidate/profile tables.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from oss_mentor.datasets.normalization import canonical_json, normalize_records, record_key
from oss_mentor.datasets.snapshot import load_snapshot, validate_snapshot


# Insertion order follows foreign-key dependencies. Table/column SQL identifiers
# below are constants, never supplied by input records or command-line arguments.
TABLES = {
    "repositories": ("midterm_dataset_repository", ("repository_key",)),
    "developers": ("midterm_dataset_developer", ("developer_key",)),
    "files": ("midterm_dataset_file", ("file_key", "repository_key")),
    "code_documents": ("midterm_dataset_code", ("file_version_key", "file_key", "commit_sha")),
    "contributions": ("midterm_dataset_contribution", ("record_key", "developer_key", "file_version_key", "event_at")),
    "project_behaviors": ("midterm_dataset_behavior", ("record_key", "developer_key", "repository_key", "event_at")),
    "issues": ("midterm_dataset_issue", ("issue_key", "repository_key")),
    "issue_file_links": ("midterm_dataset_issue_file", ("record_key", "issue_key", "file_key", "available_at")),
    "file_aliases": ("midterm_dataset_file_alias", ("record_key", "file_key")),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DatasetStore:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        if connection.in_transaction:
            raise ValueError("DatasetStore requires a connection without an active transaction")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise ValueError("DatasetStore requires foreign_keys=ON")

    @contextmanager
    def _transaction(self, *, write: bool = True):
        if self.connection.in_transaction:
            raise ValueError("DatasetStore cannot nest inside another transaction")
        self.connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        try:
            yield
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def _batch(self, version: str) -> dict:
        cursor = self.connection.execute(
            "SELECT batch_id, dataset_version, status, checkpoint, total_items, duplicates_json, error_type "
            "FROM midterm_dataset_import WHERE dataset_version = ?", (version,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError(f"Unknown dataset version: {version}")
        result = dict(zip((item[0] for item in cursor.description), row))
        result["duplicates_removed"] = json.loads(result.pop("duplicates_json"))
        return result

    def status(self, version: str) -> dict:
        """Return status and stored counts; incomplete batches must not be consumed."""
        with self._transaction(write=False):
            result = self._batch(version)
            manifest_row = self.connection.execute(
                "SELECT manifest_json FROM midterm_dataset_snapshot WHERE dataset_version = ?", (version,),
            ).fetchone()
            manifest = json.loads(manifest_row[0])
            if "dataset_lifecycle" in manifest:
                result["dataset_lifecycle"] = manifest["dataset_lifecycle"]
                result["frozen"] = manifest["frozen"]
            result["entity_counts"] = {
                kind: self.connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE dataset_version = ?", (version,),
                ).fetchone()[0]
                for kind, (table, _) in TABLES.items()
            }
        return result

    def import_snapshot(self, directory: Path, *, chunk_size: int = 100) -> dict:
        """Validate before writing, deduplicate, then commit rows and checkpoint together.

        A rerun with identical input resumes the same batch. A version cannot be
        reused for changed input. Exact duplicate relation rows are collapsed;
        conflicting payloads with the same identity fail before database writes.
        """
        if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or chunk_size < 1:
            raise ValueError("chunk_size must be a positive integer")
        manifest, records, assets = load_snapshot(directory)
        normalized, duplicates = normalize_records(records)
        version = manifest["dataset_version"]
        if not isinstance(version, str) or not version.strip():
            raise ValueError("dataset_version must be a nonempty string")
        manifest_json = canonical_json(manifest)
        manifest_hash = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
        batch_id = hashlib.sha256(canonical_json([version, manifest_hash]).encode("utf-8")).hexdigest()
        items = [("assets", name, payload) for name, payload in sorted(assets.items())]
        for kind in TABLES:
            items.extend((kind, ordinal, row) for ordinal, row in enumerate(normalized[kind]))

        with self._transaction():
            previous = self.connection.execute(
                "SELECT manifest_sha256 FROM midterm_dataset_snapshot WHERE dataset_version = ?", (version,),
            ).fetchone()
            if previous is not None and previous[0] != manifest_hash:
                raise ValueError("Dataset version already registered with different content; use a new version")
            if previous is None:
                self.connection.execute(
                    "INSERT INTO midterm_dataset_snapshot VALUES (?, ?, ?, ?, ?, ?)",
                    (version, manifest["schema_version"], manifest_hash, manifest_json,
                     int(manifest["fixture_only"]), _now()),
                )
                self.connection.execute(
                    "INSERT INTO midterm_dataset_import "
                    "(batch_id, dataset_version, status, total_items, duplicates_json, updated_at) "
                    "VALUES (?, ?, 'running', ?, ?, ?)",
                    (batch_id, version, len(items), canonical_json(duplicates), _now()),
                )
            batch = self._batch(version)
            if batch["total_items"] != len(items):
                raise ValueError("Import plan changed; use a new dataset/normalization version")

        try:
            while True:
                with self._transaction():
                    batch = self._batch(version)
                    if batch["status"] == "completed":
                        break
                    start = batch["checkpoint"]
                    end = min(start + chunk_size, len(items))
                    for kind, ordinal, row in items[start:end]:
                        if kind == "assets":
                            self.connection.execute(
                                "INSERT INTO midterm_dataset_asset VALUES (?, ?, ?, ?)",
                                (version, ordinal, hashlib.sha256(row).hexdigest(), row),
                            )
                        else:
                            self._insert_record(version, kind, ordinal, row)
                    self.connection.execute(
                        "UPDATE midterm_dataset_import SET checkpoint = ?, status = ?, "
                        "error_type = NULL, updated_at = ? WHERE dataset_version = ?",
                        (end, "completed" if end == len(items) else "running", _now(), version),
                    )
                if end == len(items):
                    break
        except Exception as error:
            with self._transaction():
                # Persist the type only: source exception text can contain secrets.
                self.connection.execute(
                    "UPDATE midterm_dataset_import SET status = 'failed', error_type = ?, updated_at = ? "
                    "WHERE dataset_version = ? AND status != 'completed'",
                    (type(error).__name__, _now(), version),
                )
            raise
        return self.status(version)

    def _insert_record(self, version: str, kind: str, ordinal: int, row: dict) -> None:
        table, fields = TABLES[kind]
        values = [record_key(kind, row) if field == "record_key" else row[field] for field in fields]
        columns = ("dataset_version", *fields, "ordinal", "payload_json")
        placeholders = ", ".join("?" for _ in columns)
        self.connection.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
            (version, *values, ordinal, canonical_json(row)),
        )

    def export_snapshot(self, version: str, target: Path) -> dict:
        """Export a complete, portable normalized snapshot with a new derived version.

        Files are reconstructed from DB blobs, so the original import directory
        need not exist. Existing output directories are never overwritten.
        """
        target = target.resolve()
        if target.exists():
            raise ValueError("Export destination already exists; choose a new directory")
        with self._transaction(write=False):
            if self._batch(version)["status"] != "completed":
                raise ValueError("Cannot export an incomplete import")
            row = self.connection.execute(
                "SELECT manifest_json FROM midterm_dataset_snapshot WHERE dataset_version = ?", (version,),
            ).fetchone()
            manifest = json.loads(row[0])
            records = {
                kind: [json.loads(row[0]) for row in self.connection.execute(
                    f"SELECT payload_json FROM {table} WHERE dataset_version = ? ORDER BY ordinal", (version,),
                )]
                for kind, (table, _) in TABLES.items()
            }
            assets = {}
            for name, digest, content in self.connection.execute(
                "SELECT relative_path, sha256, content FROM midterm_dataset_asset WHERE dataset_version = ?", (version,),
            ):
                if hashlib.sha256(content).hexdigest() != digest:
                    raise ValueError(f"Stored asset checksum mismatch: {name}")
                assets[name] = content
        assets["records.json"] = (json.dumps(records, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        manifest.update(
            parent_dataset_version=version, normalization_version="midterm-a-normalize-v2",
            entity_counts={kind: len(rows) for kind, rows in records.items()},
        )
        manifest["files"] = [
            {"path": name, "format": "git-object" if name.endswith(".bin") else "gzip-json" if name.endswith(".json.gz") else "json" if name.endswith(".json") else "text",
             "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            for name, content in sorted(assets.items())
        ]
        # Metadata, provenance and non-record assets are versioned as well.
        manifest.pop("dataset_version")
        digest = hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()
        manifest["dataset_version"] = f"{version}-normalized-{digest[:12]}"
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".midterm-a-export-", dir=target.parent) as temporary:
            staging = Path(temporary) / "snapshot"
            staging.mkdir()
            for name, content in assets.items():
                path = (staging / name).resolve()
                if not path.is_relative_to(staging.resolve()):
                    raise ValueError("Stored asset path escapes export directory")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            (staging / "manifest.json").write_bytes(
                (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            )
            report = validate_snapshot(staging)
            staging.rename(target)
        return report
