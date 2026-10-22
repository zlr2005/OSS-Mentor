"""Issue/project ranking reports for graph models on shared candidates."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Sequence

import numpy as np
import torch

from coder_issue.baselines.candidates import iter_candidate_batches
from coder_issue.training.trainer import TorchIssueRanker


def summarize_ranked_relevance(
    relevant_ranks: Sequence[Sequence[int]],
    cutoffs: tuple[int, ...],
    *,
    developer_count: int,
    candidate_counts: Sequence[int] | None = None,
) -> dict[str, float | int]:
    """Summarize one or more relevant ranks per query with explicit denominators."""

    if candidate_counts is not None and len(candidate_counts) != len(relevant_ranks):
        raise ValueError("candidate_counts must contain one value per query")
    normalized = [sorted({int(rank) for rank in ranks}) for ranks in relevant_ranks]
    if any(rank <= 0 for ranks in normalized for rank in ranks):
        raise ValueError("relevant ranks are one-based and must be positive")
    evaluable = [ranks for ranks in normalized if ranks]
    result: dict[str, float | int] = {
        "query_count": len(normalized),
        "evaluable_query_count": len(evaluable),
        "skipped_no_positive_query_count": len(normalized) - len(evaluable),
        "positive_count": sum(len(ranks) for ranks in normalized),
        "developer_count": developer_count,
    }
    if candidate_counts is not None:
        counts = np.asarray(candidate_counts, dtype=np.int64)
        if np.any(counts < 0):
            raise ValueError("candidate counts cannot be negative")
        result.update(
            candidate_count_total=int(counts.sum()),
            candidate_count_min=int(counts.min()) if len(counts) else 0,
            candidate_count_max=int(counts.max()) if len(counts) else 0,
            candidate_count_mean=float(counts.mean()) if len(counts) else 0.0,
        )
    for cutoff in cutoffs:
        recalls: list[float] = []
        hits: list[float] = []
        ndcgs: list[float] = []
        reciprocal_ranks: list[float] = []
        for ranks in evaluable:
            observed = [rank for rank in ranks if rank <= cutoff]
            recalls.append(len(observed) / len(ranks))
            hits.append(float(bool(observed)))
            ideal_count = min(len(ranks), cutoff)
            ideal = sum(1.0 / np.log2(index + 1) for index in range(1, ideal_count + 1))
            dcg = sum(1.0 / np.log2(rank + 1) for rank in observed)
            ndcgs.append(dcg / ideal if ideal else 0.0)
            reciprocal_ranks.append(1.0 / min(observed) if observed else 0.0)
        result[f"recall@{cutoff}"] = float(np.mean(recalls)) if recalls else 0.0
        result[f"hit@{cutoff}"] = float(np.mean(hits)) if hits else 0.0
        result[f"ndcg@{cutoff}"] = float(np.mean(ndcgs)) if ndcgs else 0.0
        result[f"mrr@{cutoff}"] = (
            float(np.mean(reciprocal_ranks)) if reciprocal_ranks else 0.0
        )
    return result


def _summary(
    ranks: list[int],
    cutoffs: tuple[int, ...],
    *,
    developer_count: int,
    candidate_counts: list[int],
) -> dict[str, float | int]:
    return summarize_ranked_relevance(
        [[rank] for rank in ranks],
        cutoffs,
        developer_count=developer_count,
        candidate_counts=candidate_counts,
    )


def evaluate_issue_groups(
    ranker: TorchIssueRanker,
    candidate_path: Path,
    *,
    cross_repository_users: set[int],
    cutoffs: tuple[int, ...],
) -> dict[str, dict[str, float | int]]:
    groups: dict[str, list[int]] = defaultdict(list)
    candidate_counts: dict[str, list[int]] = defaultdict(list)
    users: dict[str, set[int]] = defaultdict(set)
    for batch in iter_candidate_batches(candidate_path):
        for developer, target, cold, values in zip(
            batch["developer_id"], batch["target_issue_id"], batch["is_cold_start"],
            batch["candidate_issue_ids"], strict=True,
        ):
            candidates = np.asarray(values, dtype=np.int64)
            scores = ranker.score(int(developer), candidates)
            order = np.lexsort((candidates, -scores))
            positions = np.flatnonzero(candidates[order] == int(target))
            if len(positions) != 1:
                raise ValueError("candidate query must contain its positive target exactly once")
            rank = int(positions[0]) + 1
            labels = ["all", "cold_start" if cold else "warm_start"]
            labels.append("predictable" if ranker.is_predictable(int(developer)) else "unpredictable")
            if int(developer) in cross_repository_users:
                labels.append("cross_repository_user")
            for label in labels:
                groups[label].append(rank)
                candidate_counts[label].append(len(candidates))
                users[label].add(int(developer))
    return {
        label: _summary(
            groups[label],
            cutoffs,
            developer_count=len(users[label]),
            candidate_counts=candidate_counts[label],
        )
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
    candidate_counts: dict[str, list[int]] = defaultdict(list)
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
                    candidate_counts[label].append(len(candidates))
                    users[label].add(int(developer))
    return {
        label: _summary(
            groups[label],
            cutoffs,
            developer_count=len(users[label]),
            candidate_counts=candidate_counts[label],
        )
        for label in (
            "all", "predictable", "unpredictable", "warm_start", "cold_start",
            "cross_repository_user",
        )
    }
