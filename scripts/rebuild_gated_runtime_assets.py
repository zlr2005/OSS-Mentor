"""Rebuild a local gated runtime asset tree and prove semantic parity."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_manifest(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def rebuild(source: Path, output: Path) -> dict[str, object]:
    source = source.resolve()
    output = output.resolve()
    if output.exists():
        raise FileExistsError("Output must not already exist")
    expected = json.loads((source / "SHA256_manifest.json").read_text(encoding="utf-8"))
    if expected.get("source_head") != "9bf9208d237f343ad3d1d993ccbe9a0f31c4935b":
        raise ValueError("Portable source is not the approved PR #40 head")
    for name, digest in expected["files"].items():
        path = source / name
        if not path.is_file() or sha256(path) != digest:
            raise ValueError(f"Portable source checksum mismatch: {name}")

    shutil.copytree(source / "assets", output / "assets")
    shutil.copytree(
        source / "workspace/tmp/pr39-review/CODER/src", output / "coder_source"
    )
    (output / "bundle").mkdir(parents=True)
    shutil.copy2(source / "bundle/weights.npz", output / "bundle/weights.npz")

    mapping_path = output / "assets/runs/public-supplement-20261106-v2/mappings/repository_mapping.parquet"
    source_mapping = pq.read_table(mapping_path)
    rows = sorted(
        zip(
            source_mapping["repository_key"].to_pylist(),
            source_mapping["repository_id"].to_pylist(),
            strict=True,
        ),
        key=lambda row: str(row[0]).casefold(),
    )
    if len({str(key).casefold() for key, _ in rows}) != len(rows):
        raise ValueError("Repository mapping contains duplicate normalized keys")
    rebuilt_mapping = pa.table(
        {
            "repository_key": [str(key) for key, _ in rows],
            "repository_id": [int(value) for _, value in rows],
        }
    )
    pq.write_table(rebuilt_mapping, mapping_path, compression="zstd", version="2.6")
    roundtrip = pq.read_table(mapping_path)
    rebuilt_rows = list(
        zip(
            roundtrip["repository_key"].to_pylist(),
            roundtrip["repository_id"].to_pylist(),
            strict=True,
        )
    )
    if rebuilt_rows != rows:
        raise ValueError("Rebuilt repository mapping changed key/id semantics")

    manifest = json.loads(
        (source / "bundle/manifest.template.json").read_text(encoding="utf-8")
    )
    manifest["backbone_dependencies"] = {
        str((output / relative).resolve()): digest
        for relative, digest in manifest["backbone_dependencies"].items()
    }
    manifest["runtime_asset_rebuild"] = {
        "source_head": expected["source_head"],
        "repository_mapping_sha256": sha256(mapping_path),
        "repository_mapping_rows": len(rows),
    }
    (output / "bundle/manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    encoder_dir = output / (
        "assets/encoding-assets/hf_hub/"
        "models--sentence-transformers--all-MiniLM-L6-v2/snapshots/"
        "c21050a7ef692090620a6d037dd736908f9c7cf6"
    )
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    sys.path.insert(0, str(ROOT / "CODER/src"))
    from coder_issue.models.text_encoder import FrozenTransformerEncoder

    encoder = FrozenTransformerEncoder(
        str(encoder_dir),
        revision="c21050a7ef692090620a6d037dd736908f9c7cf6",
        allow_download=False,
        max_tokens=256,
    )
    probe = encoder.encode(["OSS Mentor offline MiniLM consistency probe"])
    if probe.shape != (1, 384) or not np.isfinite(probe).all():
        raise ValueError("Rebuilt MiniLM probe failed")

    report = {
        "status": "passed",
        "source_head": expected["source_head"],
        "source_files_verified": len(expected["files"]),
        "repository_mapping_rows": len(rows),
        "repository_mapping_semantically_identical": True,
        "repository_mapping_sha256": sha256(mapping_path),
        "minilm_revision": "c21050a7ef692090620a6d037dd736908f9c7cf6",
        "minilm_probe_shape": list(probe.shape),
        "minilm_probe_sha256": hashlib.sha256(probe.tobytes()).hexdigest(),
        "bundle_weights_sha256": sha256(output / "bundle/weights.npz"),
        "rebuilt_files": len(file_manifest(output)),
        "frozen_coder_source": "coder_source",
    }
    (output / "rebuild_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(rebuild(args.source, args.output), ensure_ascii=False, indent=2))
