from __future__ import annotations

import json
from pathlib import Path
import tempfile

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import torch

from coder_issue.inference import CoderIssueScorer
from coder_issue.models.profile import (
    PROFILE_FEATURE_NAMES,
    ProfileAdapterConfig,
    ProfileResidualAdapter,
)


def _bundle(root: Path) -> tuple[Path, Path, Path]:
    model = root / "model"
    mapping = root / "mapping"
    model.mkdir()
    mapping.mkdir()
    (model / "model_manifest.json").write_text(json.dumps({
        "variant": "coder_issue_repo", "model_version": "m1",
        "dataset_version": "d1", "graph_version": "g1", "encoder_version": "e@r",
    }), encoding="utf-8")
    np.savez(model / "embeddings.npz", user_embeddings=np.asarray([[1, 0], [0, 1]], np.float32),
             issue_embeddings=np.asarray([[1, 0]], np.float32))
    pq.write_table(pa.table({"developer_id": [0, 1], "developer_key": ["alice", "bob"]}),
                   mapping / "developer_mapping.parquet")
    pq.write_table(pa.table({"issue_id": [0], "issue_key": ["known/repo#1"]}),
                   mapping / "issue_mapping.parquet")
    cache = root / "candidates.npz"
    np.savez(cache, issue_keys=np.asarray(["new/repo#2"]),
             issue_embeddings=np.asarray([[0, 1]], np.float32))
    return model, mapping, cache


def _profile(
    *, observed: bool = True, developer_key: str | None = None
) -> dict[str, object]:
    values = [0.0] * len(PROFILE_FEATURE_NAMES)
    mask = [False] * len(PROFILE_FEATURE_NAMES)
    interests: list[str] = []
    if observed:
        index = PROFILE_FEATURE_NAMES.index("current_interest.task.quality")
        values[index] = 1.0
        mask[index] = True
        interests.append("current_interest.task.quality")
    return {
        "schema_version": "enhanced-profile-export-v0.1",
        "profile_state": "confirmed",
        "profile_key": "fixture:profile",
        "profile_version": "profile:v1",
        "developer_key": developer_key,
        "as_of": "2026-10-26T00:00:00Z",
        "feature_schema_version": "enhanced-profile-features-v0.1",
        "feature_names": list(PROFILE_FEATURE_NAMES),
        "values": values,
        "observed_mask": mask,
        "locked_mask": [False] * len(PROFILE_FEATURE_NAMES),
        "current_interests": interests,
        "evidence_refs": [[] for _ in PROFILE_FEATURE_NAMES],
    }


def _adapter(root: Path) -> Path:
    path = root / "adapter"
    adapter = ProfileResidualAdapter(ProfileAdapterConfig(embedding_dim=2, hidden_dim=2))
    with torch.no_grad():
        for parameter in adapter.parameters():
            parameter.zero_()
        adapter.network[2].bias.copy_(torch.tensor([-2.0, 3.0]))
    adapter.save_bundle(
        path,
        base_model_version="m1",
        metadata={"adapter_version": "profile-pilot-v1"},
    )
    return path


def test_known_and_cached_candidates_are_scored_with_provenance() -> None:
    with tempfile.TemporaryDirectory() as directory:
        model, mapping, cache = _bundle(Path(directory))
        scorer = CoderIssueScorer(model, mapping, candidate_cache=cache)
        result = scorer.score(profile=object(), developer_key="Alice", candidates=[
            {"task_candidate_id": 1, "repository": "known/repo", "issue_number": 1},
            {"task_candidate_id": 2, "repository": "new/repo", "issue_number": 2},
        ])
    assert result.profile_mode == "known_history"
    assert [row.coverage_state for row in result.scores] == [
        "fixed_train_graph", "platform_candidate_cache"
    ]
    assert result.scores[0].score > result.scores[1].score


def test_missing_candidate_cache_is_never_silently_replaced() -> None:
    with tempfile.TemporaryDirectory() as directory:
        model, mapping, _ = _bundle(Path(directory))
        scorer = CoderIssueScorer(model, mapping)
        try:
            scorer.score(profile=object(), developer_key=None, candidates=[
                {"task_candidate_id": 3, "repository": "missing/repo", "issue_number": 3}
            ])
        except RuntimeError as exc:
            assert "cache is incomplete" in str(exc)
        else:
            raise AssertionError("missing candidate cache must fail closed")


def test_paired_scoring_changes_only_the_profile_user_residual() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        model, mapping, cache = _bundle(root)
        scorer = CoderIssueScorer(
            model, mapping, candidate_cache=cache, profile_adapter_dir=_adapter(root)
        )
        paired = scorer.score_paired(
            profile=_profile(),
            developer_key="alice",
            candidates=[
                {"task_candidate_id": 1, "repository": "known/repo", "issue_number": 1},
                {"task_candidate_id": 2, "repository": "new/repo", "issue_number": 2},
            ],
        )
    by_id = {row.task_candidate_id: row for row in paired.scores}
    assert by_id[1].rank_original == 1
    assert by_id[2].rank_profile == 1
    assert paired.profile_adapter_version == "profile-pilot-v1"


def test_all_unknown_profile_is_exact_original_fallback() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        model, mapping, cache = _bundle(root)
        scorer = CoderIssueScorer(
            model, mapping, candidate_cache=cache, profile_adapter_dir=_adapter(root)
        )
        paired = scorer.score_paired(
            profile=_profile(observed=False),
            developer_key=None,
            candidates=[
                {"task_candidate_id": 1, "repository": "known/repo", "issue_number": 1},
                {"task_candidate_id": 2, "repository": "new/repo", "issue_number": 2},
            ],
        )
    assert all(row.raw_score_original == row.raw_score_profile for row in paired.scores)


def test_profile_uuid_cannot_be_reused_as_developer_key() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        model, mapping, cache = _bundle(root)
        scorer = CoderIssueScorer(
            model, mapping, candidate_cache=cache, profile_adapter_dir=_adapter(root)
        )
        try:
            scorer.score_paired(
                profile=_profile(developer_key="fixture:profile"),
                developer_key="alice",
                candidates=[
                    {"task_candidate_id": 1, "repository": "known/repo", "issue_number": 1}
                ],
            )
        except ValueError as exc:
            assert "must not reuse" in str(exc)
        else:
            raise AssertionError("profile_key must not be accepted as developer_key")


def test_profile_and_authenticated_developer_keys_must_match() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        model, mapping, cache = _bundle(root)
        scorer = CoderIssueScorer(
            model, mapping, candidate_cache=cache, profile_adapter_dir=_adapter(root)
        )
        try:
            scorer.score_paired(
                profile=_profile(developer_key="bob"),
                developer_key="alice",
                candidates=[
                    {"task_candidate_id": 1, "repository": "known/repo", "issue_number": 1}
                ],
            )
        except ValueError as exc:
            assert "conflicts" in str(exc)
        else:
            raise AssertionError("conflicting developer identities must fail closed")
