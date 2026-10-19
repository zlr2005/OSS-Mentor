"""Strict stage-2 configuration and frozen-input validation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import tomllib
from typing import Any

from coder_issue.errors import ConfigurationError, DataFileError


@dataclass(frozen=True)
class Protocol:
    split_protocol: str
    candidate_scope: str
    candidate_modes: tuple[str, ...]
    candidate_seed: int
    max_candidates: int
    cutoffs: tuple[int, ...]


@dataclass(frozen=True)
class TextOptions:
    max_features: int
    min_df: int
    ngram_max: int


@dataclass(frozen=True)
class BPROptions:
    dimensions: tuple[int, ...]
    learning_rates: tuple[float, ...]
    regularization: float
    epochs: int
    batch_size: int
    negative_pool_size: int


@dataclass(frozen=True)
class BaselineSettings:
    schema_version: int
    config_name: str
    dataset_version: str
    seed: int
    config_path: Path
    normalized_dir: Path
    graph_dir: Path
    models_dir: Path
    runs_dir: Path
    protocol: Protocol
    text: TextOptions
    bpr: BPROptions
    locks: dict[str, str]


def _required(data: dict[str, Any], key: str, section: str) -> Any:
    try:
        return data[key]
    except KeyError as exc:
        raise ConfigurationError(f"missing configuration key: {section}.{key}") from exc


def load_baseline_settings(config_path: str | Path) -> BaselineSettings:
    path = Path(config_path).resolve()
    try:
        with path.open("rb") as stream:
            raw = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"cannot load baseline configuration {path}: {exc}") from exc
    inputs = _required(raw, "inputs", "root")
    outputs = _required(raw, "outputs", "root")
    protocol = _required(raw, "protocol", "root")
    text = _required(raw, "text", "root")
    bpr = _required(raw, "bpr", "root")
    modes = tuple(str(x) for x in _required(protocol, "candidate_modes", "protocol"))
    if set(modes) != {"same_repo", "cross_repo"}:
        raise ConfigurationError("protocol.candidate_modes must contain same_repo and cross_repo")
    max_candidates = int(_required(protocol, "max_candidates", "protocol"))
    cutoffs = tuple(int(x) for x in _required(protocol, "cutoffs", "protocol"))
    if max_candidates < max(cutoffs) or min(cutoffs) <= 0:
        raise ConfigurationError("candidate cap must be >= every positive cutoff")
    resolve = lambda value: (path.parent / str(value)).resolve()
    return BaselineSettings(
        schema_version=int(_required(raw, "schema_version", "root")),
        config_name=str(_required(raw, "config_name", "root")),
        dataset_version=str(_required(raw, "dataset_version", "root")),
        seed=int(_required(raw, "seed", "root")),
        config_path=path,
        normalized_dir=resolve(_required(inputs, "normalized_dir", "inputs")),
        graph_dir=resolve(_required(inputs, "graph_dir", "inputs")),
        models_dir=resolve(_required(outputs, "models_dir", "outputs")),
        runs_dir=resolve(_required(outputs, "runs_dir", "outputs")),
        protocol=Protocol(
            split_protocol=str(_required(protocol, "split_protocol", "protocol")),
            candidate_scope=str(_required(protocol, "candidate_scope", "protocol")),
            candidate_modes=modes,
            candidate_seed=int(_required(protocol, "candidate_seed", "protocol")),
            max_candidates=max_candidates,
            cutoffs=cutoffs,
        ),
        text=TextOptions(
            max_features=int(_required(text, "max_features", "text")),
            min_df=int(_required(text, "min_df", "text")),
            ngram_max=int(_required(text, "ngram_max", "text")),
        ),
        bpr=BPROptions(
            dimensions=tuple(int(x) for x in _required(bpr, "dimensions", "bpr")),
            learning_rates=tuple(float(x) for x in _required(bpr, "learning_rates", "bpr")),
            regularization=float(_required(bpr, "regularization", "bpr")),
            epochs=int(_required(bpr, "epochs", "bpr")),
            batch_size=int(_required(bpr, "batch_size", "bpr")),
            negative_pool_size=int(_required(bpr, "negative_pool_size", "bpr")),
        ),
        locks={str(k): str(v).lower() for k, v in _required(raw, "locks", "root").items()},
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def validate_frozen_inputs(settings: BaselineSettings) -> dict[str, str]:
    """Reject any drift in the stage-1 split, mappings, or protocol."""

    files = {
        "split_manifest": settings.graph_dir / "split_manifest.json",
        "train": settings.graph_dir / "train.parquet",
        "valid": settings.graph_dir / "valid.parquet",
        "test": settings.graph_dir / "test.parquet",
        "developer_mapping": settings.normalized_dir / "mappings/developer_mapping.parquet",
        "issue_mapping": settings.normalized_dir / "mappings/issue_mapping.parquet",
        "repository_mapping": settings.normalized_dir / "mappings/repository_mapping.parquet",
        "issues": settings.normalized_dir / "issues.parquet",
        "developer_issue_edges": settings.normalized_dir / "developer_issue_edges.parquet",
        "issue_snapshots": settings.normalized_dir / "issue_snapshots.parquet",
    }
    actual: dict[str, str] = {}
    for name, file_path in files.items():
        if not file_path.is_file():
            raise DataFileError(f"missing frozen stage-1 input: {file_path}")
        actual[name] = _sha256(file_path)
        expected = settings.locks.get(f"{name}_sha256")
        if expected != actual[name]:
            raise DataFileError(f"frozen input hash mismatch: {name}")
    try:
        manifest = json.loads(files["split_manifest"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataFileError(f"cannot read split manifest: {exc}") from exc
    candidate = manifest.get("candidate_protocol", {})
    checks = {
        "dataset_version": (manifest.get("dataset_version"), settings.dataset_version),
        "split protocol": (manifest.get("protocol"), settings.protocol.split_protocol),
        "candidate scope": (candidate.get("scope"), settings.protocol.candidate_scope),
        "candidate seed": (candidate.get("seed"), settings.protocol.candidate_seed),
        "candidate modes": (set(candidate.get("modes", [])), set(settings.protocol.candidate_modes)),
    }
    for label, (actual_value, expected_value) in checks.items():
        if actual_value != expected_value:
            raise DataFileError(f"frozen {label} mismatch: {actual_value!r} != {expected_value!r}")
    return actual
