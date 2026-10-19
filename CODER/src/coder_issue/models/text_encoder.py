"""Creation-time Issue segmentation and versioned frozen-encoder cache."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Protocol, Sequence

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
    """Row-aligned cache that re-encodes only missing or changed Issue text."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.manifest_path = root / "embedding_manifest.json"
        self.embedding_path = root / "issue_embeddings.npy"
        self.row_mapping_path = root / "row_mapping.npy"

    def encode_or_reuse(
        self,
        items: Sequence[SegmentedIssue],
        encoder: SegmentEncoder,
        *,
        dataset_version: str,
        mapping_sha256: str,
        max_segments: int,
        batch_size: int = 64,
    ) -> tuple[np.ndarray, dict[str, int]]:
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
        for start in range(0, len(missing), batch_size):
            indices = missing[start : start + batch_size]
            flat: list[str] = []
            spans: list[tuple[int, int]] = []
            for index in indices:
                begin = len(flat)
                flat.extend(ordered[index].segments)
                spans.append((begin, len(flat)))
            encoded = encoder.encode(flat)
            for index, (begin, end) in zip(indices, spans, strict=True):
                vector = encoded[begin:end].mean(axis=0)
                norm = np.linalg.norm(vector)
                embeddings[index] = vector / norm if norm else vector
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.embedding_path.with_suffix(".tmp.npy")
        np.save(temporary, embeddings)
        temporary.replace(self.embedding_path)
        np.save(self.row_mapping_path, np.asarray(ids, dtype=np.int64))
        manifest = {
            "schema_version": 1,
            "dataset_version": dataset_version,
            "mapping_sha256": mapping_sha256,
            "model_name": encoder.model_name,
            "revision": encoder.revision,
            "dimension": encoder.dimension,
            "max_segments": max_segments,
            "row_count": len(ordered),
            "row_mapping_file": self.row_mapping_path.name,
            "text_sha256": hashes,
        }
        self.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        return embeddings, {"reused": reused, "encoded": len(missing)}
