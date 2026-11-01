"""Shared ranking metrics for the canonical candidate files."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Iterable

import numpy as np

from .candidates import iter_candidate_batches
from .models import Ranker


def _empty(cutoffs: Iterable[int]) -> dict[str, float]:
    result: dict[str, float] = {}
    for cutoff in cutoffs:
        result.update({f"recall@{cutoff}": 0.0, f"ndcg@{cutoff}": 0.0, f"mrr@{cutoff}": 0.0, f"hit@{cutoff}": 0.0})
    return result


def _summarize(ranks: list[int], cutoffs: tuple[int, ...]) -> dict[str, float | int]:
    output: dict[str, float | int] = {"query_count": len(ranks)}
    if not ranks:
        output.update(_empty(cutoffs))
        return output
    values = np.asarray(ranks)
    for cutoff in cutoffs:
        hits = values <= cutoff
        output[f"recall@{cutoff}"] = float(np.mean(hits))
        output[f"hit@{cutoff}"] = float(np.mean(hits))
        output[f"ndcg@{cutoff}"] = float(np.mean(np.where(hits, 1.0 / np.log2(values + 1), 0.0)))
        output[f"mrr@{cutoff}"] = float(np.mean(np.where(hits, 1.0 / values, 0.0)))
    return output


def evaluate(model: Ranker, candidate_path: Path, cutoffs: tuple[int, ...]) -> dict[str, object]:
    groups: dict[str, list[int]] = defaultdict(list)
    users: dict[str, set[int]] = defaultdict(set)
    for batch in iter_candidate_batches(candidate_path):
        for developer_id, target, is_cold, candidate_values in zip(
            batch["developer_id"],
            batch["target_issue_id"],
            batch["is_cold_start"],
            batch["candidate_issue_ids"],
            strict=True,
        ):
            candidates = np.asarray(candidate_values, dtype=np.int64)
            scores = np.asarray(model.score(int(developer_id), candidates), dtype=np.float64)
            order = np.lexsort((candidates, -scores))
            rank = int(np.flatnonzero(candidates[order] == int(target))[0]) + 1
            predictable = model.is_predictable(int(developer_id))
            labels = ["all", "cold_start" if is_cold else "warm_start"]
            if predictable:
                labels.append("predictable")
            else:
                labels.append("unpredictable")
            for label in labels:
                groups[label].append(rank)
                users[label].add(int(developer_id))
    report: dict[str, object] = {}
    for label in ("all", "predictable", "unpredictable", "warm_start", "cold_start"):
        summary = _summarize(groups[label], cutoffs)
        summary["developer_count"] = len(users[label])
        report[label] = summary
    return report
