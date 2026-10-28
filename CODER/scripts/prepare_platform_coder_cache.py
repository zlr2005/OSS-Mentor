"""Prepare out-of-sample platform Issue vectors for CODER online scoring."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import uuid

import numpy as np
import pyarrow.parquet as pq
import torch

from coder_issue.models.text_encoder import FrozenTransformerEncoder, IssueText, segment_issue_text


def prepare(args: argparse.Namespace) -> dict[str, object]:
    sys.path.insert(0, str(args.platform_src.resolve()))
    from oss_mentor.sqlite_store import SQLiteCandidateStore

    manifest = json.loads((args.model_dir / "model_manifest.json").read_text(encoding="utf-8"))
    state = torch.load(args.model_dir / "weights.pt", map_location="cpu", weights_only=True, mmap=True)
    store = SQLiteCandidateStore(args.database, args.migration)
    tasks = store.matchable_candidates()
    encoder_name, revision = str(manifest["encoder_version"]).rsplit("@", 1)
    encoder = FrozenTransformerEncoder(
        encoder_name, revision=revision, device=args.device, allow_download=False
    )
    segmented = [
        segment_issue_text(
            IssueText(i, str(task.get("title") or ""), str(task.get("body_text") or "")),
            max_segments=args.max_segments,
        )
        for i, task in enumerate(tasks)
    ]
    flat = [text for item in segmented for text in item.segments]
    encoded = np.concatenate([
        encoder.encode(flat[i:i + args.segment_batch_size])
        for i in range(0, len(flat), args.segment_batch_size)
    ])
    semantic = np.zeros((len(tasks), encoder.dimension), dtype=np.float32)
    cursor = 0
    for row, item in enumerate(segmented):
        part = encoded[cursor:cursor + len(item.segments)].mean(axis=0)
        norm = np.linalg.norm(part)
        semantic[row] = part / norm if norm else part
        cursor += len(item.segments)

    repositories = pq.read_table(args.mapping_dir / "repository_mapping.parquet")
    repository_ids = {
        str(key).casefold(): int(value)
        for key, value in zip(repositories["repository_key"].to_pylist(), repositories["repository_id"].to_pylist())
    }
    with np.load(args.model_dir / "embeddings.npz") as bundle:
        repository_vectors = np.asarray(bundle["repository_embeddings"], dtype=np.float32)
    dim = int(manifest["config"]["embedding_dim"])
    inputs = np.concatenate([
        np.zeros((len(tasks), dim), dtype=np.float32),
        semantic,
        np.zeros((len(tasks), dim), dtype=np.float32),
        np.stack([
            repository_vectors[repository_ids[key]] if (key := str(task["repository"]).casefold()) in repository_ids
            else np.zeros(dim, dtype=np.float32)
            for task in tasks
        ]),
    ], axis=1)
    with torch.inference_mode():
        value = torch.from_numpy(inputs)
        value = torch.relu(torch.nn.functional.linear(
            value, state["issue_fusion.0.weight"], state["issue_fusion.0.bias"]
        ))
        value = torch.nn.functional.linear(
            value, state["issue_fusion.2.weight"], state["issue_fusion.2.bias"]
        ).numpy().astype(np.float32)
    keys = np.asarray([
        f"{str(task['repository']).casefold()}#{int(task['issue_number'])}" for task in tasks
    ])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.parent / f".tmp-{uuid.uuid4().hex}.npz"
    try:
        with temporary.open("wb") as handle:
            np.savez_compressed(handle, issue_keys=keys, issue_embeddings=value)
        with np.load(temporary) as check:
            if check["issue_embeddings"].shape != (len(tasks), dim):
                raise RuntimeError("candidate cache verification failed")
        os.replace(temporary, args.output)
    finally:
        temporary.unlink(missing_ok=True)
    result = {
        "candidate_count": len(tasks),
        "known_repository_count": sum(str(t["repository"]).casefold() in repository_ids for t in tasks),
        "model_version": manifest["model_version"],
        "encoder_version": manifest["encoder_version"],
        "output": str(args.output),
        "out_of_sample_policy": "zero collaborative/structural; text plus known repository path",
    }
    args.output.with_suffix(".manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--mapping-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--migration", type=Path, default=root / "db/sqlite/001_mvp.sql")
    parser.add_argument("--platform-src", type=Path, default=root / "src")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--max-segments", type=int, default=12)
    parser.add_argument("--segment-batch-size", type=int, default=32)
    args = parser.parse_args()
    print(json.dumps(prepare(args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
