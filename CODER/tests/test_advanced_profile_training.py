from __future__ import annotations

from argparse import Namespace
import hashlib
import json
from pathlib import Path
import tempfile

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import torch

from coder_issue.models.profile import (
    PROFILE_FEATURE_NAMES,
    ProfileAdapterConfig,
    ProfileResidualAdapter,
    tensorize_confirmed_profile,
    tensorize_training_profile,
)
from coder_issue.training.advanced_profile_pipeline import run_pipeline


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
    )


def _profile(developer: str, version: str, as_of: str) -> dict[str, object]:
    values = [0.0] * len(PROFILE_FEATURE_NAMES)
    observed = [False] * len(PROFILE_FEATURE_NAMES)
    index = PROFILE_FEATURE_NAMES.index("current_interest.task.quality")
    values[index] = 1.0
    observed[index] = True
    return {
        "schema_version": "enhanced-profile-training-export-v0.1",
        "profile_state": "research_frozen",
        "profile_key": f"profile-{developer}",
        "profile_version": version,
        "developer_key": developer,
        "as_of": as_of,
        "feature_schema_version": "enhanced-profile-features-v0.1",
        "feature_names": list(PROFILE_FEATURE_NAMES),
        "values": values,
        "observed_mask": observed,
        "locked_mask": [False] * len(PROFILE_FEATURE_NAMES),
        "current_interests": ["current_interest.task.quality"],
        "evidence_refs": [[] for _ in PROFILE_FEATURE_NAMES],
        "profile_hash": hashlib.sha256(f"{developer}:{version}".encode()).hexdigest(),
    }


def _fixture(root: Path) -> Namespace:
    model = root / "model"
    model.mkdir()
    (model / "model_manifest.json").write_text(
        json.dumps(
            {
                "variant": "coder_issue_repo",
                "model_version": "coder-fixture-v1",
                "dataset_version": "data-v1",
                "graph_version": "graph-v1",
                "encoder_version": "encoder-v1",
            }
        ),
        encoding="utf-8",
    )
    np.savez(
        model / "embeddings.npz",
        user_embeddings=np.asarray([[0.0, 0.0], [0.0, 0.0]], dtype=np.float32),
        issue_embeddings=np.asarray([[1.0, 0.0], [-1.0, 0.0]], dtype=np.float32),
    )
    cache = root / "issues.npz"
    issue_keys = ["org/repo#1", "org/repo#2"]
    np.savez(
        cache,
        issue_keys=np.asarray(issue_keys),
        issue_embeddings=np.asarray([[1.0, 0.0], [-1.0, 0.0]], dtype=np.float32),
        content_sha256=np.asarray(
            [hashlib.sha256(issue_key.encode()).hexdigest() for issue_key in issue_keys]
        ),
    )
    profiles = root / "profiles.jsonl"
    _write_jsonl(
        profiles,
        [
            _profile("train-dev", "v1", "2025-01-01T00:00:00Z"),
            _profile("valid-dev", "v1", "2025-07-01T00:00:00Z"),
            _profile("test-dev", "v1", "2026-01-01T00:00:00Z"),
        ],
    )
    outcomes = root / "outcomes.jsonl"
    _write_jsonl(
        outcomes,
        [
            {
                "schema_version": "advanced-issue-outcome-v0.1",
                "outcome_id": "outcome-train",
                "developer_key": "train-dev",
                "issue_key": "org/repo#1",
                "outcome_type": "verified_closing_pr",
                "interaction_at": "2025-01-15T00:00:00Z",
                "outcome_at": "2025-02-01T00:00:00Z",
                "verification_status": "verified",
                "source_url": "https://github.com/org/repo/pull/1",
            },
            {
                "schema_version": "advanced-issue-outcome-v0.1",
                "outcome_id": "outcome-valid",
                "developer_key": "valid-dev",
                "issue_key": "org/repo#1",
                "outcome_type": "verified_closing_pr",
                "interaction_at": "2025-07-15T00:00:00Z",
                "outcome_at": "2025-08-01T00:00:00Z",
                "verification_status": "verified",
                "source_url": "https://github.com/org/repo/pull/2",
            },
            {
                "schema_version": "advanced-issue-outcome-v0.1",
                "outcome_id": "outcome-test",
                "developer_key": "test-dev",
                "issue_key": "org/repo#1",
                "outcome_type": "verified_closing_pr",
                "interaction_at": "2026-01-15T00:00:00Z",
                "outcome_at": "2026-02-01T00:00:00Z",
                "verification_status": "verified",
                "source_url": "https://github.com/org/repo/pull/3",
            },
        ],
    )
    candidates = root / "candidates.jsonl"
    _write_jsonl(
        candidates,
        [
            {
                "schema_version": "advanced-historical-candidate-v0.1",
                "issue_key": issue_key,
                "available_from": "2024-01-01T00:00:00Z",
                "available_until": None,
                "content_sha256": hashlib.sha256(issue_key.encode()).hexdigest(),
            }
            for issue_key in ("org/repo#1", "org/repo#2")
        ],
    )
    config = root / "config.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": "advanced-adapter-training-config-v0.1",
                "adapter_version": "advanced-adapter-fixture-v1",
                "seed": 7,
                "hidden_dim": 4,
                "residual_scale": 1.0,
                "observation_window_days": 90,
                "negative_samples_per_positive": 1,
                "outcome_weights": {"verified_closing_pr": 1.0},
                "accepted_verification_statuses": ["verified"],
                "train_end": "2025-06-30T23:59:59Z",
                "validation_end": "2025-12-31T23:59:59Z",
                "epochs": 20,
                "batch_size": 1,
                "learning_rate": 0.05,
                "weight_decay": 0.0,
                "patience": 4,
            }
        ),
        encoding="utf-8",
    )
    return Namespace(
        mode="train",
        config=str(config),
        profiles=str(profiles),
        outcomes=str(outcomes),
        candidates=str(candidates),
        model_dir=str(model),
        issue_cache=str(cache),
        output_dir=str(root / "output"),
        device="cpu",
    )


def test_training_snapshot_has_a_separate_state_contract() -> None:
    snapshot = _profile("developer", "v1", "2025-01-01T00:00:00Z")
    config = ProfileAdapterConfig(embedding_dim=2, hidden_dim=2)
    assert tensorize_training_profile(snapshot, config=config).developer_key == "developer"
    try:
        tensorize_confirmed_profile(snapshot, config=config)
    except ValueError as exc:
        assert "schema_version" in str(exc) or "confirmed" in str(exc)
    else:
        raise AssertionError("research_frozen profile must not pass online validation")


def test_end_to_end_training_writes_a_loadable_reproducible_bundle() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        args = _fixture(root)
        before = (Path(args.model_dir) / "embeddings.npz").read_bytes()
        result = run_pipeline(args)
        output = Path(args.output_dir)
        after = (Path(args.model_dir) / "embeddings.npz").read_bytes()
        manifest = json.loads((output / "adapter_manifest.json").read_text(encoding="utf-8"))
        loaded = ProfileResidualAdapter.load_bundle(
            output, expected_base_model_version="coder-fixture-v1"
        )

        assert before == after
        assert result["split_pairs"] == {"train": 1, "validation": 1, "test": 1}
        assert result["metrics"]["coder_profile"]["test"]["outcomes"] == 1
        assert result["metrics"]["coder_original"]["test"]["outcomes"] == 1
        assert "mrr" in result["metrics"]["paired_delta"]["test"]
        assert manifest["base_user_mode"] == "population_mean"
        assert manifest["service_track"] == "growth"
        assert manifest["weights_sha256"] == hashlib.sha256(
            (output / "weights.pt").read_bytes()
        ).hexdigest()
        assert isinstance(loaded, ProfileResidualAdapter)
        for name in (
            "audit_report.json",
            "failures.jsonl",
            "training_metrics.json",
            "split_manifest.json",
            "weights.pt",
            "adapter_manifest.json",
            "_SUCCESS",
        ):
            assert (output / name).is_file()


def test_audit_mode_reports_future_profile_without_training() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        args = _fixture(root)
        rows = [
            _profile("train-dev", "future", "2025-03-01T00:00:00Z"),
            _profile("valid-dev", "v1", "2025-07-01T00:00:00Z"),
            _profile("test-dev", "v1", "2026-01-01T00:00:00Z"),
        ]
        _write_jsonl(Path(args.profiles), rows)
        args.mode = "audit"
        report = run_pipeline(args)
        failures = (Path(args.output_dir) / "failures.jsonl").read_text(encoding="utf-8")

        assert report["pairs"] == 2
        assert "no earlier profile" in failures
        assert not (Path(args.output_dir) / "weights.pt").exists()


def test_audit_accepts_parquet_contract_inputs() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        args = _fixture(root)
        for field in ("profiles", "outcomes", "candidates"):
            source = Path(getattr(args, field))
            rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
            target = source.with_suffix(".parquet")
            pq.write_table(pa.Table.from_pylist(rows), target)
            setattr(args, field, str(target))
        args.mode = "audit"
        report = run_pipeline(args)
        assert report["split_pairs"] == {"train": 1, "validation": 1, "test": 1}
