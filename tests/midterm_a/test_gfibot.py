"""Paper ingestion must preserve source boundaries and reject damaged inputs."""

import gzip
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path

from oss_mentor.datasets.gfibot import bson_documents, import_resolved, normalize_resolution

try:
    from bson import BSON, ObjectId
except ImportError:
    BSON = None


def document(number=32):
    return {"owner": "OpenMined", "name": "PySyft", "number": number,
            "created_at": datetime(2017, 8, 9), "resolved_at": datetime(2017, 9, 2),
            "resolver": "Example", "resolved_in": 254, "resolver_commit_num": 0}


class PaperNormalizationTests(unittest.TestCase):
    def test_outcome_and_identity_boundary(self):
        value = normalize_resolution(document())
        self.assertEqual(value["issue_key"], "github:openmined/pysyft#32")
        self.assertEqual(value["resolver_key"], "gfibot:6837711:login:example")
        self.assertEqual(value["identity_status"], "source_login_only")
        self.assertEqual(value["feature_role"], "outcome_only")
        self.assertFalse(value["prediction_features_ready"])
        self.assertFalse(value["live_candidate_verified"])

    def test_unknown_resolution_not_fabricated(self):
        value = normalize_resolution({**document(), "resolved_in": "not-a-sha", "resolver": None})
        self.assertEqual(value["resolution_kind"], "unknown")
        self.assertIsNone(value["resolution_locator"])
        self.assertIsNone(value["resolver_key"])

    def test_invalid_labels_and_time_rejected(self):
        for change in ({"resolver_commit_num": -1}, {"number": True}, {"owner": "../other"},
                       {"created_at": datetime(2020, 1, 1)}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                normalize_resolution({**document(), **change})

    def test_truncated_bson_rejected(self):
        for raw in (b'\x05', b'\x08\x00\x00\x00ab', b'\x04\x00\x00\x00'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                list(bson_documents(io.BytesIO(raw)))


@unittest.skipIf(BSON is None, "PyMongo BSON reader is optional")
class PaperImportTests(unittest.TestCase):
    def ingest(self, root, docs, corrupt=False):
        raw = b''.join(BSON.encode(d) for d in docs)
        packed = bytearray(gzip.compress(raw))
        if corrupt:
            packed[-8] ^= 1
        member = root / "member.gz"
        member.write_bytes(packed)
        evidence = {"member_sha256": hashlib.sha256(packed).hexdigest(), "zip_crc32_verified": True}
        return import_resolved(member, root / "paper.sqlite3", evidence)

    def test_success_and_exact_duplicates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            row = {**document(), "_id": ObjectId()}
            report = self.ingest(root, [row, row])
            self.assertEqual(report["historical_issues"], 1)
            self.assertEqual(report["exact_duplicates"], 1)
            self.assertFalse(report["frozen"])
            self.assertEqual(report["current_open_candidates"], 0)
            with closing(sqlite3.connect(root / "paper.sqlite3")) as db:
                self.assertEqual(db.execute("SELECT status FROM paper_source").fetchone()[0], "completed")
                record = json.loads(db.execute("SELECT record_json FROM paper_resolution").fetchone()[0])
                self.assertEqual(record["resolver_commit_num"], 0)

    def test_conflicting_issue_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "Conflicting"):
                self.ingest(root, [{**document(), "_id": ObjectId()}, {**document(), "_id": ObjectId()}])
            with closing(sqlite3.connect(root / "paper.sqlite3")) as db:
                self.assertEqual(db.execute("SELECT status FROM paper_source").fetchone()[0], "failed")

    def test_corrupted_gzip_never_completes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(gzip.BadGzipFile):
                self.ingest(root, [{**document(), "_id": ObjectId()}], corrupt=True)
            with closing(sqlite3.connect(root / "paper.sqlite3")) as db:
                self.assertEqual(db.execute("SELECT status FROM paper_source").fetchone()[0], "failed")

    def test_reject_bad_label_preserve_count(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = self.ingest(Path(temporary), [{**document(), "_id": ObjectId(), "resolver_commit_num": -1}])
            self.assertEqual(report["source_documents"], 1)
            self.assertEqual(report["rejected_documents"], 1)
            self.assertEqual(report["historical_issues"], 0)


if __name__ == '__main__':
    unittest.main()
