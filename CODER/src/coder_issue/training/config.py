"""Configuration for the stage 3-5 training workflow."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import tomllib
from typing import Any

from coder_issue.baselines.config import BaselineSettings, load_baseline_settings, validate_frozen_inputs
from coder_issue.errors import ConfigurationError, DataFileError


VALID_VARIANTS = {
    "lightgcn", "coder_issue_text", "coder_issue_structure", "coder_issue_base", "coder_issue_repo"
}


@dataclass(frozen=True)
class EncoderOption:
    name: str
    revision: str
    kind: str


@dataclass(frozen=True)
class NeuralSettings:
    config_path: Path
    config_name: str
    dataset_version: str
    model_version: str
    graph_version: str
    seed: int
    baseline: BaselineSettings
    candidate_dir: Path
    embedding_dir: Path
    model_dir: Path
    run_dir: Path
    device: str
    epochs: int
    batch_size: int
    progress_interval_seconds: float
    validation_interval_epochs: int
    validation_start_epoch: int
    early_stopping_enabled: bool
    early_stopping_patience: int
    early_stopping_min_delta: float
    early_stopping_min_epochs: int
    embedding_dimensions: tuple[int, ...]
    issue_layers: tuple[int, ...]
    learning_rates: tuple[float, ...]
    weight_decay: float
    repository_layers: int
    repository_loss_weights: tuple[float, ...]
    selection_metric: str
    structure_layers: tuple[int, ...]
    structure_heads: int
    structure_dropout: float
    max_segments: int
    text_batch_size: int
    allow_download: bool
    encoders: tuple[EncoderOption, ...]
    variants: tuple[str, ...]


def _get(data: dict[str, Any], key: str, section: str) -> Any:
    try:
        return data[key]
    except KeyError as exc:
        raise ConfigurationError(f"missing configuration key: {section}.{key}") from exc


def load_neural_settings(path_value: str | Path) -> NeuralSettings:
    path = Path(path_value).resolve()
    try:
        with path.open("rb") as stream:
            raw = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"cannot load neural configuration {path}: {exc}") from exc
    inputs, outputs = _get(raw, "inputs", "root"), _get(raw, "outputs", "root")
    training, structure = _get(raw, "training", "root"), _get(raw, "structure", "root")
    text, variants_raw = _get(raw, "text", "root"), _get(raw, "variants", "root")
    resolve = lambda value: (path.parent / str(value)).resolve()
    baseline = load_baseline_settings(resolve(_get(inputs, "baseline_config", "inputs")))
    variants = tuple(str(value) for value in _get(variants_raw, "enabled", "variants"))
    if not variants or not set(variants) <= VALID_VARIANTS:
        raise ConfigurationError("variants.enabled contains an unsupported model variant")
    dataset_version = str(_get(raw, "dataset_version", "root"))
    if baseline.dataset_version != dataset_version:
        raise ConfigurationError("neural and baseline dataset versions must match")
    graph_version = str(_get(raw, "graph_version", "root"))
    if graph_version != dataset_version:
        raise ConfigurationError("graph_version must match the frozen dataset version")
    epochs = int(_get(training, "epochs", "training"))
    batch_size = int(_get(training, "batch_size", "training"))
    progress_interval_seconds = float(
        _get(training, "progress_interval_seconds", "training")
    )
    validation_interval_epochs = int(
        _get(training, "validation_interval_epochs", "training")
    )
    validation_start_epoch = int(_get(training, "validation_start_epoch", "training"))
    early_stopping_enabled = bool(_get(training, "early_stopping_enabled", "training"))
    early_stopping_patience = int(_get(training, "early_stopping_patience", "training"))
    early_stopping_min_delta = float(
        _get(training, "early_stopping_min_delta", "training")
    )
    early_stopping_min_epochs = int(
        _get(training, "early_stopping_min_epochs", "training")
    )
    dimensions = tuple(int(x) for x in _get(training, "embedding_dimensions", "training"))
    issue_layers = tuple(int(x) for x in _get(training, "issue_layers", "training"))
    learning_rates = tuple(float(x) for x in _get(training, "learning_rates", "training"))
    repository_weights = tuple(
        float(x) for x in _get(training, "repository_loss_weights", "training")
    )
    structure_layers = tuple(int(x) for x in _get(structure, "layers", "structure"))
    heads = int(_get(structure, "heads", "structure"))
    if not all((dimensions, issue_layers, learning_rates, repository_weights, structure_layers)):
        raise ConfigurationError("training and structure search grids cannot be empty")
    if min(epochs, batch_size, heads, *dimensions, *issue_layers, *structure_layers) <= 0:
        raise ConfigurationError("epochs, batch size, heads, and dimensions must be positive")
    if min(learning_rates) <= 0 or min(repository_weights) < 0:
        raise ConfigurationError("learning rates must be positive and repository weights non-negative")
    if progress_interval_seconds <= 0:
        raise ConfigurationError("training.progress_interval_seconds must be positive")
    if validation_interval_epochs <= 0:
        raise ConfigurationError("training.validation_interval_epochs must be positive")
    if validation_start_epoch <= 0:
        raise ConfigurationError("training.validation_start_epoch must be positive")
    if early_stopping_patience <= 0:
        raise ConfigurationError("training.early_stopping_patience must be positive")
    if not math.isfinite(early_stopping_min_delta) or early_stopping_min_delta < 0:
        raise ConfigurationError(
            "training.early_stopping_min_delta must be finite and non-negative"
        )
    if early_stopping_min_epochs <= 0:
        raise ConfigurationError("training.early_stopping_min_epochs must be positive")
    if any(dimension % heads for dimension in dimensions):
        raise ConfigurationError("every embedding dimension must be divisible by GAT heads")
    return NeuralSettings(
        config_path=path,
        config_name=str(_get(raw, "config_name", "root")),
        dataset_version=dataset_version,
        model_version=str(_get(raw, "model_version", "root")),
        graph_version=graph_version,
        seed=int(_get(raw, "seed", "root")),
        baseline=baseline,
        candidate_dir=resolve(_get(inputs, "candidate_dir", "inputs")),
        embedding_dir=resolve(_get(outputs, "embedding_dir", "outputs")),
        model_dir=resolve(_get(outputs, "model_dir", "outputs")),
        run_dir=resolve(_get(outputs, "run_dir", "outputs")),
        device=str(_get(training, "device", "training")),
        epochs=epochs,
        batch_size=batch_size,
        progress_interval_seconds=progress_interval_seconds,
        validation_interval_epochs=validation_interval_epochs,
        validation_start_epoch=validation_start_epoch,
        early_stopping_enabled=early_stopping_enabled,
        early_stopping_patience=early_stopping_patience,
        early_stopping_min_delta=early_stopping_min_delta,
        early_stopping_min_epochs=early_stopping_min_epochs,
        embedding_dimensions=dimensions,
        issue_layers=issue_layers,
        learning_rates=learning_rates,
        weight_decay=float(_get(training, "weight_decay", "training")),
        repository_layers=int(_get(training, "repository_layers", "training")),
        repository_loss_weights=repository_weights,
        selection_metric=str(_get(training, "selection_metric", "training")),
        structure_layers=structure_layers,
        structure_heads=heads,
        structure_dropout=float(_get(structure, "dropout", "structure")),
        max_segments=int(_get(text, "max_segments", "text")),
        text_batch_size=int(_get(text, "batch_size", "text")),
        allow_download=bool(_get(text, "allow_download", "text")),
        encoders=tuple(
            EncoderOption(str(_get(item, "name", "text.encoders")), str(_get(item, "revision", "text.encoders")), str(_get(item, "kind", "text.encoders")))
            for item in _get(text, "encoders", "text")
        ),
        variants=variants,
    )


def validate_neural_inputs(settings: NeuralSettings, *, require_text: bool = False) -> dict[str, str]:
    hashes = validate_frozen_inputs(settings.baseline)
    manifest_path = settings.candidate_dir / "manifest.json"
    if not manifest_path.is_file():
        raise DataFileError(f"missing shared candidate manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for key in ("train/cross_repo", "valid/same_repo", "valid/cross_repo", "test/same_repo", "test/cross_repo"):
        entry = manifest.get(key)
        if not entry or not entry.get("validated"):
            raise DataFileError(f"candidate pool is absent or not validated: {key}")
        candidate_path = settings.candidate_dir / f"{key.replace('/', '_')}.parquet"
        hasher = hashlib.sha256()
        with candidate_path.open("rb") as stream:
            while chunk := stream.read(8 * 1024 * 1024):
                hasher.update(chunk)
        digest = hasher.hexdigest()
        if digest != entry.get("sha256"):
            raise DataFileError(f"candidate pool hash mismatch: {key}")
        hashes[f"candidate_{key.replace('/', '_')}"] = digest
    if require_text:
        for encoder in settings.encoders:
            if encoder.revision.startswith("REPLACE_"):
                raise ConfigurationError(
                    f"pin an immutable local revision before training text variant: {encoder.name}"
                )
    return hashes
