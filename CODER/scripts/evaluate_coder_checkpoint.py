"""Evaluate an already selected CODER best checkpoint without retraining it."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import torch

from coder_issue.baselines.dataset import mapping_sizes, read_interactions
from coder_issue.data.graph_builders import build_training_graphs
from coder_issue.evaluation.groups import cross_repository_users
from coder_issue.evaluation.multigranular import evaluate_issue_groups
from coder_issue.models.text_encoder import EmbeddingCache
from coder_issue.training.config import load_neural_settings, validate_neural_inputs
from coder_issue.training.pipeline import _build_trial_model
from coder_issue.training.selection import restore_best_checkpoint
from coder_issue.training.trainer import TorchIssueRanker


def _semantic_array(settings, encoder_version: str, issue_count: int) -> np.ndarray:
    matches = [
        option
        for option in settings.encoders
        if encoder_version == f"{option.name}@{option.revision}"
    ]
    if len(matches) != 1:
        raise ValueError("checkpoint encoder identity does not match the evaluation config")
    option = matches[0]
    root = settings.embedding_dir / f"{option.name.replace('/', '__')}@{option.revision}"
    cache = EmbeddingCache(root)
    status, reasons = cache.inspect(
        dataset_version=settings.dataset_version,
        mapping_sha256=settings.baseline.locks["issue_mapping_sha256"],
        model_name=option.name,
        revision=option.revision,
        dimension=None,
        max_segments=settings.max_segments,
        row_count=issue_count,
    )
    if status != "CACHE_READY":
        raise RuntimeError(f"text cache is not reusable: {status}: {reasons}")
    return np.load(cache.embedding_path, mmap_mode="r")


def evaluate_checkpoint(
    *,
    config_path: Path,
    run_id: str,
    output_path: Path,
    include_test: bool,
) -> dict[str, object]:
    settings = load_neural_settings(config_path)
    input_hashes = validate_neural_inputs(settings, require_text=True)
    run_dir = settings.run_dir / run_id
    selection_path = run_dir / "validation_selection.json"
    if selection_path.is_file():
        selections = json.loads(selection_path.read_text(encoding="utf-8"))
        selected = max(selections, key=lambda item: float(item["score"]))
        checkpoint = run_dir / str(selected["best_checkpoint"])
    else:
        candidates = sorted((run_dir / "checkpoints").glob("trial-*.best.pt"))
        if len(candidates) != 1:
            raise FileNotFoundError(
                "selecting among multiple Trial checkpoints requires validation_selection.json"
            )
        checkpoint = candidates[0]
    payload = torch.load(checkpoint, map_location="cpu", weights_only=True, mmap=True)
    metadata = dict(payload.get("metadata", {}))
    parameters = dict(metadata.get("parameters", {}))
    checkpoint_epoch = int(payload["epoch"])
    checkpoint_validation_score = float(payload["validation_score"])
    del payload

    developer_count, issue_count, repository_count = mapping_sizes(settings.baseline)
    label_count = pq.ParquetFile(
        settings.baseline.normalized_dir / "mappings/label_mapping.parquet"
    ).metadata.num_rows
    graphs = build_training_graphs(
        graph_dir=settings.baseline.graph_dir,
        normalized_dir=settings.baseline.normalized_dir,
        num_users=developer_count,
        num_issues=issue_count,
        num_repositories=repository_count,
        num_labels=label_count,
    )
    semantic = _semantic_array(
        settings, str(parameters["encoder_version"]), issue_count
    )
    device = torch.device(settings.device)
    model = _build_trial_model(
        parameters=parameters,
        semantic_array=semantic,
        developer_count=developer_count,
        issue_count=issue_count,
        repository_count=repository_count,
        label_count=label_count,
        graphs=graphs,
        settings=settings,
        use_text=True,
        use_structure=True,
        use_repository=True,
        device=device,
    )
    restored = restore_best_checkpoint(checkpoint, model, map_location=device)
    train = read_interactions(settings.baseline, "train")
    known = np.bincount(train.developer_ids, minlength=developer_count) > 0
    cross_users = cross_repository_users(train.developer_ids, train.repository_ids)
    ranker = TorchIssueRanker(model, known, device=device)
    splits = ("valid", "test") if include_test else ("valid",)
    issue_metrics: dict[str, object] = {}
    for split in splits:
        issue_metrics[split] = {}
        for mode in settings.baseline.protocol.candidate_modes:
            candidate_path = settings.candidate_dir / f"{split}_{mode}.parquet"
            issue_metrics[split][mode] = evaluate_issue_groups(
                ranker,
                candidate_path,
                cross_repository_users=cross_users,
                cutoffs=settings.baseline.protocol.cutoffs,
            )
    report: dict[str, object] = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "variant": "coder_issue_repo",
        "dataset_version": settings.dataset_version,
        "graph_version": settings.graph_version,
        "checkpoint": str(checkpoint),
        "checkpoint_epoch": checkpoint_epoch,
        "checkpoint_validation_score": checkpoint_validation_score,
        "restored_checkpoint": restored,
        "parameters": parameters,
        "selection_protocol": {
            "split": "valid",
            "candidate_mode": "cross_repo",
            "group": "predictable",
            "metric": "ndcg@10",
        },
        "test_included": include_test,
        "input_sha256": input_hashes,
        "issue": issue_metrics,
        "repository": {
            "status": "UNAVAILABLE_INDEPENDENT_PROTOCOL",
            "metrics": None,
        },
    }
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite evaluation output: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--include-test",
        action="store_true",
        help="evaluate frozen test candidates only after the validation-selected checkpoint is fixed",
    )
    args = parser.parse_args(argv)
    evaluate_checkpoint(
        config_path=args.config,
        run_id=args.run_id,
        output_path=args.output,
        include_test=args.include_test,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
