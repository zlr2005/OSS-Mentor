"""Stage-1 configuration and versioned Arrow schemas."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import tomllib
from typing import Any

import pyarrow as pa

from coder_issue.config import Settings, load_settings
from coder_issue.errors import ConfigurationError, DataFileError


SCHEMA_VERSION = "1"
CANDIDATE_SCOPE = "historical_resolved_issue_subset"
TIME_SOURCE = "resolution_proxy"


@dataclass(frozen=True)
class ProcessingOptions:
    batch_size: int
    progress_every: int
    max_document_bytes: int
    parquet_compression: str
    verify_sha256: bool


@dataclass(frozen=True)
class SplitOptions:
    protocol: str
    train_before: datetime
    valid_before: datetime
    time_source: str


@dataclass(frozen=True)
class CandidateOptions:
    scope: str
    supported_modes: tuple[str, ...]
    seed: int


@dataclass(frozen=True)
class PreparationSettings:
    schema_version: int
    config_name: str
    source_dataset_version: str
    output_version: str
    base: Settings
    config_path: Path
    raw_manifest_path: Path
    dataset_path: Path
    resolved_issue_path: Path
    processing: ProcessingOptions
    split: SplitOptions
    candidates: CandidateOptions

    @property
    def normalized_output(self) -> Path:
        return self.base.paths.normalized / self.output_version

    @property
    def graph_output(self) -> Path:
        return self.base.paths.graphs / self.output_version

    @property
    def validation_output(self) -> Path:
        return self.base.paths.artifact_root / "validation" / self.output_version


def _required(mapping: dict[str, Any], key: str, section: str) -> Any:
    try:
        return mapping[key]
    except KeyError as exc:
        raise ConfigurationError(f"missing configuration key: {section}.{key}") from exc


def parse_utc(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_preparation_settings(config_path: str | Path) -> PreparationSettings:
    path = Path(config_path).resolve()
    try:
        with path.open("rb") as stream:
            raw = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"cannot load preparation configuration {path}: {exc}") from exc

    inputs = _required(raw, "inputs", "root")
    processing = _required(raw, "processing", "root")
    split = _required(raw, "split", "root")
    candidates = _required(raw, "candidates", "root")
    base_path = (path.parent / _required(inputs, "base_config", "inputs")).resolve()
    base = load_settings(base_path)
    source_version = str(_required(raw, "source_dataset_version", "root"))
    if source_version != base.dataset_version:
        raise ConfigurationError(
            "source_dataset_version must match the base configuration dataset_version"
        )

    dataset_index = int(_required(inputs, "dataset_file_index", "inputs"))
    resolved_index = int(_required(inputs, "resolved_issue_file_index", "inputs"))
    try:
        dataset_path = base.paths.raw_data_files[dataset_index]
        resolved_path = base.paths.raw_data_files[resolved_index]
    except IndexError as exc:
        raise ConfigurationError("raw data file index is outside base paths.raw_data_files") from exc

    batch_size = int(_required(processing, "batch_size", "processing"))
    progress_every = int(_required(processing, "progress_every", "processing"))
    max_document_bytes = int(_required(processing, "max_document_bytes", "processing"))
    if min(batch_size, progress_every, max_document_bytes) <= 0:
        raise ConfigurationError("processing sizes must be positive")

    train_before = parse_utc(_required(split, "train_before", "split"), "split.train_before")
    valid_before = parse_utc(_required(split, "valid_before", "split"), "split.valid_before")
    if train_before >= valid_before:
        raise ConfigurationError("split.train_before must be earlier than split.valid_before")

    scope = str(_required(candidates, "scope", "candidates"))
    modes = tuple(str(item) for item in _required(candidates, "supported_modes", "candidates"))
    if scope != CANDIDATE_SCOPE:
        raise ConfigurationError(f"unsupported candidate scope: {scope}")
    if set(modes) != {"same_repo", "cross_repo"}:
        raise ConfigurationError("candidate modes must contain same_repo and cross_repo")

    return PreparationSettings(
        schema_version=int(_required(raw, "schema_version", "root")),
        config_name=str(_required(raw, "config_name", "root")),
        source_dataset_version=source_version,
        output_version=str(_required(raw, "output_version", "root")),
        base=base,
        config_path=path,
        raw_manifest_path=(path.parent / _required(inputs, "raw_manifest", "inputs")).resolve(),
        dataset_path=dataset_path,
        resolved_issue_path=resolved_path,
        processing=ProcessingOptions(
            batch_size=batch_size,
            progress_every=progress_every,
            max_document_bytes=max_document_bytes,
            parquet_compression=str(_required(processing, "parquet_compression", "processing")),
            verify_sha256=bool(_required(processing, "verify_sha256", "processing")),
        ),
        split=SplitOptions(
            protocol=str(_required(split, "protocol", "split")),
            train_before=train_before,
            valid_before=valid_before,
            time_source=str(_required(split, "time_source", "split")),
        ),
        candidates=CandidateOptions(
            scope=scope,
            supported_modes=modes,
            seed=int(_required(candidates, "seed", "candidates")),
        ),
    )


def load_raw_manifest(settings: PreparationSettings) -> dict[str, Any]:
    try:
        manifest = json.loads(settings.raw_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataFileError(f"cannot read raw manifest {settings.raw_manifest_path}: {exc}") from exc
    if manifest.get("dataset_version") != settings.source_dataset_version:
        raise DataFileError("raw manifest dataset_version does not match preparation config")
    return manifest


UTC_TIMESTAMP = pa.timestamp("us", tz="UTC")

TABLE_SCHEMAS: dict[str, pa.Schema] = {
    "developers": pa.schema(
        [
            ("developer_id", pa.int64()),
            ("developer_key", pa.string()),
            ("developer_login", pa.string()),
        ]
    ),
    "repositories": pa.schema(
        [
            ("repository_id", pa.int64()),
            ("repository_key", pa.string()),
            ("owner", pa.string()),
            ("name", pa.string()),
        ]
    ),
    "issues": pa.schema(
        [
            ("issue_id", pa.int64()),
            ("issue_key", pa.string()),
            ("repository_id", pa.int64()),
            ("number", pa.int64()),
            ("created_at", UTC_TIMESTAMP),
            ("closed_at", UTC_TIMESTAMP),
            ("snapshot_count", pa.int32()),
            ("has_resolution", pa.bool_()),
        ]
    ),
    "issue_snapshots": pa.schema(
        [
            ("issue_id", pa.int64()),
            ("source_object_id", pa.string()),
            ("snapshot_at", UTC_TIMESTAMP),
            ("snapshot_type", pa.string()),
            ("created_at", UTC_TIMESTAMP),
            ("closed_at", UTC_TIMESTAMP),
            ("title", pa.string()),
            ("body", pa.string()),
            ("labels", pa.list_(pa.string())),
            ("label_category_json", pa.string()),
            ("features_json", pa.string()),
        ]
    ),
    "developer_issue_edges": pa.schema(
        [
            ("developer_id", pa.int64()),
            ("issue_id", pa.int64()),
            ("interaction_type", pa.string()),
            ("interaction_at", UTC_TIMESTAMP),
            ("time_source", pa.string()),
            ("source_object_id", pa.string()),
        ]
    ),
    "developer_repo_edges": pa.schema(
        [
            ("developer_id", pa.int64()),
            ("repository_id", pa.int64()),
            ("source_issue_id", pa.int64()),
            ("interaction_type", pa.string()),
            ("interaction_at", UTC_TIMESTAMP),
        ]
    ),
    "issue_repo_edges": pa.schema(
        [("issue_id", pa.int64()), ("repository_id", pa.int64())]
    ),
    "issue_label_edges": pa.schema(
        [
            ("issue_id", pa.int64()),
            ("label_id", pa.int64()),
            ("observed_at", UTC_TIMESTAMP),
            ("snapshot_type", pa.string()),
            ("source_object_id", pa.string()),
        ]
    ),
    "issue_events": pa.schema(
        [
            ("issue_id", pa.int64()),
            ("actor_key", pa.string()),
            ("commenter_key", pa.string()),
            ("assignee_key", pa.string()),
            ("event_type", pa.string()),
            ("event_at", UTC_TIMESTAMP),
            ("label", pa.string()),
            ("commit", pa.string()),
            ("has_comment", pa.bool_()),
        ]
    ),
    "developer_mapping": pa.schema(
        [
            ("developer_id", pa.int64()),
            ("developer_key", pa.string()),
            ("developer_login", pa.string()),
        ]
    ),
    "repository_mapping": pa.schema(
        [("repository_id", pa.int64()), ("repository_key", pa.string())]
    ),
    "issue_mapping": pa.schema(
        [("issue_id", pa.int64()), ("issue_key", pa.string())]
    ),
    "label_mapping": pa.schema(
        [("label_id", pa.int64()), ("label_key", pa.string()), ("label", pa.string())]
    ),
    "split": pa.schema(
        [
            ("developer_id", pa.int64()),
            ("issue_id", pa.int64()),
            ("repository_id", pa.int64()),
            ("interaction_at", UTC_TIMESTAMP),
            ("time_source", pa.string()),
        ]
    ),
}


def schema_with_metadata(
    schema: pa.Schema,
    *,
    table_name: str,
    settings: PreparationSettings,
) -> pa.Schema:
    metadata = {
        b"schema_version": SCHEMA_VERSION.encode(),
        b"table_name": table_name.encode(),
        b"dataset_version": settings.output_version.encode(),
        b"source_dataset_version": settings.source_dataset_version.encode(),
        b"candidate_scope": settings.candidates.scope.encode(),
        b"time_source": settings.split.time_source.encode(),
    }
    return schema.with_metadata(metadata)
