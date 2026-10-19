"""One-command stage-2 baseline training and evaluation pipeline."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import platform
import sys
from typing import Any

import numpy as np
import pyarrow
import scipy
import sklearn

from coder_issue.logging_utils import configure_logging
from coder_issue.randomness import set_global_seed

from .candidates import iter_candidate_batches, materialize_candidates, validate_candidate_file
from .config import BaselineSettings, load_baseline_settings, validate_frozen_inputs
from .dataset import Interactions, mapping_sizes, read_catalog, read_interactions, training_history
from .metrics import evaluate
from .models import BPRModel, PopularityModel, TextSimilarityModel


LOGGER = logging.getLogger(__name__)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _subset(interactions: Interactions, mask: np.ndarray) -> Interactions:
    return Interactions(
        interactions.developer_ids[mask],
        interactions.issue_ids[mask],
        interactions.repository_ids[mask],
        interactions.interaction_times[mask],
    )


def _negative_pools(path: Path, train: Interactions) -> tuple[Interactions, np.ndarray, int]:
    pools: list[list[int]] = [[] for _ in range(len(train))]
    for batch in iter_candidate_batches(path):
        for query_id, target, candidates in zip(
            batch["query_id"], batch["target_issue_id"], batch["candidate_issue_ids"], strict=True
        ):
            pools[int(query_id)] = [int(value) for value in candidates if int(value) != int(target)]
    usable = np.asarray([bool(row) for row in pools])
    skipped = int(np.sum(~usable))
    width = max(len(row) for row in pools if row)
    matrix = np.empty((int(np.sum(usable)), width), dtype=np.int64)
    output_row = 0
    for row in pools:
        if not row:
            continue
        matrix[output_row] = np.resize(np.asarray(row, dtype=np.int64), width)
        output_row += 1
    return _subset(train, usable), matrix, skipped


def _materialize_shared_candidates(
    settings: BaselineSettings,
    run_dir: Path,
    catalog,
    splits: dict[str, Interactions],
    solved: dict[int, set[int]],
    known: set[int],
) -> dict[str, Any]:
    candidate_dir = run_dir / "candidates"
    stats: dict[str, Any] = {}
    for split in ("train", "valid", "test"):
        modes = ("cross_repo",) if split == "train" else settings.protocol.candidate_modes
        cap = settings.bpr.negative_pool_size + 1 if split == "train" else settings.protocol.max_candidates
        for mode in modes:
            path = candidate_dir / f"{split}_{mode}.parquet"
            LOGGER.info("materializing candidates: %s/%s", split, mode)
            item = materialize_candidates(
                catalog=catalog,
                queries=splits[split],
                training_solved=solved,
                known_developers=known,
                split=split,
                mode=mode,
                seed=settings.protocol.candidate_seed,
                max_candidates=cap,
                output_path=path,
            )
            validate_candidate_file(
                path,
                catalog=catalog,
                training_solved=solved,
                split=split,
                mode=mode,
                max_candidates=cap,
                expected_queries=len(splits[split]),
            )
            item["validated"] = True
            item["sha256"] = _sha256(path)
            item["path"] = str(path)
            stats[f"{split}/{mode}"] = item
    _json(candidate_dir / "manifest.json", stats)
    return stats


def run(settings: BaselineSettings, *, run_id: str) -> Path:
    input_hashes = validate_frozen_inputs(settings)
    set_global_seed(settings.seed)
    np.random.seed(settings.seed)
    run_dir = settings.runs_dir / run_id
    model_dir = settings.models_dir / run_id
    if run_dir.exists() or model_dir.exists():
        raise FileExistsError(f"refusing to overwrite an existing run: {run_id}")
    run_dir.mkdir(parents=True)
    model_dir.mkdir(parents=True)
    try:
        LOGGER.info("loading frozen %s artifacts", settings.dataset_version)
        catalog = read_catalog(settings)
        splits = {name: read_interactions(settings, name) for name in ("train", "valid", "test")}
        developer_count, issue_count, repository_count = mapping_sizes(settings)
        solved, known = training_history(splits["train"])
        candidate_manifest = _materialize_shared_candidates(
            settings, run_dir, catalog, splits, solved, known
        )

        LOGGER.info("fitting popularity baseline")
        popularity_path = model_dir / "popularity"
        PopularityModel.fit(splits["train"], catalog, repository_count).save(popularity_path)
        popularity = PopularityModel.load(popularity_path)

        LOGGER.info("fitting text similarity baseline")
        text_path = model_dir / "text_similarity"
        TextSimilarityModel.fit(
            splits["train"],
            catalog,
            developer_count=developer_count,
            max_features=settings.text.max_features,
            min_df=settings.text.min_df,
            ngram_max=settings.text.ngram_max,
        ).save(text_path)
        text_model = TextSimilarityModel.load(text_path)

        bpr_train, negative_pools, skipped = _negative_pools(
            run_dir / "candidates/train_cross_repo.parquet", splits["train"]
        )
        LOGGER.info("selecting BPR hyperparameters on validation only")
        selection: list[dict[str, Any]] = []
        best_score = -1.0
        best_parameters: dict[str, Any] | None = None
        best_model: BPRModel | None = None
        validation_candidates = run_dir / "candidates/valid_cross_repo.parquet"
        for dimensions in settings.bpr.dimensions:
            for learning_rate in settings.bpr.learning_rates:
                model = BPRModel.fit(
                    bpr_train,
                    negative_pools,
                    developer_count=developer_count,
                    issue_count=issue_count,
                    dimensions=dimensions,
                    learning_rate=learning_rate,
                    regularization=settings.bpr.regularization,
                    epochs=settings.bpr.epochs,
                    batch_size=settings.bpr.batch_size,
                    seed=settings.seed,
                )
                validation = evaluate(model, validation_candidates, settings.protocol.cutoffs)
                score = float(validation["predictable"]["ndcg@10"])
                parameters = {"dimensions": dimensions, "learning_rate": learning_rate}
                selection.append({"parameters": parameters, "validation_predictable_ndcg@10": score})
                if score > best_score:
                    best_score, best_parameters, best_model = score, parameters, model
        assert best_model is not None and best_parameters is not None
        best_model.save(
            model_dir / "bpr_mf",
            metadata={
                **best_parameters,
                "regularization": settings.bpr.regularization,
                "epochs": settings.bpr.epochs,
                "skipped_no_negative_interactions": skipped,
            },
        )
        bpr_model = BPRModel.load(model_dir / "bpr_mf")
        _json(run_dir / "bpr_selection.json", selection)

        LOGGER.info("hyperparameters frozen; evaluating validation and test")
        models = (popularity, text_model, bpr_model)
        metrics: dict[str, Any] = {}
        for model in models:
            metrics[model.name] = {}
            for split in ("valid", "test"):
                metrics[model.name][split] = {}
                for mode in settings.protocol.candidate_modes:
                    metrics[model.name][split][mode] = evaluate(
                        model,
                        run_dir / f"candidates/{split}_{mode}.parquet",
                        settings.protocol.cutoffs,
                    )
        _json(run_dir / "metrics.json", metrics)
        manifest = {
            "schema_version": 1,
            "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "dataset_version": settings.dataset_version,
            "config": str(settings.config_path),
            "config_sha256": _sha256(settings.config_path),
            "frozen_input_sha256": input_hashes,
            "candidate_manifest": candidate_manifest,
            "selected_bpr_parameters": best_parameters,
            "training_counts": {
                "original": len(splits["train"]),
                "bpr_usable": len(bpr_train),
                "bpr_skipped_no_observable_negative": skipped,
            },
            "versions": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "numpy": np.__version__,
                "pyarrow": pyarrow.__version__,
                "scipy": scipy.__version__,
                "scikit_learn": sklearn.__version__,
            },
            "test_gate": "test evaluated only after validation-based BPR selection",
        }
        _json(run_dir / "run_manifest.json", manifest)
        (run_dir / "_SUCCESS").write_text(
            json.dumps({"dataset_version": settings.dataset_version, "run_id": run_id}) + "\n",
            encoding="utf-8",
        )
        return run_dir
    except Exception:
        (run_dir / "_FAILED").write_text(datetime.now(timezone.utc).isoformat() + "\n", encoding="utf-8")
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(level="INFO", log_format="text")
    try:
        settings = load_baseline_settings(args.config)
        output = run(settings, run_id=args.run_id)
    except Exception:
        LOGGER.exception("stage-2 baseline run failed")
        return 1
    LOGGER.info("stage-2 baseline run complete: %s", output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
