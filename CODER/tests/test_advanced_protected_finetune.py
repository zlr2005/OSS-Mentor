from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
import torch
from coder_issue.models.profile import ProfileAdapterConfig, ProfileResidualAdapter
from coder_issue.training.advanced_profile_data import TrainingPair
from coder_issue.training.advanced_protected_finetune import (
    _epoch_replay_rows,
    _low_coverage_weight,
    _parameter_anchor,
)


def _pair(outcome_id: str, *, observed: int = 4, weight: float = 1.0) -> TrainingPair:
    mask = torch.zeros(23, dtype=torch.bool)
    mask[:observed] = True
    return TrainingPair(
        pair_id=f"pair-{outcome_id}",
        outcome_id=outcome_id,
        developer_key=f"dev-{outcome_id}",
        profile_key=f"profile-{outcome_id}",
        profile_version="research_frozen",
        profile_hash=f"hash-{outcome_id}",
        profile_as_of=datetime(2025, 1, 1, tzinfo=UTC),
        outcome_at=datetime(2025, 2, 1, tzinfo=UTC),
        outcome_type="verified_closing_pr",
        positive_issue_key=f"positive-{outcome_id}",
        comparison_issue_key=f"negative-{outcome_id}",
        weight=weight,
        values=torch.zeros(23),
        observed_mask=mask,
    )


def test_epoch_replay_keeps_all_base_and_targets_ten_percent_supplement() -> None:
    base = [_pair(f"base-{index}") for index in range(9)]
    supplement = [_pair("supplement")]

    first = _epoch_replay_rows(
        base, supplement, supplement_fraction=0.1, seed=17, epoch=2
    )
    second = _epoch_replay_rows(
        base, supplement, supplement_fraction=0.1, seed=17, epoch=2
    )

    assert [row.pair_id for row, _ in first] == [row.pair_id for row, _ in second]
    assert sum(is_base for _, is_base in first) == 9
    assert sum(not is_base for _, is_base in first) == 1
    assert {row.outcome_id for row, is_base in first if is_base} == {
        row.outcome_id for row in base
    }


def test_low_coverage_multiplier_only_changes_sparse_profiles() -> None:
    sparse = _pair("sparse", observed=3, weight=2.0)
    covered = _pair("covered", observed=4, weight=2.0)

    weighted = _low_coverage_weight([sparse, covered], multiplier=1.5)

    assert weighted[0].weight == pytest.approx(3.0)
    assert weighted[1].weight == pytest.approx(2.0)
    assert sparse.weight == pytest.approx(2.0)


def test_parameter_anchor_is_zero_at_teacher_and_positive_after_update() -> None:
    adapter = ProfileResidualAdapter(
        ProfileAdapterConfig(embedding_dim=4, hidden_dim=3)
    )
    reference = {
        name: parameter.detach().clone()
        for name, parameter in adapter.named_parameters()
    }

    assert _parameter_anchor(adapter, reference).item() == pytest.approx(0.0)
    first_name, first_parameter = next(iter(adapter.named_parameters()))
    with torch.no_grad():
        first_parameter.add_(0.5)
    assert first_name in reference
    assert _parameter_anchor(adapter, reference).item() > 0.0


def test_low_coverage_multiplier_rejects_downweighting() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        _low_coverage_weight([replace(_pair("row"), weight=1.0)], multiplier=0.5)
