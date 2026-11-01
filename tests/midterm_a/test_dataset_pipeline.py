import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/midterm_a/dataset_pipeline.py"
FIXTURE = ROOT / "fixtures/midterm/a/snapshot_v0.1"


class DatasetPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / "cli.sqlite3"

    def run_command(self, *args):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--database", str(self.database), *map(str, args)],
            cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
        )

    def test_import_status_and_export_commands(self):
        imported = self.run_command("import", FIXTURE, "--chunk-size", "2")
        self.assertEqual(imported.returncode, 0, imported.stdout + imported.stderr)
        first = json.loads(imported.stdout)
        status = self.run_command("status", "synthetic-a-v0.1")
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
        self.assertEqual(json.loads(status.stdout), first)
        exported = self.run_command("export", "synthetic-a-v0.1", self.root / "export")
        self.assertEqual(exported.returncode, 0, exported.stdout + exported.stderr)
        self.assertTrue(json.loads(exported.stdout)["fixture_only"])

    def test_status_of_missing_database_does_not_create_it(self):
        result = self.run_command("status", "absent")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.database.exists())

    def test_invalid_chunk_size_does_not_create_database(self):
        result = self.run_command("import", FIXTURE, "--chunk-size", "0")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.database.exists())

    def test_existing_live_database_path_is_refused_before_opening(self):
        self.database = ROOT / "data/oss_mentor.sqlite3"
        result = self.run_command("import", FIXTURE)
        self.assertEqual(result.returncode, 2)
        self.assertIn("independent", result.stderr)


if __name__ == "__main__":
    unittest.main()
