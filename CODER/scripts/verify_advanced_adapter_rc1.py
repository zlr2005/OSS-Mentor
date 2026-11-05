"""Fail-closed verification for the Advanced Adapter RC1 runtime bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from coder_issue.models.profile import ProfileResidualAdapter


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_file(path: Path, expected: Mapping[str, Any]) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"required RC1 file is missing: {path}")
    size = path.stat().st_size
    if size != int(expected["bytes"]):
        raise ValueError(
            f"RC1 size mismatch for {path}: expected {expected['bytes']}, got {size}"
        )
    digest = _sha256(path)
    if digest != str(expected["sha256"]):
        raise ValueError(
            f"RC1 SHA-256 mismatch for {path}: expected {expected['sha256']}, got {digest}"
        )
    return {"path": str(path), "bytes": size, "sha256": digest}


def verify(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).absolute()
    adapter_dir = (repo_root / args.adapter_dir).absolute()
    release_path = adapter_dir / "release_manifest.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    if release.get("schema_version") != "advanced-adapter-rc1-release-v0.1":
        raise ValueError("unsupported RC1 release manifest")

    checked: dict[str, Any] = {"adapter": {}, "coder": {}, "runtime": {}}
    for name, expected in release["adapter_files"].items():
        checked["adapter"][name] = _check_file(adapter_dir / name, expected)

    coder = release["required_external_coder_bundle"]
    model_dir = (
        Path(args.model_dir).absolute()
        if args.model_dir
        else (repo_root / coder["default_model_dir"]).absolute()
    )
    for name, expected in coder["files"].items():
        checked["coder"][name] = _check_file(model_dir / name, expected)

    for name, expected in release["required_external_runtime_inputs"].items():
        path = (repo_root / expected["default_path"]).absolute()
        checked["runtime"][name] = _check_file(path, expected)

    adapter = ProfileResidualAdapter.load_bundle(
        adapter_dir, expected_base_model_version=str(coder["version"])
    )
    if len(adapter.config.feature_names) != 23 or adapter.config.embedding_dim != 32:
        raise ValueError("RC1 Adapter dimensions are incompatible")
    return {
        "status": "passed",
        "release_id": release["release_id"],
        "adapter_version": release["adapter_version"],
        "approved_mode": release["approved_mode"],
        "serve_approved": release["serve_approved"],
        "checked": checked,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--adapter-dir", default="CODER/artifacts/advanced_adapter_rc1")
    parser.add_argument("--model-dir")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    result = verify(build_parser().parse_args(argv))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
