from __future__ import annotations

import json
from pathlib import Path
import tempfile

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from coder_issue.inference import CoderIssueScorer


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
