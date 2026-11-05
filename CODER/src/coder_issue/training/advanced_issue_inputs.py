"""Prepare time-safe historical Issue candidates and frozen CODER vectors."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
import uuid

import numpy as np
import pyarrow.parquet as pq
import torch

from coder_issue.models.text_encoder import FrozenTransformerEncoder, IssueText, segment_issue_text
from coder_issue.training.advanced_profile_data import normalize_issue_key, parse_timestamp


INPUT_SCHEMA_VERSION = "advanced-historical-snapshot-v0.1"
CANDIDATE_SCHEMA_VERSION = "advanced-historical-candidate-v0.1"
MANIFEST_SCHEMA_VERSION = "advanced-issue-input-manifest-v0.1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(payload: Any) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _records(path: Path) -> Iterable[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: row must be an object")
            yield row


def _content_sha256(title: str, body: str) -> str:
    payload = _canonical_json({"title": title, "body": body}) + b"\n"
    return hashlib.sha256(payload).hexdigest()


def prepare_candidate_rows(
    snapshots: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Select one deterministic, exact-cutoff text version per Issue key.

    PR #33's v0.1 cache is keyed by Issue key rather than Issue version.  To
    avoid silently choosing future text, the earliest verified snapshot is
    retained and is available only at that snapshot's cutoff.  Other versions
    are reported rather than merged.
    """

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    invalid: list[dict[str, Any]] = []
    for index, raw in enumerate(snapshots, 1):
        try:
            issue_key = normalize_issue_key(raw.get("issue_key"))
            as_of = parse_timestamp(raw.get("as_of"), "as_of")
            title_value = raw.get("title_at_cutoff")
            if not isinstance(title_value, str) or not title_value.strip():
                raise ValueError("title_at_cutoff must be a non-empty string")
            # Content hashes are defined over the exact historical bytes.  Do
            # not strip title whitespace before validating or encoding it.
            title = title_value
            body_value = raw.get("body_at_cutoff")
            body = body_value if isinstance(body_value, str) else ""
            content_sha256 = _text(raw.get("content_sha256"), "content_sha256").casefold()
            if _content_sha256(title, body) != content_sha256:
                raise ValueError("content_sha256 does not match canonical title/body")
            grouped[issue_key].append(
                {
                    "issue_key": issue_key,
                    "as_of": as_of,
                    "title": title,
                    "body_text": body,
                    "content_sha256": content_sha256,
                    "source_url": _text(raw.get("source_url"), "source_url"),
                    "evidence_ref": _text(raw.get("evidence_ref"), "evidence_ref"),
                    "role": str(raw.get("role") or "unknown"),
                }
            )
        except (TypeError, ValueError) as exc:
            invalid.append(
                {
                    "record_id": str(raw.get("issue_key") or index),
                    "code": "invalid_historical_snapshot",
                    "detail": str(exc),
                }
            )

    candidates: list[dict[str, Any]] = []
    exclusions = list(invalid)
    multi_hash_issue_keys = 0
    for issue_key, versions in sorted(grouped.items()):
        versions.sort(key=lambda row: (row["as_of"], row["content_sha256"]))
        selected = versions[0]
        if len({row["content_sha256"] for row in versions}) > 1:
            multi_hash_issue_keys += 1
        for excluded in versions[1:]:
            exclusions.append(
                {
                    "record_id": issue_key,
                    "code": "duplicate_issue_snapshot_not_selected",
                    "detail": "v0.1 cache retains the earliest verified snapshot per issue_key",
                    "selected_as_of": selected["as_of"].isoformat(),
                    "excluded_as_of": excluded["as_of"].isoformat(),
                    "selected_content_sha256": selected["content_sha256"],
                    "excluded_content_sha256": excluded["content_sha256"],
                }
            )
        available_until = selected["as_of"] + timedelta(seconds=1)
        candidates.append(
            {
                "schema_version": CANDIDATE_SCHEMA_VERSION,
                "issue_key": issue_key,
                "available_from": selected["as_of"].isoformat(),
                "available_until": available_until.isoformat(),
                "content_sha256": selected["content_sha256"],
                "title": selected["title"],
                "body_text": selected["body_text"],
                "source_url": selected["source_url"],
                "evidence_ref": selected["evidence_ref"],
                "snapshot_role": selected["role"],
                "availability_policy": "exact_verified_snapshot_second",
            }
        )
    stats = {
        "input_snapshots": sum(len(rows) for rows in grouped.values()) + len(invalid),
        "valid_snapshots": sum(len(rows) for rows in grouped.values()),
        "invalid_snapshots": len(invalid),
        "selected_candidates": len(candidates),
        "excluded_duplicate_snapshots": sum(max(0, len(rows) - 1) for rows in grouped.values()),
        "multi_hash_issue_keys": multi_hash_issue_keys,
    }
    return candidates, exclusions, stats


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with Path(path).open("wb") as handle:
        for row in rows:
            handle.write(_canonical_json(row) + b"\n")


def _encode_semantic(
    candidates: Sequence[Mapping[str, Any]],
    *,
    encoder: FrozenTransformerEncoder,
    max_segments: int,
    segment_batch_size: int,
) -> np.ndarray:
    segmented = [
        segment_issue_text(
            IssueText(index, str(row["title"]), str(row["body_text"])),
            max_segments=max_segments,
        )
        for index, row in enumerate(candidates)
    ]
    flat = [segment for item in segmented for segment in item.segments]
    encoded_parts = [
        encoder.encode(flat[start : start + segment_batch_size])
        for start in range(0, len(flat), segment_batch_size)
    ]
    encoded = (
        np.concatenate(encoded_parts)
        if encoded_parts
        else np.empty((0, encoder.dimension), dtype=np.float32)
    )
    semantic = np.zeros((len(candidates), encoder.dimension), dtype=np.float32)
    cursor = 0
    for row_index, item in enumerate(segmented):
        part = encoded[cursor : cursor + len(item.segments)].mean(axis=0)
        norm = np.linalg.norm(part)
        semantic[row_index] = part / norm if norm else part
        cursor += len(item.segments)
    return semantic


def encode_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    model_dir: Path,
    mapping_dir: Path,
    device: str,
    max_segments: int,
    segment_batch_size: int,
) -> tuple[np.ndarray, dict[str, Any]]:
    model_dir = Path(model_dir)
    manifest = json.loads((model_dir / "model_manifest.json").read_text(encoding="utf-8"))
    state = torch.load(
        model_dir / "weights.pt", map_location="cpu", weights_only=True, mmap=True
    )
    encoder_name, revision = str(manifest["encoder_version"]).rsplit("@", 1)
    encoder = FrozenTransformerEncoder(
        encoder_name, revision=revision, device=device, allow_download=False
    )
    semantic = _encode_semantic(
        candidates,
        encoder=encoder,
        max_segments=max_segments,
        segment_batch_size=segment_batch_size,
    )

    repositories = pq.read_table(Path(mapping_dir) / "repository_mapping.parquet")
    repository_ids = {
        str(key).casefold(): int(value)
        for key, value in zip(
            repositories["repository_key"].to_pylist(),
            repositories["repository_id"].to_pylist(),
            strict=True,
        )
    }
    with np.load(model_dir / "embeddings.npz") as bundle:
        repository_vectors = np.asarray(bundle["repository_embeddings"], dtype=np.float32)
    dim = int(manifest["config"]["embedding_dim"])
    repository_keys = [str(row["issue_key"]).rsplit("#", 1)[0] for row in candidates]
    repository_input = np.stack(
        [
            repository_vectors[repository_ids[key]]
            if key in repository_ids
            else np.zeros(dim, dtype=np.float32)
            for key in repository_keys
        ]
    )
    inputs = np.concatenate(
        [
            np.zeros((len(candidates), dim), dtype=np.float32),
            semantic,
            np.zeros((len(candidates), dim), dtype=np.float32),
            repository_input,
        ],
        axis=1,
    )
    with torch.inference_mode():
        value = torch.from_numpy(inputs)
        value = torch.relu(
            torch.nn.functional.linear(
                value, state["issue_fusion.0.weight"], state["issue_fusion.0.bias"]
            )
        )
        value = torch.nn.functional.linear(
            value, state["issue_fusion.2.weight"], state["issue_fusion.2.bias"]
        ).numpy().astype(np.float32)
    metadata = {
        "model_version": manifest["model_version"],
        "encoder_version": manifest["encoder_version"],
        "embedding_dim": dim,
        "known_repository_count": sum(key in repository_ids for key in repository_keys),
        "out_of_sample_policy": "zero collaborative/structural; frozen text plus known repository path",
    }
    return value, metadata


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory must be absent or empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates, exclusions, stats = prepare_candidate_rows(_records(Path(args.snapshots)))
    if not candidates:
        raise RuntimeError("no valid historical candidates")

    candidates_path = output_dir / "historical_issue_candidates.jsonl"
    exclusions_path = output_dir / "candidate_exclusions.jsonl"
    cache_path = output_dir / "historical_issue_embeddings.npz"
    _write_jsonl(candidates_path, candidates)
    _write_jsonl(exclusions_path, exclusions)
    vectors, encoding = encode_candidates(
        candidates,
        model_dir=Path(args.model_dir),
        mapping_dir=Path(args.mapping_dir),
        device=args.device,
        max_segments=args.max_segments,
        segment_batch_size=args.segment_batch_size,
    )
    temporary = output_dir / f".tmp-{uuid.uuid4().hex}.npz"
    try:
        with temporary.open("wb") as handle:
            np.savez_compressed(
                handle,
                issue_keys=np.asarray([row["issue_key"] for row in candidates]),
                issue_embeddings=vectors,
                content_sha256=np.asarray([row["content_sha256"] for row in candidates]),
            )
        with np.load(temporary) as check:
            if check["issue_embeddings"].shape != (
                len(candidates),
                int(encoding["embedding_dim"]),
            ):
                raise RuntimeError("historical issue cache verification failed")
        os.replace(temporary, cache_path)
    finally:
        temporary.unlink(missing_ok=True)

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "source_snapshots": str(Path(args.snapshots)),
        "source_snapshots_sha256": _sha256(Path(args.snapshots)),
        "selection_policy": "earliest verified snapshot per issue_key; exact one-second availability",
        "statistics": stats,
        "encoding": encoding,
        "parameters": {
            "max_segments": args.max_segments,
            "segment_batch_size": args.segment_batch_size,
            "device": args.device,
        },
        "artifacts": {
            "historical_issue_candidates.jsonl": _sha256(candidates_path),
            "candidate_exclusions.jsonl": _sha256(exclusions_path),
            "historical_issue_embeddings.npz": _sha256(cache_path),
        },
    }
    manifest_path = output_dir / "issue_input_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    success_path = output_dir / "_SUCCESS"
    success_path.write_text(_sha256(manifest_path) + "\n", encoding="ascii")
    return {
        "candidates": len(candidates),
        "exclusions": len(exclusions),
        "cache_sha256": manifest["artifacts"]["historical_issue_embeddings.npz"],
        "manifest_sha256": _sha256(manifest_path),
        "output_dir": str(output_dir),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshots", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--mapping-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--max-segments", type=int, default=12)
    parser.add_argument("--segment-batch-size", type=int, default=32)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_segments <= 0 or args.segment_batch_size <= 0:
        raise SystemExit("segment limits must be positive")
    print(json.dumps(run(args), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
