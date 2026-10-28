from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
import unittest

from oss_mentor.contracts_midterm import ProfileEvidence
from oss_mentor.profiling.dataset_evidence import (
    adapt_a_dataset_records_to_profile_evidence,
    build_profile_evidence_from_a_dataset_store,
    build_profile_evidence_from_a_snapshot,
)
from oss_mentor.datasets.snapshot import load_snapshot
from oss_mentor.sqlite_store import SQLiteCandidateStore
from oss_mentor.storage.datasets import DatasetStore


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "db/sqlite"
FIXTURE = ROOT / "fixtures/midterm/a/snapshot_v0.1"
VERSION = "synthetic-a-v0.1"
AS_OF = "2026-10-14T00:00:00Z"


class DatasetEvidenceAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        database = Path(self.temporary.name) / "adapter.sqlite3"
        factory = SQLiteCandidateStore(database, MIGRATIONS / "001_mvp.sql")
        factory.initialize()
        self.connection = factory.connect()
        self.addCleanup(self.connection.close)
        DatasetStore(self.connection).import_snapshot(FIXTURE, chunk_size=2)

    def snapshot_result(self, developer_key: str = "dev-a"):
        return build_profile_evidence_from_a_snapshot(
            FIXTURE,
            developer_key=developer_key,
            as_of=AS_OF,
            allow_fixture=True,
        )

    def database_result(self, developer_key: str = "dev-a"):
        return build_profile_evidence_from_a_dataset_store(
            self.connection,
            VERSION,
            developer_key=developer_key,
            as_of=AS_OF,
            allow_fixture=True,
        )

    def test_verified_contribution_and_star_map_to_frozen_contract(self) -> None:
        result = self.snapshot_result()
        self.assertTrue(all(isinstance(item, ProfileEvidence) for item in result["evidence"]))
        by_feature = {item.feature_name: item for item in result["evidence"]}
        self.assertEqual(
            set(by_feature),
            {
                "capability.code_change",
                "capability.open_source_experience",
                "preference.language.python",
            },
        )
        self.assertEqual(by_feature["capability.code_change"].source_kind, "github_direct")
        self.assertEqual(by_feature["preference.language.python"].source_kind, "github_weak")
        self.assertEqual(by_feature["preference.language.python"].confidence, 0.2)
        self.assertTrue(by_feature["preference.language.python"].details["event_time_unknown"])
        self.assertFalse(by_feature["preference.language.python"].details["recency_applied"])

    def test_fixture_provenance_and_rename_are_preserved(self) -> None:
        result = self.snapshot_result()
        code = next(
            item for item in result["evidence"] if item.feature_name == "capability.code_change"
        )
        self.assertTrue(result["fixture_only"])
        self.assertTrue(code.details["fixture_only"])
        self.assertEqual(code.details["dataset_version"], VERSION)
        self.assertEqual(code.details["file_aliases"][0]["old_path"], "src/legacy_parser.py")
        self.assertEqual(code.details["file_aliases"][0]["new_path"], "src/parser.py")
        self.assertIn("not_verified_ability", result["interpretation"])

    def test_unconfirmed_identity_and_missing_code_never_create_evidence(self) -> None:
        result = self.snapshot_result("dev-unknown")
        self.assertEqual(result["evidence"], ())
        self.assertEqual(len(result["contribution_audit"]), 1)
        audit = result["contribution_audit"][0]
        self.assertEqual(audit["exclusion_reason"], "author_identity_not_confirmed")
        self.assertEqual(
            audit["dataset_boundary_flags"],
            ["author_identity_not_confirmed", "source_content_unavailable"],
        )

    def test_snapshot_and_completed_database_import_are_equivalent(self) -> None:
        snapshot = self.snapshot_result()
        database = self.database_result()
        self.assertEqual(
            [item.to_dict() for item in snapshot["evidence"]],
            [item.to_dict() for item in database["evidence"]],
        )
        self.assertEqual(snapshot["contribution_audit"], database["contribution_audit"])
        self.assertEqual(snapshot["project_behavior_audit"], database["project_behavior_audit"])

    def test_fixture_requires_explicit_opt_in(self) -> None:
        with self.assertRaisesRegex(ValueError, "allow_fixture=True"):
            build_profile_evidence_from_a_snapshot(
                FIXTURE,
                developer_key="dev-a",
                as_of=AS_OF,
            )

    def test_incomplete_database_import_is_rejected(self) -> None:
        self.connection.execute(
            "UPDATE midterm_dataset_import SET status = 'failed' WHERE dataset_version = ?",
            (VERSION,),
        )
        self.connection.commit()
        with self.assertRaisesRegex(ValueError, "must be completed"):
            self.database_result()

    def test_issue_file_links_are_not_misused_as_profile_evidence(self) -> None:
        result = self.snapshot_result()
        audits = {item["feature_role"]: item for item in result["issue_file_audit"]}
        self.assertEqual(
            audits["outcome_only"]["exclusion_reason"],
            "outcome_only_not_available_for_prediction",
        )
        self.assertEqual(
            audits["pre_resolution_hint"]["exclusion_reason"],
            "task_evidence_not_developer_attributed",
        )

    def test_repeated_reads_are_deterministic(self) -> None:
        first = self.database_result()
        second = self.database_result()
        self.assertEqual(
            [item.to_dict() for item in first["evidence"]],
            [item.to_dict() for item in second["evidence"]],
        )
        self.assertEqual(first["coverage"], second["coverage"])

    def test_real_dataset_scope_preserves_not_delivered_signals(self) -> None:
        manifest, records, _ = load_snapshot(FIXTURE)
        manifest = dict(manifest)
        manifest.update(
            {
                "dataset_version": "real-scope-v0.2",
                "fixture_only": False,
                "not_delivered": ["star", "fork", "complete_repository_history"],
            }
        )
        result = adapt_a_dataset_records_to_profile_evidence(
            manifest,
            records,
            developer_key="dev-a",
            as_of=AS_OF,
        )
        self.assertFalse(result["fixture_only"])
        limitations = result["coverage"]["limitations"]
        self.assertTrue(all(item in limitations for item in manifest["quality_gaps"]))
        self.assertEqual(
            limitations[-3:],
            ("star", "fork", "complete_repository_history"),
        )


if __name__ == "__main__":
    unittest.main()
