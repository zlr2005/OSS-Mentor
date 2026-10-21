from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/midterm_b/export_confirmed_profiles.py"

from oss_mentor.profiling.delivery import (
    build_confirmed_feature_snapshot,
    validate_confirmed_feature_snapshot,
)


class ProfileDeliveryTests(unittest.TestCase):
    def _run(self, output_root: Path, version: str = "fixture-v0.1"):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--output-root",
                str(output_root),
                "--snapshot-version",
                version,
                "--source-commit",
                "fixture-source-commit",
            ],
            cwd=output_root.parent,
            capture_output=True,
            check=True,
        )

    def test_cli_writes_confirmed_snapshot_manifest_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "midterm"
            result = self._run(output_root)
            summary = json.loads(result.stdout)
            profile_dir = output_root / "profiles/fixture-v0.1"
            feature_file = profile_dir / "enhanced_profile_features.json"
            manifest_file = profile_dir / "manifest.json"
            validation_file = output_root / "validation/b/fixture-v0.1.json"
            self.assertTrue(feature_file.is_file())
            self.assertTrue(manifest_file.is_file())
            self.assertTrue(validation_file.is_file())
            snapshots = json.loads(feature_file.read_text(encoding="utf-8"))
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            validation = json.loads(validation_file.read_text(encoding="utf-8"))
            self.assertEqual(len(snapshots), 6)
            self.assertEqual(manifest["profile_count"], 6)
            self.assertEqual(manifest["profile_state"], "confirmed")
            self.assertTrue(all(validation["checks"].values()))
            self.assertTrue(all(row["profile_state"] == "confirmed" for row in snapshots))
            self.assertEqual(
                summary["feature_sha256"], hashlib.sha256(feature_file.read_bytes()).hexdigest()
            )
            artifacts = {row["path"]: row["sha256"] for row in manifest["artifacts"]}
            self.assertEqual(
                artifacts["enhanced_profile_features.json"], summary["feature_sha256"]
            )
            self.assertEqual(
                artifacts["../../validation/b/fixture-v0.1.json"],
                summary["validation_sha256"],
            )

    def test_repeated_exports_have_identical_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            first_root = base / "first"
            second_root = base / "second"
            self._run(first_root)
            self._run(second_root)
            first_files = {
                path.relative_to(first_root): path.read_bytes()
                for path in first_root.rglob("*.json")
            }
            second_files = {
                path.relative_to(second_root): path.read_bytes()
                for path in second_root.rglob("*.json")
            }
            self.assertEqual(first_files, second_files)

    def test_existing_delivery_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "midterm"
            self._run(output_root)
            feature_file = (
                output_root / "profiles/fixture-v0.1/enhanced_profile_features.json"
            )
            original = feature_file.read_bytes()
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--output-root",
                    str(output_root),
                    "--snapshot-version",
                    "fixture-v0.1",
                    "--source-commit",
                    "different-commit",
                ],
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(feature_file.read_bytes(), original)

    def test_unknown_and_locked_masks_are_preserved(self) -> None:
        state = {
            "schema_version": "profile-feature-state-v0.1",
            "profile_key": "profile:fixture",
            "profile_version": "profile:v1",
            "feature_schema_version": "features:v1",
            "updated_at": "2026-10-21T00:00:00Z",
            "features": [
                {
                    "feature_name": "capability.code_change",
                    "observed": False,
                    "value": 0.0,
                    "locked": False,
                    "evidence_refs": [],
                },
                {
                    "feature_name": "current_interest.task.quality",
                    "observed": True,
                    "value": 1.0,
                    "locked": True,
                    "evidence_refs": ["assessment:focus:quality"],
                },
            ],
        }
        snapshot = build_confirmed_feature_snapshot(
            state, as_of="2026-10-21T00:00:00Z"
        )
        validate_confirmed_feature_snapshot(snapshot)
        self.assertEqual(snapshot["observed_mask"], [False, True])
        self.assertEqual(snapshot["locked_mask"], [False, True])
        self.assertEqual(snapshot["current_interests"], ["current_interest.task.quality"])


if __name__ == "__main__":
    unittest.main()
