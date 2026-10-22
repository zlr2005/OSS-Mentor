"""Validated stage 3-5 training entry point; execution requires an explicit flag."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
from pathlib import Path
import platform
import signal
import sys
import time
import uuid

import numpy as np
import pyarrow.compute as pc
import pyarrow.parquet as pq
import torch

from coder_issue.baselines.dataset import mapping_sizes, read_interactions
from coder_issue.data.graph_builders import build_training_graphs
from coder_issue.evaluation.groups import cross_repository_users, project_module_coverage
from coder_issue.evaluation.multigranular import evaluate_issue_groups
from coder_issue.logging_utils import configure_logging
from coder_issue.models.coder import CoderConfig, CoderIssueModel
from coder_issue.models.gat import StructuralGATConfig, StructuralGATEncoder
from coder_issue.models.text_encoder import (
    EmbeddingCache, FrozenTransformerEncoder, IssueText, segment_issue_text,
)
from coder_issue.training.config import (
    expand_search_trials,
    load_neural_settings,
    validate_neural_inputs,
)
from coder_issue.training.losses import BPRLoss
from coder_issue.training.monitoring import RunMonitor, format_duration, running_state_is_active
from coder_issue.training.checkpoint import CheckpointManager
from coder_issue.training.negative_sampling import CanonicalCandidateSampler, RepositoryCandidateSampler
from coder_issue.training.selection import (
    EarlyStoppingPolicy,
    ValidationSchedule,
    restore_best_checkpoint,
    run_training_trial,
)
from coder_issue.training.trainer import (
    BatchProgress, BatchResumeState, TorchIssueRanker, seed_training, train_coder_epoch,
)


LOGGER = logging.getLogger(__name__)
VALIDATION_CANDIDATE_FILENAME = "valid_cross_repo.parquet"
VARIANT_FLAGS = {
    "lightgcn": (False, False, False),
    "coder_issue_text": (True, False, False),
    "coder_issue_structure": (False, True, False),
    "coder_issue_base": (True, True, False),
    "coder_issue_repo": (True, True, True),
}


class _SafeInterrupt:
    """Turn the first Ctrl+C into a request handled after an optimizer step."""

    def __init__(self) -> None:
        self.requested = False
        self._previous = None

    def install(self) -> None:
        self._previous = signal.getsignal(signal.SIGINT)

        def handler(signum, frame):
            del signum, frame
            if self.requested:
                raise KeyboardInterrupt("forced by second Ctrl+C")
            self.requested = True
            LOGGER.warning(
                "Ctrl+C received; stopping at the next completed optimizer step and saving checkpoint"
            )

        signal.signal(signal.SIGINT, handler)

    def restore(self) -> None:
        if self._previous is not None:
            signal.signal(signal.SIGINT, self._previous)

    def __call__(self) -> bool:
        return self.requested


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _finish_probe_run(
    monitor: RunMonitor,
    run_dir: Path,
    *,
    run_id: str,
    variant: str,
    trial: int,
    parameters: dict[str, object],
    issue_loss: float,
    repository_loss: float | None,
    validation_score: float,
    best_epoch: int,
    best_checkpoint: str,
) -> None:
    """Persist the bounded Probe result and commit its terminal state."""

    payload = {
        "schema_version": 1,
        "status": "success",
        "probe_batch": True,
        "run_id": run_id,
        "variant": variant,
        "trial": trial,
        "parameters": parameters,
        "issue_loss": issue_loss,
        "repository_loss": repository_loss,
        "validation_score": validation_score,
        "best_epoch": best_epoch,
        "best_checkpoint": best_checkpoint,
        "formal_final_evaluation_skipped": True,
        "note": (
            "Single-batch engineering probe completed training, validation, and an "
            "atomic best checkpoint; formal model export and valid/test evaluation "
            "were intentionally skipped."
        ),
    }
    _write_json(run_dir / "probe_result.json", payload)
    monitor.update(
        LOGGER,
        "complete",
        (
            "single-batch probe complete; checkpoint and validation persisted; "
            "formal model export and final valid/test evaluation skipped by design"
        ),
        probe_batch=True,
        validation_score=validation_score,
        best_epoch=best_epoch,
        best_checkpoint=best_checkpoint,
    )
    monitor.finish("success")


def _training_code_sha256() -> str:
    root = Path(__file__).resolve().parents[1]
    files = (
        Path(__file__), root / "training/trainer.py", root / "training/selection.py",
        root / "training/checkpoint.py", root / "models/coder.py",
        root / "models/lightgcn.py", root / "models/gat.py",
    )
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode("utf-8")); digest.update(b"\0")
        digest.update(path.read_bytes()); digest.update(b"\0")
    return digest.hexdigest()


def _semantic_cache_identity(settings, options) -> dict[str, object]:
    identity: dict[str, object] = {}
    for name, array in options:
        if array is None:
            identity[name] = None
            continue
        root = settings.embedding_dir / name.replace("/", "__")
        manifest = json.loads((root / "embedding_manifest.json").read_text(encoding="utf-8"))
        identity[name] = {
            "shape": list(array.shape), "dtype": str(array.dtype),
            "embedding_sha256": manifest.get("embedding_sha256")
            or EmbeddingCache._array_sha256(array),
            "mapping_sha256": manifest.get("mapping_sha256"),
            "revision": manifest.get("revision"),
        }
    return identity


def _variant_flags(variant: str) -> tuple[bool, bool, bool]:
    return VARIANT_FLAGS[variant]


def _prepare_run_directories(run_dir: Path, model_dir: Path, *, resume: bool) -> None:
    """Claim a new/incomplete run without overwriting completed or active work."""

    if resume:
        if not run_dir.is_dir():
            raise FileNotFoundError(f"cannot resume missing run directory: {run_dir}")
        if (run_dir / "_SUCCESS").exists():
            raise RuntimeError("completed _SUCCESS run cannot be resumed")
        running_path = run_dir / "_RUNNING"
        if running_path.is_file():
            try:
                running_state = json.loads(running_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                running_state = {}
            if running_state_is_active(running_state):
                raise RuntimeError("run is still active in another process")
            running_path.replace(run_dir / f"_RUNNING.stale.{uuid.uuid4().hex}.json")
        for terminal_name in ("_CANCELLED", "_FAILED"):
            terminal = run_dir / terminal_name
            if terminal.is_file():
                terminal.replace(
                    run_dir / f"{terminal_name}.history.{uuid.uuid4().hex}.json"
                )
        model_dir.mkdir(parents=True, exist_ok=True)
        return
    if run_dir.exists() or model_dir.exists():
        raise FileExistsError(
            f"refusing to overwrite existing run/model: {run_dir.name}; inspect {run_dir} and "
            "use a new run-id, or pass --resume for the same incomplete run"
        )
    run_dir.mkdir(parents=True)
    model_dir.mkdir(parents=True)


def _resume_start_trial(progress: dict[str, object]) -> int:
    """Return the first unfinished Trial; between-trial checkpoints never repeat work."""

    value = progress.get("next_trial", progress.get("trial", 1))
    trial = int(value)
    if trial <= 0:
        raise ValueError("checkpoint Trial position must be positive")
    return trial


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
    LOGGER.info("preparing creation-time issue text for %d encoder(s)", len(settings.encoders))
    texts = _creation_texts(settings, settings.max_segments)
    outputs: list[tuple[str, np.ndarray]] = []
    mapping_hash = settings.baseline.locks["issue_mapping_sha256"]
    for option in settings.encoders:
        safe_name = option.name.replace("/", "__")
        cache = EmbeddingCache(settings.embedding_dir / f"{safe_name}@{option.revision}")
        status, reasons = cache.inspect(
            dataset_version=settings.dataset_version,
            mapping_sha256=mapping_hash,
            model_name=option.name,
            revision=option.revision,
            dimension=None,
            max_segments=settings.max_segments,
            row_count=len(texts),
        )
        if status == "CACHE_READY":
            LOGGER.info("encoder cache is ready; reusing without loading Transformer: %s", cache.root)
            outputs.append((f"{option.name}@{option.revision}", np.load(cache.embedding_path, mmap_mode="r")))
            continue
        LOGGER.info("encoder cache %s (%s); loading frozen encoder %s@%s", status, reasons, option.name, option.revision)
        encoder = FrozenTransformerEncoder(
            option.name, revision=option.revision, device=settings.device,
            allow_download=settings.allow_download,
        )
        embeddings, statistics = cache.encode_or_reuse(
            texts,
            encoder,
            dataset_version=settings.dataset_version,
            mapping_sha256=mapping_hash,
            max_segments=settings.max_segments,
            batch_size=settings.text_batch_size,
            segment_batch_size=settings.text_segment_batch_size,
            chunk_size=settings.text_chunk_size,
        )
        LOGGER.info("encoder cache %s: %s", option.name, statistics)
        del embeddings
        outputs.append((
            f"{option.name}@{option.revision}", np.load(cache.embedding_path, mmap_mode="r")
        ))
    return outputs


def validation_candidate_path(settings) -> Path:
    """Return the only candidate file allowed for in-training model selection."""

    return settings.candidate_dir / VALIDATION_CANDIDATE_FILENAME


def _build_trial_model(
    *,
    parameters: dict[str, object],
    semantic_array: np.ndarray | None,
    developer_count: int,
    issue_count: int,
    repository_count: int,
    label_count: int,
    graphs,
    settings,
    use_text: bool,
    use_structure: bool,
    use_repository: bool,
    device: torch.device,
) -> CoderIssueModel:
    dimension = int(parameters["embedding_dim"])
    structure_layers = int(parameters["structure_layers"])
    structural = None
    if use_structure:
        structural = StructuralGATEncoder(
            StructuralGATConfig(
                repository_count,
                issue_count,
                label_count,
                dimension,
                structure_layers,
                settings.structure_heads,
                settings.structure_dropout,
            ),
            graphs.structural_edge_index,
        )
    config = CoderConfig(
        developer_count,
        issue_count,
        repository_count,
        embedding_dim=dimension,
        issue_layers=int(parameters["issue_layers"]),
        repository_layers=settings.repository_layers,
        structure_layers=structure_layers or 2,
        structure_heads=settings.structure_heads,
        structure_dropout=settings.structure_dropout,
        semantic_dim=0 if semantic_array is None else semantic_array.shape[1],
        use_text=use_text,
        use_structure=use_structure,
        use_repository=use_repository,
    )
    return CoderIssueModel(
        config,
        issue_adjacency=graphs.issue_adjacency,
        issue_to_repository=graphs.issue_to_repository,
        repository_adjacency=graphs.repository_adjacency if use_repository else None,
        semantic_embeddings=(
            torch.tensor(semantic_array, dtype=torch.float32)
            if semantic_array is not None
            else None
        ),
        structural_encoder=structural,
    ).to(device)


def train_variant(
    settings,
    *,
    variant: str,
    run_id: str,
    validated_input_hashes: dict[str, str] | None = None,
    progress_interval_seconds: float | None = None,
    resume: bool = False,
    probe_batch: bool = False,
) -> Path:
    if probe_batch and resume:
        raise ValueError("a completed single-batch probe cannot use resume mode")
    use_text, use_structure, use_repository = _variant_flags(variant)
    input_hashes = (
        dict(validated_input_hashes)
        if validated_input_hashes is not None
        else validate_neural_inputs(settings, require_text=use_text)
    )
    if variant not in settings.variants:
        raise ValueError(f"variant is disabled by configuration: {variant}")
    run_dir = settings.run_dir / run_id
    model_dir = settings.model_dir / run_id
    _prepare_run_directories(run_dir, model_dir, resume=resume)
    monitor = RunMonitor(
        run_dir,
        run_id=run_id,
        variant=variant,
        interval_seconds=(
            settings.progress_interval_seconds
            if progress_interval_seconds is None
            else progress_interval_seconds
        ),
    )
    safe_interrupt = _SafeInterrupt()
    try:
        monitor.update(
            LOGGER,
            "initializing",
            f"run={run_id} variant={variant} device={settings.device} outputs={run_dir}",
            dataset_version=settings.dataset_version,
            graph_version=settings.graph_version,
        )
        seed_training(settings.seed)
        device = torch.device(settings.device)
        developer_count, issue_count, repository_count = mapping_sizes(settings.baseline)
        label_count = pq.ParquetFile(
            settings.baseline.normalized_dir / "mappings/label_mapping.parquet"
        ).metadata.num_rows
        monitor.update(
            LOGGER,
            "building_graphs",
            "building frozen train-only Developer-Issue and structural graphs",
            developer_count=developer_count,
            issue_count=issue_count,
            repository_count=repository_count,
            label_count=label_count,
        )
        graphs = build_training_graphs(
            graph_dir=settings.baseline.graph_dir,
            normalized_dir=settings.baseline.normalized_dir,
            num_users=developer_count,
            num_issues=issue_count,
            num_repositories=repository_count,
            num_labels=label_count,
        )
        monitor.update(LOGGER, "loading_training_data", "loading frozen training interactions")
        train = read_interactions(settings.baseline, "train")
        known = np.bincount(train.developer_ids, minlength=developer_count) > 0
        cross_users = cross_repository_users(train.developer_ids, train.repository_ids)
        candidate_train = settings.candidate_dir / "train_cross_repo.parquet"
        monitor.update(
            LOGGER,
            "loading_candidates",
            f"loading canonical comparisons from {candidate_train}",
            training_interactions=len(train.developer_ids),
        )
        issue_sampler = CanonicalCandidateSampler.from_parquet(candidate_train, seed=settings.seed)
        repository_sampler = (
            RepositoryCandidateSampler.from_issue_candidates(
                candidate_train,
                issue_to_repository=graphs.issue_to_repository.numpy(),
                seed=settings.seed,
            )
            if use_repository else None
        )
        monitor.update(
            LOGGER,
            "preparing_features",
            "preparing semantic/structural feature options",
            use_text=use_text,
            use_structure=use_structure,
            use_repository=use_repository,
        )
        semantic_options = _semantic_candidates(settings) if use_text else [("disabled", None)]
        # During cache preparation a normal KeyboardInterrupt preserves completed chunks.
        # Defer soft-stop handling until optimizer steps begin.
        safe_interrupt.install()
        semantic_by_version = dict(semantic_options)
        structure_options = settings.structure_layers if use_structure else (0,)
        repository_weights = settings.repository_loss_weights if use_repository else (0.0,)
        trial_parameters = expand_search_trials(
            settings,
            use_text=use_text,
            use_structure=use_structure,
            use_repository=use_repository,
            encoder_versions=tuple(name for name, _ in semantic_options),
        )
        trials: list[dict[str, object]] = []
        validation_histories: list[dict[str, object]] = []
        best_score = -float("inf")
        best_parameters: dict[str, object] | None = None
        best_checkpoint_path: Path | None = None
        best_trial_summary: dict[str, object] | None = None
        validation_path = validation_candidate_path(settings)
        schedule = ValidationSchedule(
            settings.validation_interval_epochs, settings.validation_start_epoch
        )
        early_stopping = EarlyStoppingPolicy(
            enabled=settings.early_stopping_enabled,
            patience=settings.early_stopping_patience,
            min_delta=settings.early_stopping_min_delta,
            min_epochs=settings.early_stopping_min_epochs,
        )
        training_strategy = {
            "validation_candidate": VALIDATION_CANDIDATE_FILENAME,
            "validation_group": "predictable",
            "selection_metric": settings.selection_metric,
            "validation_interval_epochs": settings.validation_interval_epochs,
            "validation_start_epoch": settings.validation_start_epoch,
            "early_stopping_enabled": settings.early_stopping_enabled,
            "early_stopping_patience": settings.early_stopping_patience,
            "early_stopping_min_delta": settings.early_stopping_min_delta,
            "early_stopping_min_epochs": settings.early_stopping_min_epochs,
            "max_batches_per_epoch": settings.max_batches_per_epoch,
            "checkpoint_interval_batches": settings.checkpoint_interval_batches,
            "checkpoint_format": "atomic latest/previous resume state plus full best-model checkpoint",
            "probe_batch": probe_batch,
        }
        search_space = {
            "embedding_dimensions": list(settings.embedding_dimensions),
            "issue_layers": list(settings.issue_layers),
            "learning_rates": list(settings.learning_rates),
            "structure_layers": list(structure_options),
            "repository_loss_weights": list(repository_weights),
            "encoder_versions": [name for name, _ in semantic_options],
            "design": "explicit" if settings.search_trials else "cartesian",
            "trials": list(trial_parameters),
        }
        checkpoint_identity = {
            "schema_version": 1,
            "run_id": run_id,
            "variant": variant,
            "dataset_version": settings.dataset_version,
            "graph_version": settings.graph_version,
            "model_version": settings.model_version,
            "seed": settings.seed,
            "batch_size": settings.batch_size,
            "weight_decay": settings.weight_decay,
            "epochs": settings.epochs,
            "max_batches_per_epoch": settings.max_batches_per_epoch,
            "search_space": search_space,
            "config_sha256": hashlib.sha256(settings.config_path.read_bytes()).hexdigest(),
            "training_code_sha256": _training_code_sha256(),
            "input_sha256": input_hashes,
            "text_cache": _semantic_cache_identity(settings, semantic_options),
        }
        recovery = CheckpointManager(run_dir / "checkpoints", checkpoint_identity)
        resume_payload: dict[str, object] | None = None
        start_trial = 1
        if resume:
            resume_payload, resumed_from = recovery.peek(map_location="cpu")
            saved_history = dict(resume_payload.get("history", {}))
            trials = list(saved_history.get("trials", []))
            validation_histories = list(saved_history.get("validation_histories", []))
            best_score = float(saved_history.get("best_score", -float("inf")))
            best_parameters = saved_history.get("best_parameters")
            best_trial_summary = saved_history.get("best_trial_summary")
            if best_trial_summary is not None:
                best_checkpoint_path = run_dir / str(best_trial_summary["best_checkpoint"])
            resume_progress = dict(resume_payload["progress"])
            start_trial = _resume_start_trial(resume_progress)
            monitor.update(
                LOGGER, "resuming",
                f"resuming from {resumed_from}: trial={start_trial}, "
                f"epoch={resume_progress.get('epoch', 1)}, "
                f"next_batch={resume_progress.get('next_batch', 0)}",
                **resume_progress,
            )
        total_trials = len(trial_parameters)
        monitor.update(
            LOGGER,
            "training",
            (
                f"hyperparameter grid ready: {total_trials} trial(s) x up to "
                f"{settings.epochs} epoch(s); validate every "
                f"{settings.validation_interval_epochs} epoch(s) from epoch "
                f"{settings.validation_start_epoch}; early_stopping="
                f"{settings.early_stopping_enabled}"
            ),
            total_trials=total_trials,
            epochs_per_trial=settings.epochs,
            training_strategy=training_strategy,
        )
        completed_trial_seconds: list[float] = []
        for trial_number, configured_parameters in enumerate(trial_parameters, start=1):
            if trial_number < start_trial:
                continue
            parameters = dict(configured_parameters)
            dimension = int(parameters["embedding_dim"])
            layers = int(parameters["issue_layers"])
            learning_rate = float(parameters["learning_rate"])
            structure_layers = int(parameters["structure_layers"])
            repository_weight = float(parameters["repository_loss_weight"])
            encoder_version = str(parameters["encoder_version"])
            semantic_array = semantic_by_version[encoder_version]
            trial_started = time.monotonic()
            monitor.update(
                LOGGER,
                "training",
                f"trial {trial_number}/{total_trials} started: {json.dumps(parameters, sort_keys=True)}",
                trial=trial_number,
                total_trials=total_trials,
                parameters=parameters,
            )
            seed_training(settings.seed)
            model = _build_trial_model(
                parameters=parameters,
                semantic_array=semantic_array,
                developer_count=developer_count,
                issue_count=issue_count,
                repository_count=repository_count,
                label_count=label_count,
                graphs=graphs,
                settings=settings,
                use_text=use_text,
                use_structure=use_structure,
                use_repository=use_repository,
                device=device,
            )
            optimizer = torch.optim.Adam(
                model.parameters(), lr=learning_rate, weight_decay=settings.weight_decay
            )
            criterion = BPRLoss()
            trial_resume_state: dict[str, object] = {}
            batch_resume_state = BatchResumeState()
            resume_epoch = 0
            if resume_payload is not None:
                progress_state = dict(resume_payload["progress"])
                if (
                    progress_state.get("phase") == "training"
                    and int(progress_state.get("trial", -1)) == trial_number
                ):
                    loaded, loaded_path = recovery.load(
                        model, optimizer, map_location=device
                    )
                    trial_resume_state = dict(
                        dict(loaded.get("history", {})).get("current_trial_state", {})
                    )
                    resume_epoch = int(progress_state.get("epoch", 1))
                    batch_resume_state = BatchResumeState(
                        next_batch=int(progress_state.get("next_batch", 0)),
                        issue_loss_sum=float(progress_state.get("issue_loss_sum", 0.0)),
                        repository_loss_sum=float(progress_state.get("repository_loss_sum", 0.0)),
                        processed=int(progress_state.get("processed", 0)),
                        repository_count=int(progress_state.get("repository_count", 0)),
                    )
                    trial_resume_state["start_epoch"] = resume_epoch
                    monitor.update(
                        LOGGER, "resuming",
                        f"restored model, Adam and RNG from {loaded_path}; "
                        f"trial={trial_number} epoch={resume_epoch} "
                        f"next_batch={batch_resume_state.next_batch}",
                    )
                resume_payload = None

            runtime_trial_state = dict(trial_resume_state)

            def recovery_history() -> dict[str, object]:
                return {
                    "trials": trials,
                    "validation_histories": validation_histories,
                    "best_score": best_score,
                    "best_parameters": best_parameters,
                    "best_trial_summary": best_trial_summary,
                    "current_trial_state": runtime_trial_state,
                }

            def train_epoch(epoch_number: int):
                epoch_started = time.monotonic()
                monitor.update(
                    LOGGER,
                    "training",
                    f"trial {trial_number}/{total_trials}, epoch {epoch_number}/{settings.epochs} started",
                    trial=trial_number,
                    total_trials=total_trials,
                    epoch=epoch_number,
                    total_epochs=settings.epochs,
                )

                def report_batch(progress: BatchProgress) -> None:
                    percent = 100.0 * progress.processed / progress.total
                    losses = f"issue_loss={progress.issue_loss:.6f}"
                    if progress.repository_loss is not None:
                        losses += f" repository_loss={progress.repository_loss:.6f}"
                    monitor.update(
                        LOGGER,
                        "training",
                        (
                            f"trial {trial_number}/{total_trials}, epoch "
                            f"{epoch_number}/{settings.epochs}, batch "
                            f"{progress.batch}/{progress.total_batches} ({percent:.1f}%), {losses}"
                        ),
                        force=progress.batch in {1, progress.total_batches},
                        trial=trial_number,
                        total_trials=total_trials,
                        epoch=epoch_number,
                        total_epochs=settings.epochs,
                        batch=progress.batch,
                        total_batches=progress.total_batches,
                        processed_comparisons=progress.processed,
                        total_comparisons=progress.total,
                        batch_seconds=progress.batch_seconds,
                        process_rss_bytes=progress.process_rss_bytes,
                        available_memory_bytes=progress.available_memory_bytes,
                        cuda_allocated_bytes=progress.cuda_allocated_bytes,
                        cuda_peak_allocated_bytes=progress.cuda_peak_allocated_bytes,
                    )

                def checkpoint_batch(progress: BatchProgress, state: BatchResumeState) -> None:
                    if (
                        progress.batch % settings.checkpoint_interval_batches != 0
                        and progress.batch != progress.total_batches
                        and not safe_interrupt()
                    ):
                        return
                    recovery.save(
                        model, optimizer,
                        progress={
                            "phase": "training", "trial": trial_number,
                            "total_trials": total_trials, "epoch": epoch_number,
                            "next_batch": state.next_batch,
                            "total_batches": progress.total_batches,
                            "issue_loss_sum": state.issue_loss_sum,
                            "repository_loss_sum": state.repository_loss_sum,
                            "processed": state.processed,
                            "repository_count": state.repository_count,
                            "parameters": parameters,
                        },
                        history=recovery_history(),
                    )

                result = train_coder_epoch(
                    model,
                    optimizer=optimizer,
                    criterion=criterion,
                    developer_ids=train.developer_ids,
                    issue_sampler=issue_sampler,
                    repository_sampler=repository_sampler,
                    repository_weight=repository_weight,
                    epoch=epoch_number - 1,
                    batch_size=settings.batch_size,
                    seed=settings.seed,
                    device=device,
                    progress_callback=report_batch,
                    checkpoint_callback=checkpoint_batch,
                    resume_state=(
                        batch_resume_state if epoch_number == resume_epoch else None
                    ),
                    max_batches=settings.max_batches_per_epoch,
                    stop_requested=safe_interrupt,
                )
                epoch_seconds = time.monotonic() - epoch_started
                remaining_epochs = settings.epochs - epoch_number
                monitor.update(
                    LOGGER,
                    "training",
                    (
                        f"trial {trial_number}/{total_trials}, epoch "
                        f"{epoch_number}/{settings.epochs} complete in {format_duration(epoch_seconds)}; "
                        f"issue_loss={result.issue_loss:.6f}; "
                        f"estimated trial remainder={format_duration(epoch_seconds * remaining_epochs)}"
                    ),
                    trial=trial_number,
                    total_trials=total_trials,
                    epoch=epoch_number,
                    total_epochs=settings.epochs,
                    issue_loss=result.issue_loss,
                    repository_loss=result.repository_loss,
                )
                return result

            def checkpoint_epoch(state: dict[str, object]) -> None:
                nonlocal runtime_trial_state
                runtime_trial_state = dict(state)
                recovery.save(
                    model, optimizer,
                    progress={
                        "phase": "training", "trial": trial_number,
                        "total_trials": total_trials,
                        "epoch": int(state["start_epoch"]), "next_batch": 0,
                        "parameters": parameters,
                    },
                    history=recovery_history(),
                )
                if safe_interrupt():
                    raise KeyboardInterrupt("safe stop requested after epoch checkpoint")

            def evaluate_validation(current_model):
                ranker = TorchIssueRanker(current_model, known, device=device)
                return evaluate_issue_groups(
                    ranker,
                    validation_path,
                    cross_repository_users=cross_users,
                    cutoffs=settings.baseline.protocol.cutoffs,
                )

            def report_validation(record: dict[str, object]) -> None:
                patience = int(record["validations_without_improvement"])
                repository_loss = record["repository_loss"]
                repository_segment = (
                    ""
                    if repository_loss is None
                    else f" | repository_loss={float(repository_loss):.6f}"
                )
                monitor.update(
                    LOGGER,
                    "validation",
                    (
                        f"trial {trial_number}/{total_trials} | epoch "
                        f"{record['epoch']}/{settings.epochs} | "
                        f"train_loss={float(record['issue_loss']):.6f}"
                        f"{repository_segment} | "
                        f"valid_{settings.selection_metric}="
                        f"{float(record['validation_score']):.6f} | "
                        f"best_epoch={record['best_epoch']} | patience="
                        f"{patience}/{settings.early_stopping_patience} | "
                        f"validation_time={format_duration(float(record['validation_seconds']))}"
                    ),
                    trial=trial_number,
                    total_trials=total_trials,
                    epoch=record["epoch"],
                    total_epochs=settings.epochs,
                    validation_score=record["validation_score"],
                    best_validation_score=record["best_validation_score"],
                    best_epoch=record["best_epoch"],
                    validations_without_improvement=patience,
                )

            checkpoint_path = run_dir / "checkpoints" / f"trial-{trial_number:04d}.best.pt"
            trial_result = run_training_trial(
                model,
                epochs=settings.epochs,
                schedule=schedule,
                policy=early_stopping,
                checkpoint_path=checkpoint_path,
                checkpoint_metadata={
                    "trial": trial_number,
                    "variant": variant,
                    "dataset_version": settings.dataset_version,
                    "graph_version": settings.graph_version,
                    "seed": settings.seed,
                    "selection_metric": settings.selection_metric,
                    "parameters": parameters,
                },
                train_epoch=train_epoch,
                evaluator=evaluate_validation,
                selection_metric=settings.selection_metric,
                map_location=device,
                on_validation=report_validation,
                resume_state=trial_resume_state,
                on_epoch_committed=checkpoint_epoch,
                restore_and_verify_best=not probe_batch,
            )
            score = trial_result.best_validation_score
            checkpoint_relative = str(checkpoint_path.relative_to(run_dir))
            trials.append(
                {
                    "parameters": parameters,
                    "validation": trial_result.best_validation,
                    "history": trial_result.history,
                    "score": score,
                    "best_epoch": trial_result.best_epoch,
                    "epochs_trained": trial_result.epochs_trained,
                    "best_validation_score": score,
                    "stopped_early": trial_result.stopped_early,
                    "stop_reason": trial_result.stop_reason,
                    "best_checkpoint": checkpoint_relative,
                    "restored_validation_score": trial_result.restored_validation_score,
                }
            )
            validation_histories.append(
                {
                    "trial": trial_number,
                    "parameters": parameters,
                    "validations": [
                        {"trial": trial_number, "parameters": parameters, **record}
                        for record in trial_result.validation_history
                    ],
                    "best_epoch": trial_result.best_epoch,
                    "epochs_trained": trial_result.epochs_trained,
                    "best_validation_score": score,
                    "stopped_early": trial_result.stopped_early,
                    "stop_reason": trial_result.stop_reason,
                }
            )
            if score > best_score:
                best_score = score
                best_parameters = dict(parameters)
                best_checkpoint_path = checkpoint_path
                best_trial_summary = {
                    "trial": trial_number,
                    "best_epoch": trial_result.best_epoch,
                    "epochs_trained": trial_result.epochs_trained,
                    "best_validation_score": score,
                    "stopped_early": trial_result.stopped_early,
                    "stop_reason": trial_result.stop_reason,
                    "best_checkpoint": checkpoint_relative,
                }
            trial_seconds = time.monotonic() - trial_started
            completed_trial_seconds.append(trial_seconds)
            remaining_trials = total_trials - trial_number
            mean_trial_seconds = sum(completed_trial_seconds) / len(completed_trial_seconds)
            monitor.update(
                LOGGER,
                "validation",
                (
                    f"trial {trial_number}/{total_trials} complete in {format_duration(trial_seconds)}; "
                    f"best_epoch={trial_result.best_epoch}; trained_epochs="
                    f"{trial_result.epochs_trained}; stop={trial_result.stop_reason}; "
                    f"predictable {settings.selection_metric}={score:.6f}; "
                    f"global_best={best_score:.6f}; "
                    f"estimated grid remainder={format_duration(mean_trial_seconds * remaining_trials)}"
                ),
                trial=trial_number,
                total_trials=total_trials,
                validation_score=score,
                best_validation_score=best_score,
            )
            if trial_result.stopped_early:
                LOGGER.info(
                    "trial %d/%d | Early Stopping | best_epoch=%d | trained_epochs=%d",
                    trial_number,
                    total_trials,
                    trial_result.best_epoch,
                    trial_result.epochs_trained,
                )
            runtime_trial_state = {}
            if probe_batch:
                final_epoch = trial_result.history[-1]
                _finish_probe_run(
                    monitor,
                    run_dir,
                    run_id=run_id,
                    variant=variant,
                    trial=trial_number,
                    parameters=parameters,
                    issue_loss=float(final_epoch["issue_loss"]),
                    repository_loss=(
                        None
                        if final_epoch["repository_loss"] is None
                        else float(final_epoch["repository_loss"])
                    ),
                    validation_score=score,
                    best_epoch=trial_result.best_epoch,
                    best_checkpoint=checkpoint_relative,
                )
                return run_dir
            recovery.save(
                model, optimizer,
                progress={
                    "phase": "between_trials", "next_trial": trial_number + 1,
                    "total_trials": total_trials,
                },
                history={
                    "trials": trials,
                    "validation_histories": validation_histories,
                    "best_score": best_score,
                    "best_parameters": best_parameters,
                    "best_trial_summary": best_trial_summary,
                    "current_trial_state": {},
                },
            )
        assert (
            best_parameters is not None
            and best_checkpoint_path is not None
            and best_trial_summary is not None
        )
        monitor.update(
            LOGGER,
            "freezing_model",
            (
                "global validation winner selected and restoring its best Epoch: "
                f"{json.dumps(best_parameters, sort_keys=True)}; "
                f"best_epoch={best_trial_summary['best_epoch']}"
            ),
            selected_parameters=best_parameters,
            best_validation_score=best_score,
            best_epoch=best_trial_summary["best_epoch"],
        )
        _write_json(run_dir / "validation_selection.json", trials)
        _write_json(
            run_dir / "validation_history.json",
            {
                "schema_version": 1,
                **training_strategy,
                "search_space": search_space,
                "trials": validation_histories,
            },
        )

        # Test candidates are used for metric evaluation only after the validation winner is frozen.
        best_semantic = semantic_by_version[str(best_parameters["encoder_version"])]
        best_model = _build_trial_model(
            parameters=best_parameters,
            semantic_array=best_semantic,
            developer_count=developer_count,
            issue_count=issue_count,
            repository_count=repository_count,
            label_count=label_count,
            graphs=graphs,
            settings=settings,
            use_text=use_text,
            use_structure=use_structure,
            use_repository=use_repository,
            device=device,
        )
        restored_checkpoint = restore_best_checkpoint(
            best_checkpoint_path, best_model, map_location=device
        )
        if restored_checkpoint["epoch"] != best_trial_summary["best_epoch"]:
            raise RuntimeError("global best checkpoint epoch does not match selection metadata")
        best_model.eval()
        with torch.inference_mode():
            restored_ranker = TorchIssueRanker(best_model, known, device=device)
            restored_validation = evaluate_issue_groups(
                restored_ranker,
                validation_path,
                cross_repository_users=cross_users,
                cutoffs=settings.baseline.protocol.cutoffs,
            )
        restored_global_score = float(
            restored_validation["predictable"][settings.selection_metric]
        )
        if not math.isfinite(restored_global_score) or not math.isclose(
            restored_global_score, best_score, rel_tol=1e-6, abs_tol=1e-8
        ):
            raise RuntimeError(
                "global best checkpoint score changed after reconstruction: "
                f"selected={best_score}, restored={restored_global_score}"
            )
        monitor.update(
            LOGGER,
            "freezing_model",
            (
                f"global best checkpoint restored and verified: epoch="
                f"{best_trial_summary['best_epoch']} {settings.selection_metric}="
                f"{restored_global_score:.6f}"
            ),
            restored_validation_score=restored_global_score,
        )
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
                "best_trial": best_trial_summary["trial"],
                "best_epoch": best_trial_summary["best_epoch"],
                "epochs_trained": best_trial_summary["epochs_trained"],
                "best_validation_score": best_score,
                "restored_validation_score": restored_global_score,
                "stopped_early": best_trial_summary["stopped_early"],
                "stop_reason": best_trial_summary["stop_reason"],
                "selection_checkpoint": best_trial_summary["best_checkpoint"],
                "training_strategy": training_strategy,
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
        metrics: dict[str, object] = {
            "schema_version": 2,
            "protocol": {
                "issue": {
                    "positive_definition": "one frozen target Issue per query",
                    "candidate_source": "frozen time-aware candidate parquet",
                    "selection": "valid_cross_repo.predictable.ndcg@10",
                },
                "repository": {
                    "status": "UNAVAILABLE_INDEPENDENT_PROTOCOL",
                    "reason": (
                        "No independently frozen Repository query/candidate artifact exists; "
                        "Issue candidates must not be collapsed into Repository candidates and "
                        "reported as an independent test protocol."
                    ),
                },
            },
            "issue": {},
            "repository": {
                "status": "UNAVAILABLE_INDEPENDENT_PROTOCOL",
                "metrics": None,
            },
        }
        for split in ("valid", "test"):
            metrics["issue"][split] = {}
            for mode in settings.baseline.protocol.candidate_modes:
                candidate_path = settings.candidate_dir / f"{split}_{mode}.parquet"
                monitor.update(
                    LOGGER,
                    "evaluation",
                    f"evaluating issue ranking: split={split} mode={mode}",
                    evaluation_split=split,
                    candidate_mode=mode,
                    evaluation_target="issue",
                )
                metrics["issue"][split][mode] = evaluate_issue_groups(
                    ranker, candidate_path, cross_repository_users=cross_users,
                    cutoffs=settings.baseline.protocol.cutoffs,
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
        monitor.update(LOGGER, "exporting", "writing metrics and exported embeddings")
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
            "best_trial": best_trial_summary["trial"],
            "best_epoch": best_trial_summary["best_epoch"],
            "epochs_trained": best_trial_summary["epochs_trained"],
            "best_validation_score": best_score,
            "restored_validation_score": restored_global_score,
            "stopped_early": best_trial_summary["stopped_early"],
            "stop_reason": best_trial_summary["stop_reason"],
            "best_checkpoint": best_trial_summary["best_checkpoint"],
            "training_strategy": training_strategy,
            "search_space": search_space,
            "test_gate": "test evaluated after validation selection and model bundle freeze",
            "project_relation_semantics": "project-level auxiliary propagation derived from issue solutions",
            "versions": {"python": sys.version.split()[0], "torch": torch.__version__, "platform": platform.platform()},
        }
        _write_json(run_dir / "run_manifest.json", run_manifest)
        monitor.update(
            LOGGER,
            "complete",
            f"training workflow complete; artifacts written to {run_dir}",
        )
        monitor.finish("success")
        return run_dir
    except KeyboardInterrupt as exc:
        latest = run_dir / "checkpoints" / "latest.pt"
        LOGGER.warning(
            "training cancelled by user; last valid checkpoint=%s", latest if latest.is_file() else "none"
        )
        monitor.finish(
            "cancelled", error_type=type(exc).__name__,
            error_message=f"Ctrl+C; last_valid_checkpoint={latest if latest.is_file() else 'none'}",
        )
        raise
    except Exception as exc:
        LOGGER.exception("training run failed; preserving diagnostics in %s", run_dir)
        monitor.finish(
            "failed", error_type=type(exc).__name__, error_message=str(exc)
        )
        raise
    finally:
        safe_interrupt.restore()
        monitor.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--variant", choices=sorted(VARIANT_FLAGS), required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--execute-training", action="store_true",
        help="required safety switch; without it only inputs/configuration are validated",
    )
    parser.add_argument(
        "--progress-interval-seconds",
        type=float,
        default=None,
        help="override the configured maximum interval between training progress messages",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="resume the same incomplete run-id from its latest valid checkpoint",
    )
    parser.add_argument(
        "--probe-batch", action="store_true",
        help="execute exactly one real-data epoch containing one forward/backward batch",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(level="INFO", log_format="text")
    try:
        LOGGER.info(
            "loading training configuration: config=%s variant=%s run_id=%s execute=%s",
            args.config,
            args.variant,
            args.run_id,
            args.execute_training,
        )
        settings = load_neural_settings(args.config)
        if args.probe_batch:
            if not args.execute_training:
                raise ValueError("--probe-batch requires --execute-training")
            if args.resume:
                raise ValueError("--probe-batch and --resume cannot be combined")
            settings = replace(
                settings, epochs=1, max_batches_per_epoch=1,
                validation_interval_epochs=1, validation_start_epoch=1,
                early_stopping_enabled=False,
            )
        if args.progress_interval_seconds is not None and args.progress_interval_seconds <= 0:
            raise ValueError("--progress-interval-seconds must be positive")
        LOGGER.info(
            "validating frozen dataset, mappings, graph, split, and candidate hashes for %s",
            settings.dataset_version,
        )
        hashes = validate_neural_inputs(
            settings, require_text=_variant_flags(args.variant)[0] and args.execute_training
        )
        LOGGER.info("validated %d immutable input artifacts", len(hashes))
        if not args.execute_training:
            if args.resume:
                raise ValueError("--resume requires --execute-training")
            LOGGER.info(
                "dry-run validated %d frozen inputs for %s; no training started",
                len(hashes), args.variant,
            )
            return 0
        output = train_variant(
            settings,
            variant=args.variant,
            run_id=args.run_id,
            validated_input_hashes=hashes,
            progress_interval_seconds=args.progress_interval_seconds,
            resume=args.resume,
            probe_batch=args.probe_batch,
        )
        LOGGER.info("training workflow complete: %s", output)
        return 0
    except KeyboardInterrupt:
        LOGGER.warning("stage 3-5 workflow cancelled by user")
        return 130
    except Exception:
        LOGGER.exception("stage 3-5 workflow failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
