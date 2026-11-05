from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import torch

from coder_issue.models.profile import PROFILE_FEATURE_NAMES
from coder_issue.training.advanced_issue_inputs import _content_sha256
from coder_issue.training.advanced_profile_pipeline import (
    AdvancedTrainingConfig,
    FrozenInputs,
)
from coder_issue.training.advanced_profile_supplement import _candidate_row
from coder_issue.training.advanced_supplement_experiment import (
    build_supplement_pairs,
)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _config() -> AdvancedTrainingConfig:
    return AdvancedTrainingConfig(
        adapter_version="supplement-test",
        seed=7,
        data_seed=8,
        hidden_dim=4,
        residual_scale=0.75,
        gate_mode="none",
        feature_dropout=0.0,
        residual_l2=0.0,
        hard_negative_weight=1.0,
        hard_negative_temperature=1.0,
        observation_window_days=90,
        negative_samples_per_positive=20,
        outcome_weights={"verified_closing_pr": 1.0},
        accepted_verification_statuses=("verified",),
        train_end=datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
        validation_end=datetime(2026, 6, 30, 23, 59, 59, tzinfo=timezone.utc),
        epochs=2,
        batch_size=2,
        learning_rate=0.001,
        weight_decay=0.0,
        patience=1,
    )


def test_candidate_versions_do_not_collapse_across_cutoffs() -> None:
    title = "Historical issue"
    body = "Body at cutoff"
    content_hash = _content_sha256(title, body)
    base = {
        "issue_key": "Org/Repo#1",
        "title_at_cutoff": title,
        "body_at_cutoff": body,
        "content_sha256": content_hash,
        "source_url": "https://github.com/org/repo/issues/1",
    }
    first = _candidate_row(
        {**base, "as_of": "2025-01-01T00:00:00Z"},
        role="unlabeled_control",
        evidence_ref="source:first",
    )
    second = _candidate_row(
        {**base, "as_of": "2025-02-01T00:00:00Z"},
        role="unlabeled_control",
        evidence_ref="source:second",
    )
    assert first["issue_key"] == second["issue_key"] == "org/repo#1"
    assert first["candidate_key"] != second["candidate_key"]


def test_supplement_pairs_are_training_only_and_use_versioned_embeddings(
    tmp_path: Path,
) -> None:
    values = [0.0] * len(PROFILE_FEATURE_NAMES)
    observed = [False] * len(PROFILE_FEATURE_NAMES)
    observed[1] = True
    values[1] = 0.5
    profile_hash = hashlib.sha256(b"profile").hexdigest()
    _write_jsonl(
        tmp_path / "profiles.jsonl",
        [
            {
                "schema_version": "enhanced-profile-training-export-v0.1",
                "profile_state": "research_frozen",
                "profile_key": "research-profile:test",
                "profile_version": "research-profile-version:test",
                "profile_hash": profile_hash,
                "developer_key": "developer",
                "as_of": "2025-01-01T00:00:00Z",
                "feature_schema_version": "enhanced-profile-features-v0.1",
                "feature_names": list(PROFILE_FEATURE_NAMES),
                "values": values,
                "observed_mask": observed,
                "locked_mask": [False] * len(PROFILE_FEATURE_NAMES),
                "current_interests": [],
                "evidence_refs": [[] for _ in PROFILE_FEATURE_NAMES],
            }
        ],
    )
    _write_jsonl(
        tmp_path / "outcomes.jsonl",
        [
            {
                "schema_version": "advanced-issue-outcome-v0.1",
                "outcome_id": "outcome-1",
                "developer_key": "developer",
                "issue_key": "org/repo#1",
                "outcome_type": "verified_closing_pr",
                "interaction_at": "2025-01-02T00:00:00Z",
                "outcome_at": "2025-01-03T00:00:00Z",
                "verification_status": "verified",
                "source_url": "https://github.com/org/repo/pull/1",
            }
        ],
    )
    positive_key = "historical-candidate:" + "1" * 64
    comparison_key = "historical-candidate:" + "2" * 64
    positive_hash = "3" * 64
    comparison_hash = "4" * 64
    _write_jsonl(
        tmp_path / "candidate_snapshots.jsonl",
        [
            {
                "candidate_key": positive_key,
                "issue_key": "org/repo#1",
                "as_of": "2025-01-01T00:00:00Z",
                "content_sha256": positive_hash,
            },
            {
                "candidate_key": comparison_key,
                "issue_key": "org/repo#2",
                "as_of": "2025-01-01T00:00:00Z",
                "content_sha256": comparison_hash,
            },
        ],
    )
    _write_jsonl(
        tmp_path / "pair_plan.jsonl",
        [
            {
                "outcome_id": "outcome-1",
                "developer_key": "developer",
                "profile_key": "research-profile:test",
                "profile_version": "research-profile-version:test",
                "profile_hash": profile_hash,
                "profile_as_of": "2025-01-01T00:00:00Z",
                "positive_issue_key": "org/repo#1",
                "positive_candidate_key": positive_key,
                "comparisons": [
                    {
                        "issue_key": "org/repo#2",
                        "candidate_key": comparison_key,
                    }
                ],
                "cutoff_policy": "calendar_quarter",
            }
        ],
    )
    frozen = FrozenInputs(
        base_user=torch.zeros(2),
        issue_embeddings={
            positive_key: torch.tensor([1.0, 0.0]),
            comparison_key: torch.tensor([0.0, 1.0]),
        },
        issue_content_sha256={
            positive_key: positive_hash,
            comparison_key: comparison_hash,
        },
        model_manifest={"model_version": "fixture"},
        model_manifest_sha256="5" * 64,
    )
    pairs, failures, report = build_supplement_pairs(
        supplement_dir=tmp_path,
        frozen=frozen,
        config=_config(),
    )
    assert not failures
    assert len(pairs) == 1
    assert pairs[0].positive_issue_key == positive_key
    assert pairs[0].comparison_issue_key == comparison_key
    assert report["eligible_outcomes"] == 1
    assert report["pairs"] == 1
