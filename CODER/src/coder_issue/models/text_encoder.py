"""Creation-time Issue segmentation and versioned frozen-encoder cache."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Protocol, Sequence
import uuid

import numpy as np


CODE_BLOCK = re.compile(r"(```[\s\S]*?```)", re.MULTILINE)


@dataclass(frozen=True)
class IssueText:
    issue_id: int
    title: str
    body: str


@dataclass(frozen=True)
class SegmentedIssue:
    issue_id: int
    segments: tuple[str, ...]


def segment_issue_text(issue: IssueText, *, max_segments: int) -> SegmentedIssue:
    if max_segments <= 0:
        raise ValueError("max_segments must be positive")
    segments: list[str] = []
    title = issue.title.strip()
    if title:
        segments.append(f"[TITLE] {title}")
    for part in CODE_BLOCK.split(issue.body or ""):
        part = part.strip()
        if not part:
            continue
        if part.startswith("```") and part.endswith("```"):
            segments.append(f"[CODE] {part[3:-3].strip()}")
        else:
            segments.extend(
                f"[TEXT] {paragraph.strip()}"
                for paragraph in re.split(r"\n\s*\n", part)
                if paragraph.strip()
            )
        if len(segments) >= max_segments:
            break
    if not segments:
        segments = ["[TEXT] "]
    return SegmentedIssue(issue.issue_id, tuple(segments[:max_segments]))


class SegmentEncoder(Protocol):
    model_name: str
    revision: str
    dimension: int

    def encode(self, segments: Sequence[str]) -> np.ndarray: ...


class FrozenTransformerEncoder:
    """Lazy Hugging Face encoder; local weights only unless explicitly allowed."""

    def __init__(
        self,
        model_name: str,
        *,
        revision: str,
        device: str = "cpu",
        allow_download: bool = False,
        max_tokens: int = 256,
    ) -> None:
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError("install the 'encoders' extra to use Transformer encoding") from exc
        self.model_name = model_name
        self.revision = revision
        self.device = device
        self.max_tokens = max_tokens
        local_only = not allow_download
        source_is_local = Path(model_name).exists()
        revision_arguments = {} if source_is_local else {"revision": revision}
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_name, local_files_only=local_only, **revision_arguments
        )
        self.model = AutoModel.from_pretrained(
            model_name, local_files_only=local_only, **revision_arguments
        ).to(device)
        self.model.eval()
        self.model.requires_grad_(False)
        self.dimension = int(self.model.config.hidden_size)
        self._torch = torch

    def encode(self, segments: Sequence[str]) -> np.ndarray:
        if not segments:
            return np.empty((0, self.dimension), dtype=np.float32)
        tokens = self.tokenizer(
            list(segments), padding=True, truncation=True, max_length=self.max_tokens,
            return_tensors="pt",
        ).to(self.device)
        with self._torch.inference_mode():
            hidden = self.model(**tokens).last_hidden_state
            mask = tokens["attention_mask"].unsqueeze(-1)
            pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp_min(1)
        return pooled.cpu().numpy().astype(np.float32)


def _text_hash(item: SegmentedIssue) -> str:
    digest = hashlib.sha256()
    for segment in item.segments:
        digest.update(segment.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


class EmbeddingCache:
    """Row-aligned cache with validated, resumable issue chunks."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.manifest_path = root / "embedding_manifest.json"
        self.embedding_path = root / "issue_embeddings.npy"
        self.row_mapping_path = root / "row_mapping.npy"
        self.shard_dir = root / "shards"
        self.progress_path = root / "embedding_progress.json"

    @staticmethod
    def _atomic_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / f".tmp-{uuid.uuid4().hex}.json"
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _atomic_npy(path: Path, value: np.ndarray) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Keep the temporary name independent of the (potentially long) target name.
        # It stays beside the target so os.replace() is an atomic same-filesystem move.
        temporary = path.parent / f".tmp-{uuid.uuid4().hex}.npy"
        try:
            np.save(temporary, value)
            # A truncated shard must never be promoted.
            np.load(temporary, mmap_mode="r").shape
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _shard_paths(
        self,
        *,
        start: int,
        count: int,
        chunk_hash: str,
    ) -> tuple[Path, Path]:
        """Return the compact canonical path and the legacy hexadecimal path.

        URL-safe Base64 is a reversible representation of the complete SHA-256
        digest (43 rather than 64 characters), so no cache identity bits are lost.
        The legacy path remains readable to preserve completed chunks from older
        versions.
        """

        compact_hash = base64.urlsafe_b64encode(bytes.fromhex(chunk_hash)).decode(
            "ascii"
        ).rstrip("=")
        prefix = f"chunk-{start:08d}-{count:06d}-"
        return (
            self.shard_dir / f"{prefix}{compact_hash}.npy",
            self.shard_dir / f"{prefix}{chunk_hash}.npy",
        )

    @staticmethod
    def _array_sha256(value: np.ndarray) -> str:
        digest = hashlib.sha256()
        array = np.asarray(value)
        if array.ndim == 0:
            digest.update(array.tobytes())
        else:
            rows = max(1, (8 * 1024 * 1024) // max(1, array[0].nbytes))
            for start in range(0, len(array), rows):
                digest.update(np.ascontiguousarray(array[start : start + rows]).tobytes())
        return digest.hexdigest()

    @staticmethod
    def _all_finite(value: np.ndarray) -> bool:
        array = np.asarray(value)
        if array.ndim == 0:
            return bool(np.isfinite(array))
        rows = max(1, (8 * 1024 * 1024) // max(1, array[0].nbytes))
        return all(
            bool(np.isfinite(array[start : start + rows]).all())
            for start in range(0, len(array), rows)
        )

    def inspect(
        self,
        *,
        dataset_version: str,
        mapping_sha256: str,
        model_name: str,
        revision: str,
        dimension: int | None,
        max_segments: int,
        row_count: int,
    ) -> tuple[str, list[str]]:
        """Return CACHE_READY/PARTIAL/MISSING/INVALID without loading a model."""

        files = (self.embedding_path, self.manifest_path, self.row_mapping_path)
        present = [path.is_file() for path in files]
        has_shards = self.shard_dir.is_dir() and any(self.shard_dir.glob("*.npy"))
        if not any(present) and not has_shards:
            return "CACHE_MISSING", ["no final cache or resumable chunks"]
        if not all(present):
            return ("CACHE_PARTIAL" if has_shards else "CACHE_INVALID"), [
                "final cache file set is incomplete"
            ]
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            mapping = np.load(self.row_mapping_path, mmap_mode="r")
            embeddings = np.load(self.embedding_path, mmap_mode="r")
            expected = {
                "dataset_version": dataset_version,
                "mapping_sha256": mapping_sha256,
                "model_name": model_name,
                "revision": revision,
                "max_segments": max_segments,
                "row_count": row_count,
            }
            mismatches = [
                f"{key}: expected {value!r}, got {manifest.get(key)!r}"
                for key, value in expected.items() if manifest.get(key) != value
            ]
            if dimension is not None and manifest.get("dimension") != dimension:
                mismatches.append(
                    f"dimension: expected {dimension!r}, got {manifest.get('dimension')!r}"
                )
            expected_dimension = int(manifest.get("dimension", -1))
            if embeddings.shape != (row_count, expected_dimension):
                mismatches.append(f"embedding shape is {embeddings.shape}")
            if mapping.shape != (row_count,) or not np.array_equal(
                mapping, np.arange(row_count, dtype=np.int64)
            ):
                mismatches.append("row mapping is not contiguous and aligned")
            if not self._all_finite(embeddings):
                mismatches.append("embeddings contain NaN or Inf")
            expected_hash = manifest.get("embedding_sha256")
            if expected_hash and self._array_sha256(embeddings) != expected_hash:
                mismatches.append("embedding content hash mismatch")
            return ("CACHE_INVALID", mismatches) if mismatches else ("CACHE_READY", [])
        except Exception as exc:
            return "CACHE_INVALID", [f"{type(exc).__name__}: {exc}"]

    def _encode_issue_chunk(
        self,
        items: Sequence[SegmentedIssue],
        encoder: SegmentEncoder,
        *,
        segment_batch_size: int,
    ) -> np.ndarray:
        flat = [segment for item in items for segment in item.segments]
        encoded_parts = [
            encoder.encode(flat[start : start + segment_batch_size])
            for start in range(0, len(flat), segment_batch_size)
        ]
        encoded = np.concatenate(encoded_parts, axis=0)
        result = np.zeros((len(items), encoder.dimension), dtype=np.float32)
        cursor = 0
        for row, item in enumerate(items):
            end = cursor + len(item.segments)
            vector = encoded[cursor:end].mean(axis=0)
            norm = np.linalg.norm(vector)
            result[row] = vector / norm if norm else vector
            cursor = end
        return result

    def encode_or_reuse(
        self,
        items: Sequence[SegmentedIssue],
        encoder: SegmentEncoder,
        *,
        dataset_version: str,
        mapping_sha256: str,
        max_segments: int,
        batch_size: int = 64,
        segment_batch_size: int | None = None,
        chunk_size: int = 256,
        progress_callback=None,
    ) -> tuple[np.ndarray, dict[str, int]]:
        if batch_size <= 0 or chunk_size <= 0:
            raise ValueError("batch_size and chunk_size must be positive")
        segment_batch_size = batch_size if segment_batch_size is None else segment_batch_size
        if segment_batch_size <= 0:
            raise ValueError("segment_batch_size must be positive")
        ordered = sorted(items, key=lambda item: item.issue_id)
        ids = [item.issue_id for item in ordered]
        if ids != list(range(len(ids))):
            raise ValueError("embedding cache requires contiguous zero-based issue IDs")
        hashes = [_text_hash(item) for item in ordered]
        old_manifest: dict[str, object] = {}
        old_embeddings: np.ndarray | None = None
        if (
            self.manifest_path.is_file()
            and self.embedding_path.is_file()
            and self.row_mapping_path.is_file()
        ):
            old_manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            old_mapping = np.load(self.row_mapping_path)
            compatible = (
                old_manifest.get("dataset_version") == dataset_version
                and old_manifest.get("mapping_sha256") == mapping_sha256
                and old_manifest.get("model_name") == encoder.model_name
                and old_manifest.get("revision") == encoder.revision
                and old_manifest.get("dimension") == encoder.dimension
                and old_manifest.get("max_segments") == max_segments
                and np.array_equal(old_mapping, np.asarray(ids, dtype=np.int64))
            )
            if compatible:
                old_embeddings = np.load(self.embedding_path, mmap_mode="r")
        embeddings = np.zeros((len(ordered), encoder.dimension), dtype=np.float32)
        old_hashes = old_manifest.get("text_sha256", []) if old_embeddings is not None else []
        missing: list[int] = []
        reused = 0
        for index, digest in enumerate(hashes):
            if index < len(old_hashes) and old_hashes[index] == digest:
                embeddings[index] = old_embeddings[index]
                reused += 1
            else:
                missing.append(index)
        if isinstance(old_embeddings, np.memmap):
            old_embeddings._mmap.close()
            old_embeddings = None
        encoded_count = 0
        self.shard_dir.mkdir(parents=True, exist_ok=True)
        for start in range(0, len(missing), chunk_size):
            indices = missing[start : start + chunk_size]
            chunk_hash = hashlib.sha256(
                "\0".join(hashes[index] for index in indices).encode("ascii")
            ).hexdigest()
            shard, legacy_shard = self._shard_paths(
                start=start,
                count=len(indices),
                chunk_hash=chunk_hash,
            )
            chunk: np.ndarray | None = None
            for candidate_path in (shard, legacy_shard):
                if not candidate_path.is_file():
                    continue
                try:
                    candidate = np.load(candidate_path)
                    if candidate.shape == (len(indices), encoder.dimension) and self._all_finite(candidate):
                        chunk = candidate.astype(np.float32, copy=False)
                        break
                except Exception:
                    chunk = None
            if chunk is None:
                chunk = self._encode_issue_chunk(
                    [ordered[index] for index in indices], encoder,
                    segment_batch_size=segment_batch_size,
                )
                self._atomic_npy(shard, chunk)
                encoded_count += len(indices)
            else:
                reused += len(indices)
            embeddings[np.asarray(indices, dtype=np.int64)] = chunk
            self._atomic_json(self.progress_path, {
                "schema_version": 1,
                "dataset_version": dataset_version,
                "mapping_sha256": mapping_sha256,
                "model_name": encoder.model_name,
                "revision": encoder.revision,
                "dimension": encoder.dimension,
                "max_segments": max_segments,
                "row_count": len(ordered),
                "completed_rows": reused + encoded_count,
                "updated_at_unix": time.time(),
            })
            if progress_callback is not None:
                progress_callback(reused + encoded_count, len(ordered))
        self.root.mkdir(parents=True, exist_ok=True)
        self._atomic_npy(self.embedding_path, embeddings)
        self._atomic_npy(self.row_mapping_path, np.asarray(ids, dtype=np.int64))
        manifest = {
            "schema_version": 2,
            "dataset_version": dataset_version,
            "mapping_sha256": mapping_sha256,
            "model_name": encoder.model_name,
            "revision": encoder.revision,
            "dimension": encoder.dimension,
            "max_segments": max_segments,
            "row_count": len(ordered),
            "row_mapping_file": self.row_mapping_path.name,
            "text_sha256": hashes,
            "embedding_sha256": self._array_sha256(embeddings),
            "issue_chunk_size": chunk_size,
            "segment_batch_size": segment_batch_size,
        }
        # Manifest is committed last, so an interrupted finalization cannot look ready.
        self._atomic_json(self.manifest_path, manifest)
        self.progress_path.unlink(missing_ok=True)
        return embeddings, {"reused": reused, "encoded": encoded_count}
