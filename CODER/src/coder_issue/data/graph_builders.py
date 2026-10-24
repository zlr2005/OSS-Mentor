"""Construct frozen training graphs without validation/test edges."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow.compute as pc
import pyarrow.parquet as pq
import torch
from torch import Tensor

from coder_issue.models.gat import bidirectional_structural_edges
from coder_issue.models.lightgcn import normalized_bipartite_adjacency


@dataclass(frozen=True)
class TrainingGraphs:
    issue_adjacency: Tensor
    repository_adjacency: Tensor
    structural_edge_index: Tensor
    issue_to_repository: Tensor
    user_ids: Tensor
    issue_ids: Tensor
    repository_ids: Tensor


def build_training_graphs(
    *,
    graph_dir: Path,
    normalized_dir: Path,
    num_users: int,
    num_issues: int,
    num_repositories: int,
    num_labels: int,
) -> TrainingGraphs:
    train = pq.read_table(
        graph_dir / "train.parquet", columns=["developer_id", "issue_id", "repository_id"]
    )
    user_ids = torch.from_numpy(train["developer_id"].to_numpy(zero_copy_only=False).astype(np.int64))
    issue_ids = torch.from_numpy(train["issue_id"].to_numpy(zero_copy_only=False).astype(np.int64))
    repository_ids = torch.from_numpy(
        train["repository_id"].to_numpy(zero_copy_only=False).astype(np.int64)
    )
    issue_adjacency = normalized_bipartite_adjacency(
        user_ids, issue_ids, num_users=num_users, num_items=num_issues
    )
    pairs = torch.unique(torch.stack([user_ids, repository_ids], dim=1), dim=0)
    repository_adjacency = normalized_bipartite_adjacency(
        pairs[:, 0], pairs[:, 1], num_users=num_users, num_items=num_repositories
    )
    issue_repo = pq.read_table(
        normalized_dir / "issue_repo_edges.parquet", columns=["issue_id", "repository_id"]
    )
    issue_repo_tensor = torch.from_numpy(
        np.column_stack(
            [
                issue_repo["issue_id"].to_numpy(zero_copy_only=False),
                issue_repo["repository_id"].to_numpy(zero_copy_only=False),
            ]
        ).astype(np.int64)
    )
    issue_to_repository = torch.full((num_issues,), -1, dtype=torch.long)
    issue_to_repository[issue_repo_tensor[:, 0]] = issue_repo_tensor[:, 1]
    if torch.any(issue_to_repository < 0):
        raise ValueError("every Issue must have exactly one repository mapping")
    labels = pq.read_table(
        normalized_dir / "issue_label_edges.parquet",
        columns=["issue_id", "label_id", "snapshot_type"],
    )
    labels = labels.filter(pc.equal(labels["snapshot_type"], "creation_snapshot"))
    issue_label_tensor = torch.from_numpy(
        np.column_stack(
            [
                labels["issue_id"].to_numpy(zero_copy_only=False),
                labels["label_id"].to_numpy(zero_copy_only=False),
            ]
        ).astype(np.int64)
    )
    structural = bidirectional_structural_edges(
        issue_repo_tensor,
        issue_label_tensor,
        num_repositories=num_repositories,
        num_issues=num_issues,
        num_labels=num_labels,
    )
    return TrainingGraphs(
        issue_adjacency, repository_adjacency, structural, issue_to_repository,
        user_ids, issue_ids, repository_ids,
    )
