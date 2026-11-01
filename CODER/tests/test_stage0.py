from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import tempfile
import unittest

from coder_issue.config import load_settings
from coder_issue.manifest import build_manifest, write_manifest
from coder_issue.randomness import set_global_seed
from coder_issue.runtime import build_run_metadata


CODER_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = CODER_ROOT / "configs" / "base.toml"


class StageZeroTests(unittest.TestCase):
    def test_package_and_config_load(self) -> None:
        settings = load_settings(CONFIG_PATH)
        self.assertEqual("stage0-base", settings.config_name)
        self.assertEqual(8 * 1024 * 1024, settings.manifest.chunk_size_bytes)
        self.assertEqual(CODER_ROOT.parent, settings.paths.repository_root)
        self.assertEqual(2, len(settings.paths.raw_data_files))

    def test_seed_is_repeatable(self) -> None:
        set_global_seed(42)
        first = [random.random() for _ in range(3)]
        set_global_seed(42)
        self.assertEqual(first, [random.random() for _ in range(3)])

    def test_manifest_hashes_in_chunks_and_writes_json(self) -> None:
        settings = load_settings(CONFIG_PATH)
        with tempfile.TemporaryDirectory(dir=CODER_ROOT) as directory:
            sample = Path(directory) / "sample.bin"
            sample.write_bytes(b"abcdef" * 5)
            test_settings = settings.__class__(
                schema_version=settings.schema_version,
                config_name=settings.config_name,
                seed=settings.seed,
                dataset_version=settings.dataset_version,
                paths=settings.paths,
                logging=settings.logging,
                manifest=settings.manifest.__class__(
                    output=Path(directory) / "manifest.json", chunk_size_bytes=4
                ),
            )
            manifest = build_manifest(test_settings, [sample])
            self.assertEqual(hashlib.sha256(sample.read_bytes()).hexdigest(), manifest["files"][0]["sha256"])
            self.assertEqual(sample.stat().st_size, manifest["files"][0]["size_bytes"])
            write_manifest(manifest, test_settings.manifest.output)
            loaded = json.loads(test_settings.manifest.output.read_text(encoding="utf-8"))
            self.assertEqual(4, loaded["chunk_size_bytes"])

    def test_run_metadata_convention(self) -> None:
        settings = load_settings(CONFIG_PATH)
        metadata = build_run_metadata(
            settings, datetime(2026, 10, 17, 0, 0, tzinfo=timezone.utc)
        )
        self.assertTrue(metadata.run_id.startswith("20261017T000000Z-"))
        self.assertTrue(metadata.run_id.endswith("-stage0-base"))
        self.assertEqual(settings.dataset_version, metadata.dataset_version)


if __name__ == "__main__":
    unittest.main()
