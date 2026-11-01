"""Prepare or resume one frozen issue-text embedding cache."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from coder_issue.baselines.dataset import mapping_sizes
from coder_issue.models.text_encoder import EmbeddingCache, FrozenTransformerEncoder
from coder_issue.training.config import load_neural_settings, validate_neural_inputs
from coder_issue.training.pipeline import _creation_texts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--encoder", help="exact configured model name; defaults to the sole encoder")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args(argv)
    settings = load_neural_settings(args.config)
    validate_neural_inputs(settings, require_text=True)
    choices = [item for item in settings.encoders if args.encoder in (None, item.name)]
    if len(choices) != 1:
        raise ValueError("select exactly one configured encoder with --encoder")
    option = choices[0]
    root = settings.embedding_dir / f"{option.name.replace('/', '__')}@{option.revision}"
    cache = EmbeddingCache(root)
    issue_count = mapping_sizes(settings.baseline)[1]
    status, reasons = cache.inspect(
        dataset_version=settings.dataset_version,
        mapping_sha256=settings.baseline.locks["issue_mapping_sha256"],
        model_name=option.name, revision=option.revision, dimension=None,
        max_segments=settings.max_segments, row_count=issue_count,
    )
    print(json.dumps({"status": status, "root": str(root), "reasons": reasons}, ensure_ascii=False))
    if args.check_only or status == "CACHE_READY":
        return 0 if status == "CACHE_READY" else 2
    encoder = FrozenTransformerEncoder(
        option.name, revision=option.revision, device=settings.device,
        allow_download=settings.allow_download,
    )
    texts = _creation_texts(settings, settings.max_segments)
    _, stats = cache.encode_or_reuse(
        texts, encoder, dataset_version=settings.dataset_version,
        mapping_sha256=settings.baseline.locks["issue_mapping_sha256"],
        max_segments=settings.max_segments, batch_size=settings.text_batch_size,
        segment_batch_size=settings.text_segment_batch_size,
        chunk_size=settings.text_chunk_size,
        progress_callback=lambda done, total: print(f"embedding rows {done}/{total}", flush=True),
    )
    print(json.dumps({"status": "CACHE_READY", **stats}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
