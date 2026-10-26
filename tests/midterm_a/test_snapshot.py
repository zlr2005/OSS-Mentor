import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from oss_mentor.datasets.snapshot import validate_snapshot


FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/midterm/a/snapshot_v0.1"


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "snapshot"
        shutil.copytree(FIXTURE, self.root)

    def change_records(self, modify):
        path = self.root / "records.json"
        records = json.loads(path.read_text(encoding="utf-8"))
        modify(records)
        payload = json.dumps(records).encode()
        path.write_bytes(payload)
        manifest_path = self.root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest["files"]:
            if item["path"] == "records.json":
                item.update(sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload))
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def test_complete_fixture_including_missing_code_is_readable(self):
        report = validate_snapshot(self.root)
        self.assertEqual(report["status"], "fixture_verified")
        self.assertEqual(report["entity_counts"]["contributions"], 2)

    def test_tampered_content_fails_checksum(self):
        (self.root / "content/parser.py.txt").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
            validate_snapshot(self.root)

    def test_broken_contribution_reference_is_rejected(self):
        self.change_records(lambda r: r["contributions"][0].update(file_version_key="absent"))
        with self.assertRaisesRegex(ValueError, "Broken reference"):
            validate_snapshot(self.root)

    def test_future_resolution_link_cannot_be_claimed_as_early_evidence(self):
        self.change_records(lambda r: r["issue_file_links"][1].update(available_at="2026-09-02T00:00:00Z"))
        with self.assertRaisesRegex(ValueError, "before resolution"):
            validate_snapshot(self.root)

    def test_resolution_is_not_a_prediction_feature(self):
        self.change_records(lambda r: r["issue_file_links"][1].update(feature_role="pre_resolution_hint"))
        with self.assertRaisesRegex(ValueError, "outcome_only"):
            validate_snapshot(self.root)

    def test_observation_time_does_not_replace_unknown_star_time(self):
        self.change_records(lambda r: r["project_behaviors"][0].update(event_at="2026-10-14T00:00:00Z"))
        with self.assertRaisesRegex(ValueError, "Unknown behavior time"):
            validate_snapshot(self.root)

    def test_contribution_must_match_its_code_commit(self):
        self.change_records(lambda r: r["contributions"][0].update(commit_sha="3" * 40))
        with self.assertRaisesRegex(ValueError, "commit_sha"):
            validate_snapshot(self.root)

    def test_closed_issue_cannot_enter_current_inventory(self):
        self.change_records(lambda r: r["issues"][0].update(current_availability="eligible"))
        with self.assertRaisesRegex(ValueError, "Closed issue"):
            validate_snapshot(self.root)

    def test_manifest_cannot_reference_outside_snapshot(self):
        manifest_path = self.root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"][0]["path"] = "../outside.txt"
        (self.root.parent / "outside.txt").write_text("outside", encoding="utf-8")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "outside snapshot"):
            validate_snapshot(self.root)


if __name__ == "__main__":
    unittest.main()
