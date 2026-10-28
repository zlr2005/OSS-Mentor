"""Sampling from persisted time-valid candidate sets only."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq


@dataclass(frozen=True)
class SampledComparisons:
    query_ids: np.ndarray
    positive_ids: np.ndarray
    unobserved_ids: np.ndarray


class CanonicalCandidateSampler:
    """Deterministic epoch sampler backed by a canonical candidate Parquet.

    The term ``unobserved`` is intentionally used instead of ``negative``:
    absence of a solution edge is not genuine negative feedback.
    """

    def __init__(
        self,
        positives: np.ndarray,
        pools: list[np.ndarray],
        *,
        seed: int,
        query_ids: np.ndarray | None = None,
    ) -> None:
        if len(positives) != len(pools):
            raise ValueError("positive IDs and pools must have the same length")
        if any(len(pool) == 0 for pool in pools):
            raise ValueError("every training query needs at least one time-valid comparison")
        self.positives = positives.astype(np.int64, copy=False)
        self.pools = pools
        self.seed = int(seed)
        self.query_ids = (
            np.arange(len(pools), dtype=np.int64)
            if query_ids is None
            else query_ids.astype(np.int64, copy=False)
        )
        if len(self.query_ids) != len(self.pools):
            raise ValueError("query IDs and pools must have the same length")

    @classmethod
    def from_parquet(cls, path: Path, *, seed: int) -> "CanonicalCandidateSampler":
        table = pq.read_table(path, columns=["query_id", "target_issue_id", "candidate_issue_ids"])
        rows = sorted(
            zip(
                table["query_id"].to_pylist(),
                table["target_issue_id"].to_pylist(),
                table["candidate_issue_ids"].to_pylist(),
                strict=True,
            ),
            key=lambda row: int(row[0]),
        )
        positives: list[int] = []
        pools: list[np.ndarray] = []
        for expected_query, (query_id, target, candidates) in enumerate(rows):
            if int(query_id) != expected_query:
                raise ValueError("candidate query IDs must be contiguous and zero-based")
            positives.append(int(target))
            pool = np.asarray([value for value in candidates if int(value) != int(target)], dtype=np.int64)
            pools.append(pool)
        return cls(np.asarray(positives, dtype=np.int64), pools, seed=seed)

    def sample(self, epoch: int) -> SampledComparisons:
        rng = np.random.default_rng(np.random.SeedSequence([self.seed, int(epoch)]))
        comparisons = np.fromiter(
            (pool[rng.integers(0, len(pool))] for pool in self.pools),
            dtype=np.int64,
            count=len(self.pools),
        )
        return SampledComparisons(
            query_ids=self.query_ids.copy(),
            positive_ids=self.positives.copy(),
            unobserved_ids=comparisons,
        )


class RepositoryCandidateSampler(CanonicalCandidateSampler):
    """Derive project comparisons from the same canonical Issue candidates."""

    @classmethod
    def from_issue_candidates(
        cls,
        path: Path,
        *,
        issue_to_repository: np.ndarray,
        seed: int,
    ) -> "RepositoryCandidateSampler":
        table = pq.read_table(path, columns=["query_id", "repository_id", "candidate_issue_ids"])
        rows = sorted(
            zip(
                table["query_id"].to_pylist(),
                table["repository_id"].to_pylist(),
                table["candidate_issue_ids"].to_pylist(),
                strict=True,
            ),
            key=lambda row: int(row[0]),
        )
        positives: list[int] = []
        pools: list[np.ndarray] = []
        retained_query_ids: list[int] = []
        for expected_query, (query_id, positive_repository, candidates) in enumerate(rows):
            if int(query_id) != expected_query:
                raise ValueError("candidate query IDs must be contiguous and zero-based")
            positive = int(positive_repository)
            candidate_repositories = np.unique(issue_to_repository[np.asarray(candidates, dtype=np.int64)])
            pool = candidate_repositories[candidate_repositories != positive]
            if len(pool) == 0:
                continue
            positives.append(positive)
            pools.append(pool.astype(np.int64))
            retained_query_ids.append(int(query_id))
        return cls(
            np.asarray(positives, dtype=np.int64),
            pools,
            seed=seed,
            query_ids=np.asarray(retained_query_ids, dtype=np.int64),
        )
