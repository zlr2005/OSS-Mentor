"""Deterministic, time-aware candidate materialization shared by every baseline."""

from __future__ import annotations

import hashlib
from pathlib import Path
import random
from typing import Iterator

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from .dataset import Interactions, IssueCatalog


CANDIDATE_SCHEMA = pa.schema(
    [
        ("query_id", pa.int64()),
        ("developer_id", pa.int64()),
        ("target_issue_id", pa.int64()),
        ("repository_id", pa.int64()),
        ("interaction_at", pa.timestamp("us", tz="UTC")),
        ("is_cold_start", pa.bool_()),
        ("candidate_issue_ids", pa.list_(pa.int64())),
    ]
)


class IndexedSet:
    def __init__(self) -> None:
        self.values: list[int] = []
        self.positions: dict[int, int] = {}

    def add(self, value: int) -> None:
        if value in self.positions:
            return
        self.positions[value] = len(self.values)
        self.values.append(value)

    def discard(self, value: int) -> None:
        position = self.positions.pop(value, None)
        if position is None:
            return
        last = self.values.pop()
        if position < len(self.values):
            self.values[position] = last
            self.positions[last] = position


def _query_seed(seed: int, split: str, mode: str, developer_id: int, issue_id: int) -> int:
    raw = f"{seed}:{split}:{mode}:{developer_id}:{issue_id}".encode()
    return int.from_bytes(hashlib.blake2b(raw, digest_size=8).digest(), "little")


def _sample_active(
    values: list[int],
    *,
    excluded: set[int],
    target: int,
    limit: int,
    seed: int,
) -> list[int]:
    if target in excluded:
        raise ValueError("positive target is excluded by training history")
    wanted = max(0, limit - 1)
    if len(values) <= limit * 3:
        eligible = [value for value in values if value not in excluded and value != target]
        rng = random.Random(seed)
        chosen = eligible if len(eligible) <= wanted else rng.sample(eligible, wanted)
    else:
        rng = random.Random(seed)
        chosen_set: set[int] = set()
        attempts = 0
        max_attempts = min(len(values) * 2, wanted * 20)
        while len(chosen_set) < wanted and attempts < max_attempts:
            value = values[rng.randrange(len(values))]
            if value != target and value not in excluded:
                chosen_set.add(value)
            attempts += 1
        if len(chosen_set) < wanted:
            for value in values:
                if value != target and value not in excluded:
                    chosen_set.add(value)
                    if len(chosen_set) == wanted:
                        break
        chosen = list(chosen_set)
    return sorted([target, *chosen])


def materialize_candidates(
    *,
    catalog: IssueCatalog,
    queries: Interactions,
    training_solved: dict[int, set[int]],
    known_developers: set[int],
    split: str,
    mode: str,
    seed: int,
    max_candidates: int,
    output_path: Path,
    batch_size: int = 1024,
) -> dict[str, int]:
    """Create one canonical candidate file without materializing a Cartesian product."""

    if mode not in {"same_repo", "cross_repo"}:
        raise ValueError(f"unsupported candidate mode: {mode}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    create_order = np.argsort(catalog.created_times, kind="stable")
    resolve_order = np.argsort(catalog.resolved_times, kind="stable")
    query_order = np.argsort(queries.interaction_times, kind="stable")
    active_all = IndexedSet()
    active_by_repo: dict[int, IndexedSet] = {}
    create_cursor = resolve_cursor = 0
    rows: list[dict[str, object]] = []
    writer = pq.ParquetWriter(output_path, CANDIDATE_SCHEMA, compression="zstd")
    candidate_total = cold_count = 0
    progressive_solved: dict[int, set[int]] = {}
    pending_resolutions: list[tuple[int, int]] = []
    previous_time: np.datetime64 | None = None
    try:
        for query_index in query_order:
            when = queries.interaction_times[query_index]
            if split == "train" and previous_time is not None and when != previous_time:
                for prior_developer, prior_issue in pending_resolutions:
                    progressive_solved.setdefault(prior_developer, set()).add(prior_issue)
                pending_resolutions.clear()
            previous_time = when
            while create_cursor < catalog.size and catalog.created_times[create_order[create_cursor]] <= when:
                issue_id = int(create_order[create_cursor])
                repo_id = int(catalog.repository_ids[issue_id])
                active_all.add(issue_id)
                active_by_repo.setdefault(repo_id, IndexedSet()).add(issue_id)
                create_cursor += 1
            while resolve_cursor < catalog.size and catalog.resolved_times[resolve_order[resolve_cursor]] < when:
                issue_id = int(resolve_order[resolve_cursor])
                repo_id = int(catalog.repository_ids[issue_id])
                active_all.discard(issue_id)
                active_by_repo.setdefault(repo_id, IndexedSet()).discard(issue_id)
                resolve_cursor += 1
            developer_id = int(queries.developer_ids[query_index])
            target = int(queries.issue_ids[query_index])
            repo_id = int(queries.repository_ids[query_index])
            source = active_all if mode == "cross_repo" else active_by_repo.setdefault(repo_id, IndexedSet())
            if target not in source.positions:
                raise ValueError(f"positive target {target} is not historically eligible")
            candidates = _sample_active(
                source.values,
                excluded=(
                    progressive_solved.get(developer_id, set())
                    if split == "train"
                    else training_solved.get(developer_id, set())
                ),
                target=target,
                limit=max_candidates,
                seed=_query_seed(seed, split, mode, developer_id, target),
            )
            is_cold = developer_id not in known_developers
            rows.append(
                {
                    "query_id": int(query_index),
                    "developer_id": developer_id,
                    "target_issue_id": target,
                    "repository_id": repo_id,
                    "interaction_at": when.astype("datetime64[us]").astype(object),
                    "is_cold_start": is_cold,
                    "candidate_issue_ids": candidates,
                }
            )
            candidate_total += len(candidates)
            cold_count += int(is_cold)
            if split == "train":
                pending_resolutions.append((developer_id, target))
            if len(rows) >= batch_size:
                writer.write_table(pa.Table.from_pylist(rows, schema=CANDIDATE_SCHEMA))
                rows.clear()
        if rows:
            writer.write_table(pa.Table.from_pylist(rows, schema=CANDIDATE_SCHEMA))
    finally:
        writer.close()
    return {
        "query_count": len(queries),
        "candidate_count": candidate_total,
        "cold_start_query_count": cold_count,
    }


def iter_candidate_batches(path: Path, batch_size: int = 512) -> Iterator[dict[str, list]]:
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches(batch_size=batch_size):
        yield batch.to_pydict()


def validate_candidate_file(
    path: Path,
    *,
    catalog: IssueCatalog,
    training_solved: dict[int, set[int]],
    split: str,
    mode: str,
    max_candidates: int,
    expected_queries: int,
) -> None:
    """Recheck every persisted candidate against the frozen temporal protocol."""

    row_count = 0
    progressive_solved: dict[int, set[int]] = {}
    pending: list[tuple[int, int]] = []
    previous_time: np.datetime64 | None = None
    for batch in iter_candidate_batches(path):
        for developer_id, target, repository_id, when_value, values in zip(
            batch["developer_id"], batch["target_issue_id"], batch["repository_id"],
            batch["interaction_at"], batch["candidate_issue_ids"], strict=True,
        ):
            when = np.datetime64(when_value.replace(tzinfo=None), "us")
            if split == "train" and previous_time is not None and when != previous_time:
                for prior_developer, prior_issue in pending:
                    progressive_solved.setdefault(prior_developer, set()).add(prior_issue)
                pending.clear()
            previous_time = when
            candidates = [int(value) for value in values]
            if not candidates or len(candidates) > max_candidates or len(candidates) != len(set(candidates)):
                raise ValueError(f"invalid candidate cardinality at row {row_count}")
            if int(target) not in candidates:
                raise ValueError(f"missing positive target at row {row_count}")
            excluded = (
                progressive_solved.get(int(developer_id), set())
                if split == "train"
                else training_solved.get(int(developer_id), set())
            )
            for issue_id in candidates:
                if issue_id in excluded:
                    raise ValueError(f"solved issue leaked into candidates at row {row_count}")
                if catalog.created_times[issue_id] > when or catalog.resolved_times[issue_id] < when:
                    raise ValueError(f"temporally ineligible issue at row {row_count}")
                if mode == "same_repo" and int(catalog.repository_ids[issue_id]) != int(repository_id):
                    raise ValueError(f"cross-repository issue in same_repo row {row_count}")
            if split == "train":
                pending.append((int(developer_id), int(target)))
            row_count += 1
    if row_count != expected_queries:
        raise ValueError(f"candidate row count mismatch: {row_count} != {expected_queries}")
