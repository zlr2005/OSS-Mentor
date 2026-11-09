from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from oss_mentor.gated_scoring import content_hash
from oss_mentor.inventory_handoff import (
    HandoffValidationError,
    import_recommendable_handoff,
)
from oss_mentor.task_readiness import task_review_reasons


ROOT = Path(__file__).resolve().parents[1]


class InventoryHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.handoff = self.root / "handoff"
        self.handoff.mkdir()
        self.database = self.root / "isolated.sqlite3"
        self.row = {
            "task_candidate_id": 7,
            "source_task_candidate_ids": {"oss_mentor": 7, "oss_mentor_web": 99},
            "repository": "example/demo",
            "issue_number": 42,
            "github_issue_id": 10042,
            "issue_key": "example/demo#42",
            "title": "Fix parser crash on empty input",
            "body_text": (
                "Steps to reproduce: parse an empty file. "
                "Expected behavior: return an empty list without raising."
            ),
            "url": "https://github.com/example/demo/issues/42",
            "availability": "available",
            "verified_at": "2026-10-01T00:00:00+00:00",
            "verification_expires_at": "2026-10-02T00:00:00+00:00",
            "language": "Python",
            "task_types": ["bug_fix", "testing"],
            "skill_requirements": [
                {
                    "skill_name": "Python",
                    "minimum_level": 1,
                    "importance": 1.0,
                    "requirement_source": "repository_primary_language",
                    "feature_version": "task-features-v0.5",
                }
            ],
            "eligibility": "eligible",
            "task_feature_version": "task-features-v0.5",
            "recommendable_under_assumption": True,
            "content_hash": "",
            "source_content_hash": "",
            "redacted_fields": [],
        }
        self.row["content_hash"] = content_hash(self.row)
        self.row["source_content_hash"] = self.row["content_hash"]
        self._write_handoff([self.row])

    def _write_handoff(self, rows):
        report = {
            "inventory_availability_assumed": True,
            "online_reverified_this_export": False,
            "recommendable_under_assumption": len(rows),
            "required_field_missing_counts": {},
        }
        validation = {"status": "passed", "row_count": len(rows)}
        payloads = {
            "report.json": report,
            "validation.json": validation,
            "recommendable_under_assumption.json": rows,
        }
        files = []
        for name, payload in payloads.items():
            path = self.handoff / name
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            files.append(
                {
                    "path": name,
                    "size_bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        manifest = {
            "dataset_version": "test-handoff",
            "inventory_availability_assumed": True,
            "credential_scan": "passed",
            "files": files,
        }
        manifest_path = self.handoff / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        (self.handoff / "manifest.sha256").write_text(
            hashlib.sha256(manifest_path.read_bytes()).hexdigest() + "  manifest.json\n",
            encoding="ascii",
        )

    def test_requires_explicit_inventory_assumption_and_new_database(self):
        with self.assertRaisesRegex(ValueError, "assume-inventory-available"):
            import_recommendable_handoff(
                self.handoff,
                self.database,
                assume_inventory_available=False,
            )
        self.assertFalse(self.database.exists())
        self.database.touch()
        with self.assertRaises(FileExistsError):
            import_recommendable_handoff(
                self.handoff,
                self.database,
                assume_inventory_available=True,
            )

    def test_import_preserves_mapping_requirements_and_assumption_boundary(self):
        result = import_recommendable_handoff(
            self.handoff,
            self.database,
            assume_inventory_available=True,
        )
        self.assertEqual(1, result["source_rows"])
        self.assertEqual(1, result["source_web_id_mappings"])
        self.assertEqual(1, result["handoff_matchable_under_assumption"])
        self.assertEqual(0, result["handoff_matchable_without_assumption"])
        self.assertEqual({}, result["handoff_review_reasons"])
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            candidate = connection.execute(
                "SELECT * FROM task_candidate WHERE github_issue_id = 10042"
            ).fetchone()
            requirement = connection.execute(
                "SELECT * FROM task_skill_requirement WHERE task_candidate_id = ?",
                (candidate["task_candidate_id"],),
            ).fetchone()
            mapping = json.loads(candidate["inventory_record_json"])[
                "source_task_candidate_ids"
            ]
        self.assertEqual({"oss_mentor": 7, "oss_mentor_web": 99}, mapping)
        self.assertEqual("available", candidate["candidate_availability"])
        self.assertEqual("availability_assumed_offline_handoff", candidate["inventory_review_status"])
        self.assertIn("inventory_availability_assumed", candidate["warnings_json"])
        self.assertEqual("Python", requirement["skill_name"])
        record = {
            **dict(candidate),
            "task_types": json.loads(candidate["task_types_json"]),
            "requirements": [dict(requirement)],
            "feature_evidence": json.loads(candidate["feature_evidence_json"]),
        }
        self.assertEqual((), task_review_reasons(record))

    def test_repeat_import_is_idempotent_when_explicitly_allowed(self):
        first = import_recommendable_handoff(
            self.handoff,
            self.database,
            assume_inventory_available=True,
        )
        second = import_recommendable_handoff(
            self.handoff,
            self.database,
            assume_inventory_available=True,
            allow_existing=True,
        )
        self.assertEqual({"inserted": 1}, first["import_counts"])
        self.assertEqual({"updated": 1}, second["import_counts"])
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(
                1,
                connection.execute("SELECT COUNT(*) FROM task_candidate").fetchone()[0],
            )
            self.assertEqual(
                1,
                connection.execute("SELECT COUNT(*) FROM task_skill_requirement").fetchone()[0],
            )

    def test_older_handoff_cannot_overwrite_newer_candidate_evidence(self):
        current = dict(self.row)
        current["title"] = "Newer observation of parser crash"
        current["verified_at"] = "2026-11-01T00:00:00+00:00"
        current["verification_expires_at"] = "2026-11-02T00:00:00+00:00"
        current["content_hash"] = content_hash(current)
        current["source_content_hash"] = current["content_hash"]
        self._write_handoff([current])
        import_recommendable_handoff(
            self.handoff,
            self.database,
            assume_inventory_available=True,
        )

        older = dict(self.row)
        older["title"] = "Older observation of parser crash"
        older["content_hash"] = content_hash(older)
        older["source_content_hash"] = older["content_hash"]
        self._write_handoff([older])
        result = import_recommendable_handoff(
            self.handoff,
            self.database,
            assume_inventory_available=True,
            allow_existing=True,
        )
        self.assertEqual({"skipped_existing_newer": 1}, result["import_counts"])
        with closing(sqlite3.connect(self.database)) as connection:
            title = connection.execute(
                "SELECT title FROM task_candidate WHERE github_issue_id = 10042"
            ).fetchone()[0]
        self.assertEqual("Newer observation of parser crash", title)

    def test_content_hash_mismatch_is_rejected(self):
        self.row["body_text"] += " Mutated after export."
        self._write_handoff([self.row])
        with self.assertRaisesRegex(HandoffValidationError, "content hash mismatch"):
            import_recommendable_handoff(
                self.handoff,
                self.database,
                assume_inventory_available=True,
            )
        self.assertFalse(self.database.exists())

    def test_manifest_tampering_is_rejected(self):
        report = self.handoff / "report.json"
        report.write_text('{"tampered": true}', encoding="utf-8")
        with self.assertRaisesRegex(
            HandoffValidationError,
            r"manifest (?:size|file checksum) mismatch",
        ):
            import_recommendable_handoff(
                self.handoff,
                self.database,
                assume_inventory_available=True,
            )


if __name__ == "__main__":
    unittest.main()
