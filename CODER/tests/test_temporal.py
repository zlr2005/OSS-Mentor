from __future__ import annotations

from datetime import datetime, timezone

from coder_issue.data.temporal import (
    HistoricalCandidate,
    classify_snapshot,
    deterministic_sample,
    is_historical_candidate,
    split_interaction,
)


UTC = timezone.utc


def test_snapshot_and_split_boundaries() -> None:
    created = datetime(2020, 1, 1, tzinfo=UTC)
    closed = datetime(2021, 1, 1, tzinfo=UTC)
    assert classify_snapshot(before=created, created_at=created, closed_at=closed) == "creation_snapshot"
    assert classify_snapshot(before=closed, created_at=created, closed_at=closed) == "closure_snapshot"
    assert split_interaction(
        closed,
        train_before=datetime(2021, 1, 1, tzinfo=UTC),
        valid_before=datetime(2022, 1, 1, tzinfo=UTC),
    ) == "valid"


def test_historical_candidate_excludes_future_and_solved_items() -> None:
    prediction = datetime(2021, 1, 1, tzinfo=UTC)
    candidate = HistoricalCandidate(
        issue_id=1,
        repository_id=2,
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
        resolved_at=prediction,
    )
    assert is_historical_candidate(
        candidate,
        prediction_time=prediction,
        solved_issue_ids=set(),
        mode="same_repo",
        target_repository_id=2,
    )
    assert not is_historical_candidate(
        candidate,
        prediction_time=prediction,
        solved_issue_ids={1},
        mode="cross_repo",
    )
    future = HistoricalCandidate(
        issue_id=2,
        repository_id=2,
        created_at=datetime(2021, 1, 2, tzinfo=UTC),
        resolved_at=datetime(2021, 2, 1, tzinfo=UTC),
    )
    assert not is_historical_candidate(
        future,
        prediction_time=prediction,
        solved_issue_ids=set(),
        mode="cross_repo",
    )


def test_candidate_sampling_is_deterministic() -> None:
    assert deterministic_sample(range(20), size=5, seed=7) == deterministic_sample(
        reversed(range(20)), size=5, seed=7
    )
