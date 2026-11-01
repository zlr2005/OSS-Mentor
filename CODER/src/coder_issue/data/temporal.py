"""Temporal split, snapshot, and historical-candidate rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import random
from typing import Iterable, Literal


SnapshotType = Literal["creation_snapshot", "closure_snapshot", "unknown_snapshot"]
SplitName = Literal["train", "valid", "test"]
CandidateMode = Literal["same_repo", "cross_repo"]


def classify_snapshot(
    *,
    before: datetime | None,
    created_at: datetime | None,
    closed_at: datetime | None,
) -> SnapshotType:
    if before is not None and created_at is not None and before == created_at:
        return "creation_snapshot"
    if before is not None and closed_at is not None and before == closed_at:
        return "closure_snapshot"
    return "unknown_snapshot"


def split_interaction(
    interaction_at: datetime,
    *,
    train_before: datetime,
    valid_before: datetime,
) -> SplitName:
    if interaction_at < train_before:
        return "train"
    if interaction_at < valid_before:
        return "valid"
    return "test"


@dataclass(frozen=True)
class HistoricalCandidate:
    issue_id: int
    repository_id: int
    created_at: datetime
    resolved_at: datetime


def is_historical_candidate(
    candidate: HistoricalCandidate,
    *,
    prediction_time: datetime,
    solved_issue_ids: set[int],
    mode: CandidateMode,
    target_repository_id: int | None = None,
) -> bool:
    """Apply observable eligibility to the historical-resolved subset.

    ``resolved_at == prediction_time`` remains eligible because prediction is
    interpreted as the instant immediately before the recorded resolution.
    """

    if candidate.issue_id in solved_issue_ids:
        return False
    if candidate.created_at > prediction_time:
        return False
    if candidate.resolved_at < prediction_time:
        return False
    if mode == "same_repo":
        if target_repository_id is None:
            raise ValueError("same_repo requires target_repository_id")
        return candidate.repository_id == target_repository_id
    if mode == "cross_repo":
        return True
    raise ValueError(f"unsupported candidate mode: {mode}")


def deterministic_sample(
    candidate_ids: Iterable[int],
    *,
    size: int,
    seed: int,
) -> list[int]:
    if size < 0:
        raise ValueError("size must be non-negative")
    ordered = sorted(set(candidate_ids))
    if size >= len(ordered):
        return ordered
    generator = random.Random(seed)
    return sorted(generator.sample(ordered, size))
