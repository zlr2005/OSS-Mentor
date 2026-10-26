"""Versioned CodeBERT file embeddings keyed by immutable file versions."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Protocol, Sequence
import uuid

import numpy as np


@dataclass(frozen=True)
class CodeDocument:
    file_version_key: str
    content: str


class CodeSegmentEncoder(Protocol):
    model_name: str
    revision: str
    dimension: int

    def encode(self, segments: Sequence[str]) -> np.ndarray: ...


@dataclass(frozen=True)
class CodeSegmentationConfig:
    max_lines_per_segment: int = 120
    max_segments: int = 16
    max_tokens: int = 256

    def __post_init__(self) -> None:
        if min(self.max_lines_per_segment, self.max_segments, self.max_tokens) <= 0:
            raise ValueError("code segmentation limits must be positive")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def segment_code(content: str, config: CodeSegmentationConfig) -> tuple[str, ...]:
    lines = content.splitlines()
    return tuple(
        "\n".join(lines[start : start + config.max_lines_per_segment])
        for start in range(0, len(lines), config.max_lines_per_segment)
    )[: config.max_segments]


class CodeEmbeddingArtifact:
    """Create an auditable embedding artifact without hiding failed files."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @staticmethod
    def _atomic_text(path: Path, value: str) -> None:
        temporary = path.parent / f".tmp-{uuid.uuid4().hex}"
        try:
            temporary.write_text(value, encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _atomic_array(path: Path, value: np.ndarray) -> None:
        temporary = path.parent / f".tmp-{uuid.uuid4().hex}.npy"
        try:
            np.save(temporary, value)
            np.load(temporary, mmap_mode="r").shape
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def encode(
        self,
        documents: Sequence[CodeDocument],
        encoder: CodeSegmentEncoder,
        *,
        artifact_version: str,
        source_version: str,
        tokenizer: dict[str, Any],
        segmentation: CodeSegmentationConfig = CodeSegmentationConfig(),
    ) -> dict[str, Any]:
        if not artifact_version.strip() or not source_version.strip():
            raise ValueError("artifact_version and source_version are required")
        keys = [item.file_version_key for item in documents]
        if any(not key.strip() for key in keys) or len(keys) != len(set(keys)):
            raise ValueError("file_version_key values must be non-empty and unique")
        self.root.mkdir(parents=True, exist_ok=True)

        rows: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        vectors: list[np.ndarray] = []
        for item in documents:
            content_hash = _sha256_bytes(item.content.encode("utf-8"))
            segments = segment_code(item.content, segmentation)
            if not segments:
                error = "empty_content"
            else:
                try:
                    encoded = np.asarray(encoder.encode(segments), dtype=np.float32)
                    if encoded.shape != (len(segments), encoder.dimension):
                        raise ValueError(f"unexpected encoder shape {encoded.shape}")
                    vector = encoded.mean(axis=0)
                    norm = float(np.linalg.norm(vector))
                    vector = vector / norm if norm else vector
                    if not np.isfinite(vector).all():
                        raise ValueError("embedding contains NaN or Inf")
                    embedding_row = len(vectors)
                    vectors.append(vector.astype(np.float32, copy=False))
                    rows.append({
                        "file_version_key": item.file_version_key,
                        "embedding_row": embedding_row,
                        "status": "success",
                        "content_sha256": content_hash,
                    })
                    continue
                except Exception as exc:  # retain per-file failure instead of zero-filling
                    error = f"{type(exc).__name__}: {exc}"
            rows.append({
                "file_version_key": item.file_version_key,
                "embedding_row": None,
                "status": "failed",
                "content_sha256": content_hash,
            })
            failures.append({"file_version_key": item.file_version_key, "error": error})

        embeddings = (
            np.stack(vectors).astype(np.float32, copy=False)
            if vectors
            else np.empty((0, encoder.dimension), dtype=np.float32)
        )
        embedding_path = self.root / "embeddings.npy"
        mapping_path = self.root / "row_mapping.jsonl"
        failure_path = self.root / "failures.jsonl"
        manifest_path = self.root / "encoder_manifest.json"
        success_path = self.root / "_SUCCESS"
        self._atomic_array(embedding_path, embeddings)
        self._atomic_text(mapping_path, "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
        ))
        self._atomic_text(failure_path, "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in failures
        ))
        manifest = {
            "schema_version": "code-embedding-artifact-v0.1",
            "artifact_version": artifact_version,
            "source_version": source_version,
            "model_name": encoder.model_name,
            "revision": encoder.revision,
            "tokenizer": tokenizer,
            "segmentation": {
                "strategy": "contiguous_lines",
                "max_lines_per_segment": segmentation.max_lines_per_segment,
                "max_segments": segmentation.max_segments,
                "max_tokens": segmentation.max_tokens,
            },
            "token_pooling": "attention_masked_mean",
            "segment_pooling": "mean_then_l2_normalize",
            "dimension": encoder.dimension,
            "dtype": "float32",
            "input_count": len(documents),
            "success_count": len(vectors),
            "failure_count": len(failures),
            "files": {
                "embeddings.npy": _sha256_file(embedding_path),
                "row_mapping.jsonl": _sha256_file(mapping_path),
                "failures.jsonl": _sha256_file(failure_path),
            },
        }
        self._atomic_text(
            manifest_path,
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        success = {
            "artifact_version": artifact_version,
            "manifest_sha256": _sha256_file(manifest_path),
        }
        self._atomic_text(success_path, json.dumps(success, sort_keys=True) + "\n")
        return manifest

    def validate(self) -> dict[str, Any]:
        manifest_path = self.root / "encoder_manifest.json"
        success_path = self.root / "_SUCCESS"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        success = json.loads(success_path.read_text(encoding="utf-8"))
        if success.get("manifest_sha256") != _sha256_file(manifest_path):
            raise ValueError("encoder manifest hash mismatch")
        for name, digest in manifest["files"].items():
            if _sha256_file(self.root / name) != digest:
                raise ValueError(f"artifact hash mismatch: {name}")
        embeddings = np.load(self.root / "embeddings.npy", mmap_mode="r")
        if embeddings.shape != (manifest["success_count"], manifest["dimension"]):
            raise ValueError("embedding shape does not match manifest")
        if embeddings.dtype != np.float32 or not np.isfinite(embeddings).all():
            raise ValueError("embeddings must be finite float32")
        rows = [json.loads(line) for line in (self.root / "row_mapping.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()]
        if len(rows) != manifest["input_count"]:
            raise ValueError("row mapping count does not match manifest")
        if len({row["file_version_key"] for row in rows}) != len(rows):
            raise ValueError("row mapping contains duplicate file_version_key values")
        success_rows = [row["embedding_row"] for row in rows if row["status"] == "success"]
        if success_rows != list(range(manifest["success_count"])):
            raise ValueError("successful embedding rows are not contiguous")
        return manifest
