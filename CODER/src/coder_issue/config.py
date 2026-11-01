"""Typed, standard-library-only configuration loading for stage 0."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any

from .errors import ConfigurationError


@dataclass(frozen=True)
class ArtifactPaths:
    repository_root: Path
    artifact_root: Path
    normalized: Path
    embeddings: Path
    graphs: Path
    models: Path
    runs: Path
    raw_data_files: tuple[Path, ...]


@dataclass(frozen=True)
class LoggingSettings:
    level: str
    format: str


@dataclass(frozen=True)
class ManifestSettings:
    output: Path
    chunk_size_bytes: int


@dataclass(frozen=True)
class Settings:
    schema_version: int
    config_name: str
    seed: int
    dataset_version: str
    paths: ArtifactPaths
    logging: LoggingSettings
    manifest: ManifestSettings


def _required(mapping: dict[str, Any], key: str, section: str) -> Any:
    try:
        return mapping[key]
    except KeyError as exc:
        raise ConfigurationError(f"missing configuration key: {section}.{key}") from exc


def load_settings(config_path: str | Path) -> Settings:
    """Load a TOML file and resolve every configured path deterministically."""

    path = Path(config_path).resolve()
    try:
        with path.open("rb") as stream:
            raw = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"cannot load configuration {path}: {exc}") from exc

    paths_raw = _required(raw, "paths", "root")
    logging_raw = _required(raw, "logging", "root")
    manifest_raw = _required(raw, "manifest", "root")

    repository_root = (path.parent / _required(paths_raw, "repository_root", "paths")).resolve()
    artifact_root = repository_root / _required(paths_raw, "artifact_root", "paths")
    raw_data_files = tuple(
        repository_root / item
        for item in _required(paths_raw, "raw_data_files", "paths")
    )
    chunk_size = int(_required(manifest_raw, "chunk_size_bytes", "manifest"))
    if chunk_size <= 0:
        raise ConfigurationError("manifest.chunk_size_bytes must be positive")

    log_format = str(_required(logging_raw, "format", "logging")).lower()
    if log_format not in {"json", "text"}:
        raise ConfigurationError("logging.format must be 'json' or 'text'")

    return Settings(
        schema_version=int(_required(raw, "schema_version", "root")),
        config_name=str(_required(raw, "config_name", "root")),
        seed=int(_required(raw, "seed", "root")),
        dataset_version=str(_required(raw, "dataset_version", "root")),
        paths=ArtifactPaths(
            repository_root=repository_root,
            artifact_root=artifact_root,
            normalized=artifact_root / _required(paths_raw, "normalized_dir", "paths"),
            embeddings=artifact_root / _required(paths_raw, "embeddings_dir", "paths"),
            graphs=artifact_root / _required(paths_raw, "graphs_dir", "paths"),
            models=artifact_root / _required(paths_raw, "models_dir", "paths"),
            runs=artifact_root / _required(paths_raw, "runs_dir", "paths"),
            raw_data_files=raw_data_files,
        ),
        logging=LoggingSettings(
            level=str(_required(logging_raw, "level", "logging")).upper(),
            format=log_format,
        ),
        manifest=ManifestSettings(
            output=repository_root / _required(manifest_raw, "output", "manifest"),
            chunk_size_bytes=chunk_size,
        ),
    )
