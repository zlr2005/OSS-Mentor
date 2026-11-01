"""Read-only views over the frozen stage-1 Parquet artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from .config import BaselineSettings


@dataclass(frozen=True)
class Interactions:
    developer_ids: np.ndarray
    issue_ids: np.ndarray
    repository_ids: np.ndarray
    interaction_times: np.ndarray

    def __len__(self) -> int:
        return len(self.issue_ids)


@dataclass(frozen=True)
class IssueCatalog:
    issue_ids: np.ndarray
    repository_ids: np.ndarray
    created_times: np.ndarray
    resolved_times: np.ndarray
    texts: list[str]

    @property
    def size(self) -> int:
        return len(self.issue_ids)


def read_interactions(settings: BaselineSettings, split: str) -> Interactions:
    table = pq.read_table(
        settings.graph_dir / f"{split}.parquet",
        columns=["developer_id", "issue_id", "repository_id", "interaction_at"],
    )
    return Interactions(
        developer_ids=table["developer_id"].to_numpy(zero_copy_only=False).astype(np.int64),
        issue_ids=table["issue_id"].to_numpy(zero_copy_only=False).astype(np.int64),
        repository_ids=table["repository_id"].to_numpy(zero_copy_only=False).astype(np.int64),
        interaction_times=table["interaction_at"].to_numpy(zero_copy_only=False).astype("datetime64[us]"),
    )


def read_catalog(settings: BaselineSettings) -> IssueCatalog:
    issues = pq.read_table(
        settings.normalized_dir / "issues.parquet",
        columns=["issue_id", "repository_id", "created_at"],
    )
    ids = issues["issue_id"].to_numpy(zero_copy_only=False).astype(np.int64)
    repos = issues["repository_id"].to_numpy(zero_copy_only=False).astype(np.int64)
    created = issues["created_at"].to_numpy(zero_copy_only=False).astype("datetime64[us]")
    order = np.argsort(ids)
    ids, repos, created = ids[order], repos[order], created[order]
    if not np.array_equal(ids, np.arange(len(ids), dtype=np.int64)):
        raise ValueError("frozen issue mapping must use contiguous zero-based IDs")

    edges = pq.read_table(
        settings.normalized_dir / "developer_issue_edges.parquet",
        columns=["issue_id", "interaction_at"],
    )
    edge_ids = edges["issue_id"].to_numpy(zero_copy_only=False).astype(np.int64)
    edge_times = edges["interaction_at"].to_numpy(zero_copy_only=False).astype("datetime64[us]")
    resolved = np.full(len(ids), np.datetime64("NaT", "us"), dtype="datetime64[us]")
    resolved[edge_ids] = edge_times
    if np.isnat(resolved).any():
        raise ValueError("historical resolved issue catalog contains missing resolution times")

    texts = [""] * len(ids)
    snapshots = pq.read_table(
        settings.normalized_dir / "issue_snapshots.parquet",
        columns=["issue_id", "snapshot_type", "title", "body"],
    ).to_pydict()
    for issue_id, snapshot_type, title, body in zip(
        snapshots["issue_id"],
        snapshots["snapshot_type"],
        snapshots["title"],
        snapshots["body"],
        strict=True,
    ):
        if snapshot_type == "creation_snapshot":
            texts[int(issue_id)] = f"{title or ''}\n{body or ''}".strip()
    return IssueCatalog(ids, repos, created, resolved, texts)


def training_history(train: Interactions) -> tuple[dict[int, set[int]], set[int]]:
    solved: dict[int, set[int]] = {}
    for developer_id, issue_id in zip(train.developer_ids, train.issue_ids, strict=True):
        solved.setdefault(int(developer_id), set()).add(int(issue_id))
    return solved, set(solved)


def mapping_sizes(settings: BaselineSettings) -> tuple[int, int, int]:
    mappings = settings.normalized_dir / "mappings"
    return (
        pq.ParquetFile(mappings / "developer_mapping.parquet").metadata.num_rows,
        pq.ParquetFile(mappings / "issue_mapping.parquet").metadata.num_rows,
        pq.ParquetFile(mappings / "repository_mapping.parquet").metadata.num_rows,
    )
