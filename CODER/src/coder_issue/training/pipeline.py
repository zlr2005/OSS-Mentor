"""Validated stage 3-5 training entry point; execution requires an explicit flag."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
from itertools import product
import json
import logging
from pathlib import Path
import platform
import sys

import numpy as np
import pyarrow.compute as pc
import pyarrow.parquet as pq
import torch

from coder_issue.baselines.dataset import mapping_sizes, read_interactions
from coder_issue.data.graph_builders import build_training_graphs
from coder_issue.evaluation.groups import cross_repository_users, project_module_coverage
from coder_issue.evaluation.multigranular import evaluate_issue_groups, evaluate_repository_groups
from coder_issue.logging_utils import configure_logging
from coder_issue.models.coder import CoderConfig, CoderIssueModel
from coder_issue.models.gat import StructuralGATConfig, StructuralGATEncoder
from coder_issue.models.text_encoder import (
    EmbeddingCache, FrozenTransformerEncoder, IssueText, segment_issue_text,
)
from coder_issue.training.config import load_neural_settings, validate_neural_inputs
from coder_issue.training.losses import BPRLoss
from coder_issue.training.negative_sampling import CanonicalCandidateSampler, RepositoryCandidateSampler
from coder_issue.training.trainer import TorchIssueRanker, seed_training, train_coder_epoch


LOGGER = logging.getLogger(__name__)
VARIANT_FLAGS = {
    "lightgcn": (False, False, False),
    "coder_issue_text": (True, False, False),
    "coder_issue_structure": (False, True, False),
    "coder_issue_base": (True, True, False),
    "coder_issue_repo": (True, True, True),
}


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _variant_flags(variant: str) -> tuple[bool, bool, bool]:
    return VARIANT_FLAGS[variant]


def _creation_texts(settings, max_segments: int):
    table = pq.read_table(
        settings.baseline.normalized_dir / "issue_snapshots.parquet",
        columns=["issue_id", "snapshot_type", "title", "body"],
    )
    table = table.filter(pc.equal(table["snapshot_type"], "creation_snapshot"))
    rows = {
        int(issue_id): IssueText(int(issue_id), title or "", body or "")
        for issue_id, title, body in zip(
            table["issue_id"].to_pylist(), table["title"].to_pylist(), table["body"].to_pylist(),
            strict=True,
        )
    }
    issue_count = mapping_sizes(settings.baseline)[1]
    return [
        segment_issue_text(rows.get(issue_id, IssueText(issue_id, "", "")), max_segments=max_segments)
        for issue_id in range(issue_count)
    ]


def _semantic_candidates(settings) -> list[tuple[str, np.ndarray]]:
    texts = _creation_texts(settings, settings.max_segments)
    outputs: list[tuple[str, np.ndarray]] = []
    mapping_hash = settings.baseline.locks["issue_mapping_sha256"]
    for option in settings.encoders:
        encoder = FrozenTransformerEncoder(
            option.name,
            revision=option.revision,
            device=settings.device,
            allow_download=settings.allow_download,
        )
        safe_name = option.name.replace("/", "__")
        cache = EmbeddingCache(settings.embedding_dir / f"{safe_name}@{option.revision}")
        embeddings, statistics = cache.encode_or_reuse(
            texts,
            encoder,
            dataset_version=settings.dataset_version,
            mapping_sha256=mapping_hash,
            max_segments=settings.max_segments,
            batch_size=settings.text_batch_size,
        )
        LOGGER.info("encoder cache %s: %s", option.name, statistics)
        outputs.append((f"{option.name}@{option.revision}", embeddings))
    return outputs


def train_variant(settings, *, variant: str, run_id: str) -> Path:
    use_text, use_structure, use_repository = _variant_flags(variant)
    input_hashes = validate_neural_inputs(settings, require_text=use_text)
    if variant not in settings.variants:
        raise ValueError(f"variant is disabled by configuration: {variant}")
    run_dir = settings.run_dir / run_id
    model_dir = settings.model_dir / run_id
    if run_dir.exists() or model_dir.exists():
        raise FileExistsError(f"refusing to overwrite existing run/model: {run_id}")
    run_dir.mkdir(parents=True)
    model_dir.mkdir(parents=True)
    (run_dir / "_RUNNING").write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
    try:
        seed_training(settings.seed)
        device = torch.device(settings.device)
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
        train = read_interactions(settings.baseline, "train")
        known = np.bincount(train.developer_ids, minlength=developer_count) > 0
        cross_users = cross_repository_users(train.developer_ids, train.repository_ids)
        candidate_train = settings.candidate_dir / "train_cross_repo.parquet"
        issue_sampler = CanonicalCandidateSampler.from_parquet(candidate_train, seed=settings.seed)
        repository_sampler = (
            RepositoryCandidateSampler.from_issue_candidates(
                candidate_train,
                issue_to_repository=graphs.issue_to_repository.numpy(),
                seed=settings.seed,
            )
            if use_repository else None
        )
        semantic_options = _semantic_candidates(settings) if use_text else [("disabled", None)]
        structure_options = settings.structure_layers if use_structure else (0,)
        repository_weights = settings.repository_loss_weights if use_repository else (0.0,)
        trials: list[dict[str, object]] = []
        best_score = -float("inf")
        best_model = None
        best_parameters: dict[str, object] | None = None
        validation_path = settings.candidate_dir / "valid_cross_repo.parquet"
        for dimension, layers, learning_rate, structure_layers, repository_weight, semantic in product(
            settings.embedding_dimensions,
            settings.issue_layers,
            settings.learning_rates,
            structure_options,
            repository_weights,
            semantic_options,
        ):
            encoder_version, semantic_array = semantic
            seed_training(settings.seed)
            structural = None
            if use_structure:
                structural = StructuralGATEncoder(
                    StructuralGATConfig(
                        repository_count, issue_count, label_count, dimension,
                        structure_layers, settings.structure_heads, settings.structure_dropout,
                    ),
                    graphs.structural_edge_index,
                )
            config = CoderConfig(
                developer_count, issue_count, repository_count,
                embedding_dim=dimension,
                issue_layers=layers,
                repository_layers=settings.repository_layers,
                structure_layers=structure_layers or 2,
                structure_heads=settings.structure_heads,
                structure_dropout=settings.structure_dropout,
                semantic_dim=0 if semantic_array is None else semantic_array.shape[1],
                use_text=use_text,
                use_structure=use_structure,
                use_repository=use_repository,
            )
            model = CoderIssueModel(
                config,
                issue_adjacency=graphs.issue_adjacency,
                issue_to_repository=graphs.issue_to_repository,
                repository_adjacency=graphs.repository_adjacency if use_repository else None,
                semantic_embeddings=(
                    torch.from_numpy(semantic_array) if semantic_array is not None else None
                ),
                structural_encoder=structural,
            ).to(device)
            optimizer = torch.optim.Adam(
                model.parameters(), lr=learning_rate, weight_decay=settings.weight_decay
            )
            criterion = BPRLoss()
            history = []
            for epoch in range(settings.epochs):
                result = train_coder_epoch(
                    model,
                    optimizer=optimizer,
                    criterion=criterion,
                    developer_ids=train.developer_ids,
                    issue_sampler=issue_sampler,
                    repository_sampler=repository_sampler,
                    repository_weight=repository_weight,
                    epoch=epoch,
                    batch_size=settings.batch_size,
                    seed=settings.seed,
                    device=device,
                )
                history.append(result.__dict__)
            ranker = TorchIssueRanker(model, known, device=device)
            validation = evaluate_issue_groups(
                ranker, validation_path, cross_repository_users=cross_users,
                cutoffs=settings.baseline.protocol.cutoffs,
            )
            score = float(validation["predictable"][settings.selection_metric])
            parameters = {
                "embedding_dim": dimension,
                "issue_layers": layers,
                "learning_rate": learning_rate,
                "structure_layers": structure_layers,
                "repository_loss_weight": repository_weight,
                "encoder_version": encoder_version,
            }
            trials.append(
                {"parameters": parameters, "validation": validation, "history": history, "score": score}
            )
            if score > best_score:
                best_score = score
                best_parameters = parameters
                best_model = copy.deepcopy(model).cpu()
        assert best_model is not None and best_parameters is not None
        _write_json(run_dir / "validation_selection.json", trials)

        # Test candidates are used for metric evaluation only after the validation winner is frozen.
        best_model = best_model.to(device)
        best_model.save_bundle(
            model_dir,
            metadata={
                "model_version": settings.model_version,
                "dataset_version": settings.dataset_version,
                "graph_version": settings.graph_version,
                "encoder_version": best_parameters["encoder_version"],
                "profile_mode": "known_history_only",
                "coverage_state": "fixed_train_graph",
                "selected_parameters": best_parameters,
            },
        )
        _write_json(
            model_dir / "feature_schema.json",
            {
                "schema_version": 1,
                "developer_id": "gfibot-m0-v1.1 developer_mapping row ID",
                "issue_id": "gfibot-m0-v1.1 issue_mapping row ID",
                "repository_id": "gfibot-m0-v1.1 repository_mapping row ID",
                "issue_text": "creation snapshot title/body/code blocks only",
                "issue_labels": "creation_snapshot edges only",
                "variant": variant,
            },
        )
        ranker = TorchIssueRanker(best_model, known, device=device)
        metrics: dict[str, object] = {"issue": {}, "repository": {}}
        for split in ("valid", "test"):
            metrics["issue"][split] = {}
            if use_repository:
                metrics["repository"][split] = {}
            for mode in settings.baseline.protocol.candidate_modes:
                candidate_path = settings.candidate_dir / f"{split}_{mode}.parquet"
                metrics["issue"][split][mode] = evaluate_issue_groups(
                    ranker, candidate_path, cross_repository_users=cross_users,
                    cutoffs=settings.baseline.protocol.cutoffs,
                )
                if use_repository:
                    metrics["repository"][split][mode] = evaluate_repository_groups(
                        best_model,
                        candidate_path,
                        issue_to_repository=graphs.issue_to_repository.cpu().numpy(),
                        known_developers=known,
                        cross_repository_users=cross_users,
                        cutoffs=settings.baseline.protocol.cutoffs,
                        device=device,
                    )
        valid = read_interactions(settings.baseline, "valid")
        test = read_interactions(settings.baseline, "test")
        coverage = {
            split: project_module_coverage(
                data.developer_ids, train.developer_ids, train.repository_ids, data.repository_ids
            )
            for split, data in (("valid", valid), ("test", test))
        }
        _write_json(run_dir / "metrics.json", metrics)
        _write_json(run_dir / "project_coverage.json", coverage)
        baseline_metrics_path = settings.candidate_dir.parent / "metrics.json"
        if baseline_metrics_path.is_file():
            baseline_metrics = json.loads(baseline_metrics_path.read_text(encoding="utf-8"))
            _write_json(
                run_dir / "comparison.json",
                {
                    "popularity": baseline_metrics.get("popularity"),
                    "mf_bpr": baseline_metrics.get("bpr_mf"),
                    variant: metrics["issue"],
                    "note": "All entries use the same frozen split and candidate files.",
                },
            )
        with torch.inference_mode():
            users, issues = best_model.fused_embeddings()
            export = {"user_embeddings": users.cpu().numpy(), "issue_embeddings": issues.cpu().numpy()}
            if best_model.repository_graph is not None:
                repo_users, repositories = best_model.repository_graph.propagated_embeddings()
                export.update(
                    repository_user_embeddings=repo_users.cpu().numpy(),
                    repository_embeddings=repositories.cpu().numpy(),
                )
            np.savez_compressed(model_dir / "embeddings.npz", **export)
        run_manifest = {
            "run_id": run_id,
            "variant": variant,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "dataset_version": settings.dataset_version,
            "graph_version": settings.graph_version,
            "config_path": str(settings.config_path),
            "config_sha256": hashlib.sha256(settings.config_path.read_bytes()).hexdigest(),
            "input_sha256": input_hashes,
            "selected_parameters": best_parameters,
            "test_gate": "test evaluated after validation selection and model bundle freeze",
            "project_relation_semantics": "project-level auxiliary propagation derived from issue solutions",
            "versions": {"python": sys.version.split()[0], "torch": torch.__version__, "platform": platform.platform()},
        }
        _write_json(run_dir / "run_manifest.json", run_manifest)
        (run_dir / "_RUNNING").unlink()
        (run_dir / "_SUCCESS").write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
        return run_dir
    except Exception:
        (run_dir / "_FAILED").write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--variant", choices=sorted(VARIANT_FLAGS), required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--execute-training", action="store_true",
        help="required safety switch; without it only inputs/configuration are validated",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(level="INFO", log_format="text")
    try:
        settings = load_neural_settings(args.config)
        hashes = validate_neural_inputs(
            settings, require_text=_variant_flags(args.variant)[0] and args.execute_training
        )
        if not args.execute_training:
            LOGGER.info(
                "dry-run validated %d frozen inputs for %s; no training started",
                len(hashes), args.variant,
            )
            return 0
        output = train_variant(settings, variant=args.variant, run_id=args.run_id)
        LOGGER.info("training workflow complete: %s", output)
        return 0
    except Exception:
        LOGGER.exception("stage 3-5 workflow failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
