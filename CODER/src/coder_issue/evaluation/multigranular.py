"""Issue/project ranking reports for graph models on shared candidates."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import numpy as np
import torch

from coder_issue.baselines.candidates import iter_candidate_batches
from coder_issue.training.trainer import TorchIssueRanker


def _summary(
    ranks: list[int], cutoffs: tuple[int, ...], *, developer_count: int
) -> dict[str, float | int]:
    result: dict[str, float | int] = {
        "query_count": len(ranks), "developer_count": developer_count
    }
    values = np.asarray(ranks, dtype=np.int64)
    for cutoff in cutoffs:
        hit = values <= cutoff
        result[f"recall@{cutoff}"] = float(hit.mean()) if len(values) else 0.0
        result[f"hit@{cutoff}"] = result[f"recall@{cutoff}"]
        result[f"ndcg@{cutoff}"] = (
            float(np.where(hit, 1 / np.log2(values + 1), 0).mean()) if len(values) else 0.0
        )
        result[f"mrr@{cutoff}"] = (
            float(np.where(hit, 1 / values, 0).mean()) if len(values) else 0.0
        )
    return result


def evaluate_issue_groups(
    ranker: TorchIssueRanker,
    candidate_path: Path,
    *,
    cross_repository_users: set[int],
    cutoffs: tuple[int, ...],
) -> dict[str, dict[str, float | int]]:
    groups: dict[str, list[int]] = defaultdict(list)
    users: dict[str, set[int]] = defaultdict(set)
    for batch in iter_candidate_batches(candidate_path):
        for developer, target, cold, values in zip(
            batch["developer_id"], batch["target_issue_id"], batch["is_cold_start"],
            batch["candidate_issue_ids"], strict=True,
        ):
            candidates = np.asarray(values, dtype=np.int64)
            scores = ranker.score(int(developer), candidates)
            order = np.lexsort((candidates, -scores))
            rank = int(np.flatnonzero(candidates[order] == int(target))[0]) + 1
            labels = ["all", "cold_start" if cold else "warm_start"]
            labels.append("predictable" if ranker.is_predictable(int(developer)) else "unpredictable")
            if int(developer) in cross_repository_users:
                labels.append("cross_repository_user")
            for label in labels:
                groups[label].append(rank)
                users[label].add(int(developer))
    return {
        label: _summary(groups[label], cutoffs, developer_count=len(users[label]))
        for label in (
            "all", "predictable", "unpredictable", "warm_start", "cold_start",
            "cross_repository_user",
        )
    }


def evaluate_repository_groups(
    model,
    candidate_path: Path,
    *,
    issue_to_repository: np.ndarray,
    known_developers: np.ndarray,
    cross_repository_users: set[int],
    cutoffs: tuple[int, ...],
    device: torch.device,
) -> dict[str, dict[str, float | int]]:
    if model.repository_graph is None:
        raise ValueError("repository evaluation requires the project module")
    model.eval()
    groups: dict[str, list[int]] = defaultdict(list)
    users: dict[str, set[int]] = defaultdict(set)
    with torch.inference_mode():
        repository_users, repositories = model.repository_graph.propagated_embeddings()
        for batch in iter_candidate_batches(candidate_path):
            for developer, target_repository, cold, issue_values in zip(
                batch["developer_id"], batch["repository_id"], batch["is_cold_start"],
                batch["candidate_issue_ids"], strict=True,
            ):
                candidates = np.unique(issue_to_repository[np.asarray(issue_values, dtype=np.int64)])
                target = int(target_repository)
                if target not in candidates:
                    raise ValueError("target repository missing from derived repository candidates")
                predictable = int(developer) < len(known_developers) and bool(known_developers[int(developer)])
                if predictable:
                    candidate_tensor = torch.as_tensor(candidates, device=device)
                    scores = (repositories[candidate_tensor] * repository_users[int(developer)]).sum(-1)
                    score_values = scores.cpu().numpy()
                else:
                    score_values = np.zeros(len(candidates))
                order = np.lexsort((candidates, -score_values))
                rank = int(np.flatnonzero(candidates[order] == target)[0]) + 1
                labels = ["all", "cold_start" if cold else "warm_start"]
                labels.append("predictable" if predictable else "unpredictable")
                if int(developer) in cross_repository_users:
                    labels.append("cross_repository_user")
                for label in labels:
                    groups[label].append(rank)
                    users[label].add(int(developer))
    return {
        label: _summary(groups[label], cutoffs, developer_count=len(users[label]))
        for label in (
            "all", "predictable", "unpredictable", "warm_start", "cold_start",
            "cross_repository_user",
        )
    }
