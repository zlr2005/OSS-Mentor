from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/midterm_b/export_assessment_handoff.py"


class AssessmentHandoffTests(unittest.TestCase):
    def test_cli_exports_reproducible_examples_outside_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = subprocess.run(
                [sys.executable, str(SCRIPT)], cwd=directory,
                capture_output=True, check=True,
            )
            output = Path(directory) / "handoff.json"
            subprocess.run(
                [sys.executable, str(SCRIPT), "--output", str(output)],
                cwd=directory, capture_output=True, check=True,
            )
            self.assertEqual(first.stdout, output.read_bytes())
        bundle = json.loads(first.stdout)
        self.assertEqual("synthetic_fixture", bundle["data_status"])
        self.assertEqual("preview_only_not_confirmed", bundle["profile_state"])
        self.assertEqual(6, bundle["scenario_count"])
        for source in bundle["input_files"]:
            self.assertEqual(
                hashlib.sha256((ROOT / source["path"]).read_bytes()).hexdigest(),
                source["sha256"],
            )
        cases = {case["scenario_id"]: case for case in bundle["cases"]}
        for case in cases.values():
            features = case["feature_export"]
            self.assertFalse(case["preview"]["saved"])
            self.assertIsNone(features["developer_key"])
            self.assertEqual(23, len(features["feature_names"]))
            for field in ("values", "observed_mask", "evidence_refs"):
                self.assertEqual(23, len(features[field]))
        zero = cases["zero_history"]["feature_export"]
        unknown = zero["feature_names"].index("capability.debugging")
        unselected = zero["feature_names"].index("preference.language.javascript")
        self.assertFalse(zero["observed_mask"][unknown])
        self.assertTrue(zero["observed_mask"][unselected])
        self.assertEqual(0.0, zero["values"][unselected])
        self.assertIn(
            "capability.code_change",
            cases["evidence_conflict"]["summary"]["conflicting_features"],
        )
        locked = cases["manual_lock"]
        self.assertIn("capability.code_change", locked["summary"]["locked_features"])
        index = locked["feature_export"]["feature_names"].index("capability.code_change")
        self.assertEqual(0.25, locked["feature_export"]["values"][index])

    def test_existing_output_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "existing.json"
            target.write_bytes(b"existing artifact")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--output", str(target)],
                cwd=directory, capture_output=True,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertEqual(b"existing artifact", target.read_bytes())


if __name__ == "__main__":
    unittest.main()
