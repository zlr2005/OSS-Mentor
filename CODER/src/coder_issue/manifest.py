"""Generate a bounded-memory manifest for local raw data files."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import sys
import tempfile
from typing import Iterable

from .config import Settings, load_settings
from .errors import CoderIssueError, DataFileError
from .logging_utils import configure_logging
from .runtime import git_code_version

LOGGER = logging.getLogger(__name__)


def sha256_file(path: Path, chunk_size_bytes: int) -> str:
    """Hash ``path`` incrementally without loading it into memory."""

    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(chunk_size_bytes):
                digest.update(chunk)
    except OSError as exc:
        raise DataFileError(f"cannot read data file {path}: {exc}") from exc
    return digest.hexdigest()


def build_manifest(settings: Settings, files: Iterable[Path] | None = None) -> dict[str, object]:
    """Inspect file metadata and content hashes using configured chunking."""

    entries: list[dict[str, object]] = []
    for path in files or settings.paths.raw_data_files:
        resolved = path.resolve()
        if not resolved.is_file():
            raise DataFileError(f"required data file does not exist: {resolved}")
        try:
            relative_path = resolved.relative_to(settings.paths.repository_root).as_posix()
        except ValueError as exc:
            raise DataFileError(f"data file is outside repository root: {resolved}") from exc
        try:
            before = resolved.stat()
        except OSError as exc:
            raise DataFileError(f"cannot inspect data file {resolved}: {exc}") from exc
        LOGGER.info("hashing %s (%d bytes)", relative_path, before.st_size)
        digest = sha256_file(resolved, settings.manifest.chunk_size_bytes)
        try:
            after = resolved.stat()
        except OSError as exc:
            raise DataFileError(f"cannot re-inspect data file {resolved}: {exc}") from exc
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise DataFileError(f"data file changed while hashing: {resolved}")
        entries.append(
            {
                "name": resolved.name,
                "path": relative_path,
                "size_bytes": before.st_size,
                "sha256": digest,
            }
        )

    return {
        "schema_version": 1,
        "dataset_version": settings.dataset_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "code_version": git_code_version(settings.paths.repository_root),
        "hash_algorithm": "sha256",
        "chunk_size_bytes": settings.manifest.chunk_size_bytes,
        "files": entries,
    }


def write_manifest(manifest: dict[str, object], output: Path) -> None:
    """Atomically replace a manifest only after successful generation."""

    output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent, delete=False
        ) as stream:
            temporary_name = stream.name
            stream.write(serialized)
            stream.flush()
        Path(temporary_name).replace(output)
    except OSError as exc:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
        raise DataFileError(f"cannot write manifest {output}: {exc}") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    default_config = Path(__file__).resolve().parents[2] / "configs" / "base.toml"
    parser.add_argument("--config", type=Path, default=default_config)
    parser.add_argument("--output", type=Path, help="override the configured output path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = load_settings(args.config)
        configure_logging(settings.logging.level, settings.logging.format)
        manifest = build_manifest(settings)
        output = args.output.resolve() if args.output else settings.manifest.output
        write_manifest(manifest, output)
        LOGGER.info("manifest written to %s", output)
    except (CoderIssueError, ValueError) as exc:
        logging.getLogger(__name__).error("manifest generation failed: %s", exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
