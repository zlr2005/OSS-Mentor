from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from coder_issue.models.profile import ProfileResidualAdapter

REPO_ROOT = Path(__file__).parents[2]
RC1_DIR = REPO_ROOT / "CODER/artifacts/advanced_adapter_rc1"
INVENTORY_DIR = REPO_ROOT / "docs/artifacts/advanced_adapter_experiments_20261105"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def test_rc1_release_bundle_loads_and_matches_manifest() -> None:
    release = json.loads(
        (RC1_DIR / "release_manifest.json").read_text(encoding="utf-8")
    )
    for name, expected in release["adapter_files"].items():
        path = RC1_DIR / name
        assert path.stat().st_size == expected["bytes"]
        assert _sha256(path) == expected["sha256"]
    assert (RC1_DIR / "_SUCCESS").read_text(encoding="utf-8").strip() == _sha256(
        RC1_DIR / "adapter_manifest.json"
    )

    adapter = ProfileResidualAdapter.load_bundle(
        RC1_DIR, expected_base_model_version="coder-full-search8-v1"
    )
    assert adapter.config.embedding_dim == 32
    assert adapter.config.hidden_dim == 32
    assert len(adapter.config.feature_names) == 23


def test_rc1_test_metrics_are_frozen_and_profile_only_changes_adapter() -> None:
    metrics = json.loads((RC1_DIR / "test_metrics.json").read_text(encoding="utf-8"))
    assert metrics["test_opened_after_winner_selection"] is True
    assert metrics["outcomes"] == 212
    assert metrics["pairs"] == 4240
    assert metrics["coder_profile"]["mrr"] > metrics["coder_original"]["mrr"]
    assert (
        metrics["coder_profile"]["pair_accuracy"]
        > metrics["coder_original"]["pair_accuracy"]
    )


def test_experiment_inventory_contains_every_local_run_and_all_metrics() -> None:
    manifest = json.loads(
        (INVENTORY_DIR / "inventory_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["local_training_records"] == 41
    assert manifest["optimization_records"] == 25
    assert manifest["test_evaluation_records"] == 4
    assert manifest["run_records"] == 70
    assert manifest["metric_records"] == 38301
    for name, expected in manifest["artifacts"].items():
        assert _sha256(INVENTORY_DIR / name) == expected

    with (INVENTORY_DIR / "runs.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 70
    rc1 = next(row for row in rows if row["run_id"] == "optimization/test/round2_best")
    assert float(rc1["test_profile_mrr"]) == metrics_mrr()


def metrics_mrr() -> float:
    metrics = json.loads((RC1_DIR / "test_metrics.json").read_text(encoding="utf-8"))
    return float(metrics["coder_profile"]["mrr"])
