"""Validation scheduling, early stopping, and atomic best-checkpoint handling."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import os
from pathlib import Path
import time
from typing import Any, Callable
import uuid

import torch
from torch import nn

from .trainer import EpochResult


@dataclass(frozen=True)
class ValidationSchedule:
    interval_epochs: int
    start_epoch: int

    def __post_init__(self) -> None:
        if self.interval_epochs <= 0:
            raise ValueError("validation_interval_epochs must be positive")
        if self.start_epoch <= 0:
            raise ValueError("validation_start_epoch must be positive")

    def is_due(self, epoch: int, max_epochs: int) -> bool:
        if epoch <= 0 or max_epochs <= 0 or epoch > max_epochs:
            raise ValueError("epoch must be within 1..max_epochs")
        periodic = (
            epoch >= self.start_epoch
            and (epoch - self.start_epoch) % self.interval_epochs == 0
        )
        return periodic or epoch == max_epochs


@dataclass(frozen=True)
class EarlyStoppingPolicy:
    enabled: bool
    patience: int
    min_delta: float
    min_epochs: int

    def __post_init__(self) -> None:
        if self.patience <= 0:
            raise ValueError("early_stopping_patience must be positive")
        if not math.isfinite(self.min_delta) or self.min_delta < 0:
            raise ValueError("early_stopping_min_delta must be finite and non-negative")
        if self.min_epochs <= 0:
            raise ValueError("early_stopping_min_epochs must be positive")


@dataclass(frozen=True)
class ValidationDecision:
    improved: bool
    should_stop: bool
    best_score: float
    best_epoch: int
    validations_without_improvement: int


class TrialSelectionState:
    """Track one Trial; ties deliberately keep the earlier checkpoint."""

    def __init__(self, policy: EarlyStoppingPolicy) -> None:
        self.policy = policy
        self.best_score = -float("inf")
        self.best_epoch = 0
        self.validations_without_improvement = 0

    def observe(self, score: float, *, epoch: int) -> ValidationDecision:
        if not math.isfinite(score):
            raise ValueError(f"validation score must be finite, got {score!r} at epoch {epoch}")
        improved = self.best_epoch == 0 or score > self.best_score + self.policy.min_delta
        if improved:
            self.best_score = score
            self.best_epoch = epoch
            self.validations_without_improvement = 0
        else:
            self.validations_without_improvement += 1
        should_stop = (
            self.policy.enabled
            and epoch >= self.policy.min_epochs
            and self.validations_without_improvement >= self.policy.patience
        )
        return ValidationDecision(
            improved=improved,
            should_stop=should_stop,
            best_score=self.best_score,
            best_epoch=self.best_epoch,
            validations_without_improvement=self.validations_without_improvement,
        )


def _model_config(model: nn.Module) -> dict[str, Any] | None:
    config = getattr(model, "config", None)
    return asdict(config) if config is not None else None


def save_best_checkpoint(
    path: Path,
    model: nn.Module,
    *,
    epoch: int,
    validation_score: float,
    metadata: dict[str, Any],
) -> None:
    """Atomically save a self-contained model state snapshot for model selection."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    payload = {
        "schema_version": 1,
        "epoch": epoch,
        "validation_score": validation_score,
        "model_config": _model_config(model),
        "metadata": metadata,
        "model_state_dict": model.state_dict(),
    }
    try:
        torch.save(payload, temporary)
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def restore_best_checkpoint(
    path: Path,
    model: nn.Module,
    *,
    map_location: str | torch.device,
) -> dict[str, Any]:
    payload = torch.load(path, map_location=map_location, weights_only=True)
    expected_config = _model_config(model)
    if payload.get("model_config") != expected_config:
        raise ValueError("checkpoint model configuration does not match the target model")
    model.load_state_dict(payload["model_state_dict"], strict=True)
    return {
        "schema_version": int(payload["schema_version"]),
        "epoch": int(payload["epoch"]),
        "validation_score": float(payload["validation_score"]),
        "metadata": dict(payload["metadata"]),
    }


@dataclass(frozen=True)
class TrialRunResult:
    history: list[dict[str, Any]]
    validation_history: list[dict[str, Any]]
    best_validation: dict[str, Any]
    best_validation_score: float
    best_epoch: int
    epochs_trained: int
    stopped_early: bool
    stop_reason: str
    restored_validation_score: float


def _validate_model(
    model: nn.Module,
    evaluator: Callable[[nn.Module], dict[str, Any]],
    *,
    selection_group: str,
    selection_metric: str,
) -> tuple[dict[str, Any], float, float]:
    was_training = model.training
    started = time.monotonic()
    try:
        model.eval()
        with torch.inference_mode():
            report = evaluator(model)
    finally:
        model.train(was_training)
    elapsed = time.monotonic() - started
    try:
        score = float(report[selection_group][selection_metric])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"validation report is missing {selection_group}.{selection_metric}"
        ) from exc
    if not math.isfinite(score):
        raise ValueError(f"validation score must be finite, got {score!r}")
    return report, score, elapsed


def run_training_trial(
    model: nn.Module,
    *,
    epochs: int,
    schedule: ValidationSchedule,
    policy: EarlyStoppingPolicy,
    checkpoint_path: Path,
    checkpoint_metadata: dict[str, Any],
    train_epoch: Callable[[int], EpochResult],
    evaluator: Callable[[nn.Module], dict[str, Any]],
    selection_metric: str,
    selection_group: str = "predictable",
    map_location: str | torch.device = "cpu",
    on_validation: Callable[[dict[str, Any]], None] | None = None,
) -> TrialRunResult:
    """Train one Trial, restore its best Epoch, and verify the restored score."""

    if epochs <= 0:
        raise ValueError("epochs must be positive")
    tracker = TrialSelectionState(policy)
    history: list[dict[str, Any]] = []
    validation_history: list[dict[str, Any]] = []
    best_validation: dict[str, Any] | None = None
    stopped_early = False
    epochs_trained = 0

    for epoch in range(1, epochs + 1):
        result = train_epoch(epoch)
        epochs_trained = epoch
        history.append(dict(result.__dict__))
        if not schedule.is_due(epoch, epochs):
            continue
        report, score, validation_seconds = _validate_model(
            model,
            evaluator,
            selection_group=selection_group,
            selection_metric=selection_metric,
        )
        decision = tracker.observe(score, epoch=epoch)
        if decision.improved:
            save_best_checkpoint(
                checkpoint_path,
                model,
                epoch=epoch,
                validation_score=score,
                metadata=checkpoint_metadata,
            )
            best_validation = report
        record = {
            "epoch": epoch,
            "issue_loss": result.issue_loss,
            "repository_loss": result.repository_loss,
            "validation_score": score,
            "selection_group": selection_group,
            "selection_metric": selection_metric,
            "best_validation_score": decision.best_score,
            "best_epoch": decision.best_epoch,
            "improved": decision.improved,
            "validations_without_improvement": decision.validations_without_improvement,
            "validation_seconds": validation_seconds,
        }
        validation_history.append(record)
        if on_validation is not None:
            on_validation(record)
        if decision.should_stop:
            stopped_early = True
            break

    if best_validation is None or tracker.best_epoch == 0 or not checkpoint_path.is_file():
        raise RuntimeError("trial completed without a valid best checkpoint")
    checkpoint = restore_best_checkpoint(
        checkpoint_path, model, map_location=map_location
    )
    if checkpoint["epoch"] != tracker.best_epoch:
        raise RuntimeError("restored checkpoint epoch does not match the selected best epoch")
    restored_validation, restored_score, _ = _validate_model(
        model,
        evaluator,
        selection_group=selection_group,
        selection_metric=selection_metric,
    )
    if not math.isclose(restored_score, tracker.best_score, rel_tol=1e-6, abs_tol=1e-8):
        raise RuntimeError(
            "restored checkpoint score does not match its saved validation score: "
            f"saved={tracker.best_score}, restored={restored_score}"
        )
    return TrialRunResult(
        history=history,
        validation_history=validation_history,
        best_validation=restored_validation,
        best_validation_score=tracker.best_score,
        best_epoch=tracker.best_epoch,
        epochs_trained=epochs_trained,
        stopped_early=stopped_early,
        stop_reason="early_stopping" if stopped_early else "max_epochs",
        restored_validation_score=restored_score,
    )
