"""Field profiling and standalone validation for stage-1 artifacts."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import sys
from typing import Any

import pyarrow.parquet as pq
import pyarrow.compute as pc

from coder_issue.errors import CoderIssueError, DataFileError
from coder_issue.logging_utils import configure_logging
from coder_issue.manifest import sha256_file

from .normalize import bson_type_name
from .schema import (
    PreparationSettings,
    load_preparation_settings,
    load_raw_manifest,
)

LOGGER = logging.getLogger(__name__)


def _is_non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, list, dict, tuple, set)):
        return len(value) > 0
    return True


@dataclass
class FieldProfiler:
    """Collect type and completeness statistics without retaining values."""

    record_counts: Counter[str] = field(default_factory=Counter)
    present_counts: Counter[tuple[str, str]] = field(default_factory=Counter)
    nonnull_counts: Counter[tuple[str, str]] = field(default_factory=Counter)
    nonempty_counts: Counter[tuple[str, str]] = field(default_factory=Counter)
    type_counts: Counter[tuple[str, str, str]] = field(default_factory=Counter)

    def observe(self, collection: str, document: dict[str, Any]) -> None:
        self.record_counts[collection] += 1
        for field_name, value in document.items():
            self._observe_value(collection, field_name, value)
            if isinstance(value, dict):
                for nested_name, nested_value in value.items():
                    self._observe_value(collection, f"{field_name}.{nested_name}", nested_value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        nested_collection = f"{collection}.{field_name}[]"
                        self.record_counts[nested_collection] += 1
                        for nested_name, nested_value in item.items():
                            self._observe_value(nested_collection, nested_name, nested_value)

    def _observe_value(self, collection: str, path: str, value: Any) -> None:
        key = (collection, path)
        self.present_counts[key] += 1
        if value is not None:
            self.nonnull_counts[key] += 1
        if _is_non_empty(value):
            self.nonempty_counts[key] += 1
        self.type_counts[(collection, path, bson_type_name(value))] += 1

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        paths_by_collection: dict[str, set[str]] = defaultdict(set)
        for collection, path in self.present_counts:
            paths_by_collection[collection].add(path)
        for collection, paths in sorted(paths_by_collection.items()):
            total = self.record_counts[collection]
            fields: list[dict[str, Any]] = []
            for path in sorted(paths):
                present = self.present_counts[(collection, path)]
                nonnull = self.nonnull_counts[(collection, path)]
                nonempty = self.nonempty_counts[(collection, path)]
                types = {
                    type_name: count
                    for (item_collection, item_path, type_name), count in sorted(
                        self.type_counts.items()
                    )
                    if item_collection == collection and item_path == path
                }
                fields.append(
                    {
                        "field": path,
                        "present_count": present,
                        "nonnull_count": nonnull,
                        "nonempty_count": nonempty,
                        "present_ratio": present / total if total else 0.0,
                        "nonnull_ratio": nonnull / total if total else 0.0,
                        "nonempty_ratio": nonempty / total if total else 0.0,
                        "types": types,
                    }
                )
            result[collection] = {"record_count": total, "fields": fields}
        return result


def verify_raw_inputs(settings: PreparationSettings) -> dict[str, Any]:
    manifest = load_raw_manifest(settings)
    entries = {item["path"]: item for item in manifest.get("files", [])}
    checked: list[dict[str, Any]] = []
    for path in (settings.dataset_path, settings.resolved_issue_path):
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(settings.base.paths.repository_root).as_posix()
        except ValueError as exc:
            raise DataFileError(f"raw input is outside repository root: {resolved}") from exc
        entry = entries.get(relative)
        if entry is None:
            raise DataFileError(f"raw input is absent from manifest: {relative}")
        actual_size = resolved.stat().st_size
        if actual_size != entry.get("size_bytes"):
            raise DataFileError(f"raw input size differs from manifest: {relative}")
        actual_hash = None
        if settings.processing.verify_sha256:
            LOGGER.info("verifying SHA-256 for %s", relative)
            actual_hash = sha256_file(resolved, settings.base.manifest.chunk_size_bytes)
            if actual_hash != entry.get("sha256"):
                raise DataFileError(f"raw input SHA-256 differs from manifest: {relative}")
        checked.append(
            {
                "path": relative,
                "size_bytes": actual_size,
                "sha256": actual_hash or entry.get("sha256"),
                "verified_sha256": settings.processing.verify_sha256,
            }
        )
    return {"dataset_version": manifest["dataset_version"], "files": checked}


def _mapping_sets(path: Path, id_column: str, key_column: str) -> tuple[set[int], set[str]]:
    table = pq.read_table(path, columns=[id_column, key_column])
    ids = set(table[id_column].to_pylist())
    keys = set(table[key_column].to_pylist())
    if len(ids) != table.num_rows or len(keys) != table.num_rows:
        raise DataFileError(f"duplicate ID or key in {path}")
    if ids != set(range(table.num_rows)):
        raise DataFileError(f"IDs are not contiguous from zero in {path}")
    return ids, keys


def validate_output_directories(
    settings: PreparationSettings,
    *,
    normalized_dir: Path | None = None,
    graph_dir: Path | None = None,
) -> dict[str, Any]:
    """Validate schemas, references, temporal boundaries, and leakage guards."""

    normalized = normalized_dir or settings.normalized_output
    graphs = graph_dir or settings.graph_output
    required_normalized = [
        "developers.parquet",
        "repositories.parquet",
        "issues.parquet",
        "issue_snapshots.parquet",
        "developer_issue_edges.parquet",
        "developer_repo_edges.parquet",
        "issue_repo_edges.parquet",
        "issue_label_edges.parquet",
        "issue_events.parquet",
        "mappings/developer_mapping.parquet",
        "mappings/repository_mapping.parquet",
        "mappings/issue_mapping.parquet",
        "mappings/label_mapping.parquet",
    ]
    required_graphs = ["train.parquet", "valid.parquet", "test.parquet", "split_manifest.json"]
    missing = [str(normalized / item) for item in required_normalized if not (normalized / item).is_file()]
    missing.extend(str(graphs / item) for item in required_graphs if not (graphs / item).is_file())
    if missing:
        raise DataFileError(f"missing stage-1 artifacts: {missing}")

    developer_ids, _ = _mapping_sets(
        normalized / "mappings/developer_mapping.parquet", "developer_id", "developer_key"
    )
    repository_ids, _ = _mapping_sets(
        normalized / "mappings/repository_mapping.parquet", "repository_id", "repository_key"
    )
    issue_ids, _ = _mapping_sets(
        normalized / "mappings/issue_mapping.parquet", "issue_id", "issue_key"
    )
    label_ids, _ = _mapping_sets(
        normalized / "mappings/label_mapping.parquet", "label_id", "label_key"
    )

    checks: dict[str, Any] = {
        "mapping_counts": {
            "developers": len(developer_ids),
            "repositories": len(repository_ids),
            "issues": len(issue_ids),
            "labels": len(label_ids),
        },
        "split_rows": {},
    }

    edges = pq.read_table(
        normalized / "developer_issue_edges.parquet", columns=["developer_id", "issue_id"]
    )
    if not set(edges["developer_id"].to_pylist()).issubset(developer_ids):
        raise DataFileError("developer_issue_edges contains unknown developer IDs")
    edge_issue_values = edges["issue_id"].to_pylist()
    if not set(edge_issue_values).issubset(issue_ids):
        raise DataFileError("developer_issue_edges contains unknown issue IDs")
    if len(edge_issue_values) != len(set(edge_issue_values)):
        raise DataFileError("multiple snapshots or records produced duplicate resolution edges")

    issue_labels = pq.read_table(
        normalized / "issue_label_edges.parquet", columns=["issue_id", "label_id"]
    )
    if not set(issue_labels["issue_id"].to_pylist()).issubset(issue_ids):
        raise DataFileError("issue_label_edges contains unknown issue IDs")
    if not set(issue_labels["label_id"].to_pylist()).issubset(label_ids):
        raise DataFileError("issue_label_edges contains unknown label IDs")

    snapshot_file = pq.ParquetFile(normalized / "issue_snapshots.parquet")
    try:
        forbidden_snapshot_columns = {"resolver", "resolver_commit_num", "resolved_at"}
        overlap = forbidden_snapshot_columns.intersection(snapshot_file.schema.names)
        if overlap:
            raise DataFileError(f"snapshot table exposes target fields: {sorted(overlap)}")
        creation_with_close = 0
        for batch in snapshot_file.iter_batches(
            columns=["snapshot_type", "closed_at"], batch_size=65536
        ):
            creation_mask = pc.equal(batch.column(0), "creation_snapshot")
            closed_mask = pc.invert(pc.is_null(batch.column(1)))
            creation_with_close += int(
                pc.sum(pc.cast(pc.and_(creation_mask, closed_mask), "int64")).as_py()
            )
    finally:
        snapshot_file.close()
    if creation_with_close:
        raise DataFileError("creation snapshots contain future closed_at values")

    split_issue_ids: dict[str, set[int]] = {}
    for split_name in ("train", "valid", "test"):
        table = pq.read_table(graphs / f"{split_name}.parquet")
        times = table["interaction_at"]
        split_issue_ids[split_name] = set(table["issue_id"].to_pylist())
        if split_name == "train" and pc.any(
            pc.greater_equal(times, settings.split.train_before)
        ).as_py():
            raise DataFileError("training graph contains validation/test interactions")
        if split_name == "valid" and (
            pc.any(pc.less(times, settings.split.train_before)).as_py()
            or pc.any(pc.greater_equal(times, settings.split.valid_before)).as_py()
        ):
            raise DataFileError("validation split violates configured boundaries")
        if split_name == "test" and pc.any(
            pc.less(times, settings.split.valid_before)
        ).as_py():
            raise DataFileError("test split violates configured boundaries")
        checks["split_rows"][split_name] = table.num_rows

    if split_issue_ids["train"] & split_issue_ids["valid"]:
        raise DataFileError("validation target issues leak into the training graph")
    if split_issue_ids["train"] & split_issue_ids["test"]:
        raise DataFileError("test target issues leak into the training graph")
    if split_issue_ids["valid"] & split_issue_ids["test"]:
        raise DataFileError("issue targets overlap between validation and test")

    checks["creation_snapshots_with_closed_at"] = creation_with_close
    checks["duplicate_resolution_issue_edges"] = 0
    checks["cross_split_issue_overlap"] = 0
    checks["status"] = "passed"
    return checks


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
        result = validate_output_directories(settings)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    except (CoderIssueError, ValueError) as exc:
        LOGGER.error("stage-1 validation failed: %s", exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
