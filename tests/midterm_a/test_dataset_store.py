import hashlib
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from oss_mentor.datasets.snapshot import load_snapshot, validate_snapshot
from oss_mentor.sqlite_store import SQLiteCandidateStore
from oss_mentor.storage.datasets import DatasetStore


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "db/sqlite"
FIXTURE = ROOT / "fixtures/midterm/a/snapshot_v0.1"
VERSION = "synthetic-a-v0.1"


class DatasetStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.input = self.root / "input"
        shutil.copytree(FIXTURE, self.input)
        self.factory = SQLiteCandidateStore(self.root / "test.sqlite3", MIGRATIONS / "001_mvp.sql")
        self.factory.initialize()
        self.connection = self.factory.connect()
        self.addCleanup(self.connection.close)
        self.store = DatasetStore(self.connection)

    def change_records(self, modify):
        path = self.input / "records.json"
        records = json.loads(path.read_text(encoding="utf-8"))
        modify(records)
        payload = json.dumps(records, ensure_ascii=False).encode("utf-8")
        path.write_bytes(payload)
        manifest_path = self.input / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["entity_counts"] = {kind: len(rows) for kind, rows in records.items()}
        for item in manifest["files"]:
            if item["path"] == "records.json":
                item.update(sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload))
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def test_repeating_import_is_idempotent(self):
        first = self.store.import_snapshot(self.input, chunk_size=2)
        second = self.store.import_snapshot(self.input, chunk_size=5)
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "completed")
        self.assertEqual(first["checkpoint"], first["total_items"])
        self.assertEqual(first["entity_counts"]["contributions"], 2)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM midterm_dataset_import").fetchone()[0], 1)

    def test_exact_duplicate_edges_are_removed(self):
        self.change_records(lambda r: r["contributions"].append(dict(r["contributions"][0])))
        result = self.store.import_snapshot(self.input)
        self.assertEqual(result["entity_counts"]["contributions"], 2)
        self.assertEqual(result["duplicates_removed"]["contributions"], 1)
        self.store.export_snapshot(VERSION, self.root / "export")
        manifest, records, _ = load_snapshot(self.root / "export")
        self.assertEqual(manifest["entity_counts"]["contributions"], 2)
        self.assertEqual(len(records["contributions"]), 2)

    def test_conflicting_edge_fails_before_registering_batch(self):
        def conflict(records):
            row = dict(records["contributions"][0])
            row["evidence_location"] = "https://example.invalid/different-evidence"
            records["contributions"].append(row)
        self.change_records(conflict)
        with self.assertRaisesRegex(ValueError, "Conflicting records"):
            self.store.import_snapshot(self.input)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM midterm_dataset_import").fetchone()[0], 0)

    def test_failure_rolls_back_chunk_and_resumes_after_reopening(self):
        # A DB failure on the second contribution leaves earlier chunks intact.
        self.connection.executescript(
            "CREATE TRIGGER fail_contribution BEFORE INSERT ON midterm_dataset_contribution "
            "WHEN NEW.developer_key = 'dev-unknown' BEGIN SELECT RAISE(ABORT, 'test failure'); END;"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.import_snapshot(self.input, chunk_size=2)
        failed = self.store.status(VERSION)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["checkpoint"], 12)
        self.assertEqual(failed["entity_counts"]["contributions"], 1)
        self.assertEqual(failed["error_type"], "IntegrityError")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.store.export_snapshot(VERSION, self.root / "incomplete")
        self.connection.execute("DROP TRIGGER fail_contribution")
        self.connection.close()
        connection = self.factory.connect()
        try:
            resumed = DatasetStore(connection).import_snapshot(self.input, chunk_size=3)
            self.assertEqual(resumed["status"], "completed")
            self.assertEqual(resumed["batch_id"], failed["batch_id"])
            self.assertEqual(resumed["entity_counts"]["contributions"], 2)
            self.assertIsNone(resumed["error_type"])
        finally:
            connection.close()

    def test_entire_failed_chunk_and_checkpoint_are_rolled_back(self):
        self.connection.executescript(
            "CREATE TRIGGER fail_contribution BEFORE INSERT ON midterm_dataset_contribution "
            "BEGIN SELECT RAISE(ABORT, 'test failure'); END;"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.import_snapshot(self.input, chunk_size=100)
        status = self.store.status(VERSION)
        self.assertEqual(status["checkpoint"], 0)
        self.assertTrue(all(count == 0 for count in status["entity_counts"].values()))
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM midterm_dataset_asset").fetchone()[0], 0)

    def test_changed_input_cannot_reuse_frozen_version(self):
        self.store.import_snapshot(self.input)
        self.change_records(lambda r: r["repositories"][0].update(primary_language="JavaScript"))
        with self.assertRaisesRegex(ValueError, "different content"):
            self.store.import_snapshot(self.input)
        stored = self.connection.execute("SELECT payload_json FROM midterm_dataset_repository").fetchone()[0]
        self.assertEqual(json.loads(stored)["primary_language"], "Python")

    def test_export_is_portable_deterministic_and_uses_new_version(self):
        self.store.import_snapshot(self.input)
        self.input.rename(self.root / "source-no-longer-at-import-location")
        first = self.root / "export-1"
        second = self.root / "export-2"
        report = self.store.export_snapshot(VERSION, first)
        self.store.export_snapshot(VERSION, second)
        self.assertNotEqual(report["dataset_version"], VERSION)
        self.assertEqual(validate_snapshot(first)["status"], "fixture_verified")
        for path in first.rglob("*"):
            if path.is_file():
                self.assertEqual(path.read_bytes(), (second / path.relative_to(first)).read_bytes())
        _, records, _ = load_snapshot(first)
        self.assertIsNone(records["project_behaviors"][0]["event_at"])
        self.assertEqual(records["code_documents"][1]["availability"], "missing")
        self.assertEqual(records["developers"][1]["identity_status"], "unconfirmed")

    def test_existing_export_is_not_overwritten(self):
        self.store.import_snapshot(self.input)
        destination = self.root / "existing"
        destination.mkdir()
        marker = destination / "important.txt"
        marker.write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.store.export_snapshot(VERSION, destination)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_database_enforces_foreign_keys_and_preserves_other_tables(self):
        self.connection.execute(
            "INSERT INTO repository (full_name, html_url, first_collected_at, updated_at) "
            "VALUES ('existing/repo', 'https://example.invalid', '2026-01-01', '2026-01-01')"
        )
        self.connection.commit()
        self.store.import_snapshot(self.input)
        self.assertEqual(self.connection.execute("SELECT full_name FROM repository").fetchone()[0], "existing/repo")
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute("DELETE FROM midterm_dataset_developer WHERE developer_key = 'dev-a'")
        self.connection.rollback()
        self.assertEqual(self.connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_corrupt_database_asset_fails_without_publishing_export(self):
        self.store.import_snapshot(self.input)
        self.connection.execute(
            "UPDATE midterm_dataset_asset SET content = ? WHERE relative_path = 'content/parser.py.txt'", (b"bad",),
        )
        self.connection.commit()
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            self.store.export_snapshot(VERSION, self.root / "export")
        self.assertFalse((self.root / "export").exists())

    def test_fixture_rejected_before_any_rows_are_written(self):
        (self.input / "content/parser.py.txt").write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
            self.store.import_snapshot(self.input)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM midterm_dataset_snapshot").fetchone()[0], 0)


class DatasetMigrationTests(unittest.TestCase):
    def test_upgrade_old_database_and_repeat_initialization(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = root / "old-migrations"
            old.mkdir()
            for path in MIGRATIONS.glob("*.sql"):
                if path.name < "011":
                    shutil.copy2(path, old / path.name)
            database = root / "upgrade.sqlite3"
            SQLiteCandidateStore(database, old / "001_mvp.sql").initialize()
            current = SQLiteCandidateStore(database, MIGRATIONS / "001_mvp.sql")
            current.initialize()
            current.initialize()
            with current.connect() as connection:
                applied = connection.execute(
                    "SELECT COUNT(*) FROM schema_migration WHERE migration_name = '011_midterm_datasets.sql'"
                ).fetchone()[0]
                self.assertEqual(applied, 1)
                migration = (MIGRATIONS / "011_midterm_datasets.sql").read_text(encoding="utf-8")
                connection.executescript(migration)
                connection.executescript(migration)
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
