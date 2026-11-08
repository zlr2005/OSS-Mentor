from __future__ import annotations

import copy
import hashlib
import json
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
        self.assertEqual(
            by_feature["preference.language.python"].details["event_time_basis"],
            "unknown_observed_state_only",
        )

    def test_weak_interest_preserves_source_and_pagination_context(self) -> None:
        manifest, records, _ = load_snapshot(FIXTURE)
        manifest = copy.deepcopy(manifest)
        records = copy.deepcopy(records)
        source_locations = {
            "weak_interest/evidence/star-page.json": "a" * 64,
            "weak_interest/evidence/identity.json": "b" * 64,
            "weak_interest/evidence/repository.json": "c" * 64,
        }
        records["_source_assets"] = source_locations
        behavior = records["project_behaviors"][0]
        behavior.update(
            {
                "source": "github_rest",
                "source_location": "weak_interest/evidence/star-page.json",
                "identity_source_location": "weak_interest/evidence/identity.json",
                "evidence_location": "https://github.com/example/project",
                "interpretation": "weak repository-language interest, not ability",
                "event_at": "2026-10-13T00:00:00Z",
                "event_time_unknown": False,
            }
        )
        records["repositories"][0]["source_location"] = (
            "weak_interest/evidence/repository.json"
        )
        manifest["weak_interest_coverage"] = [
            {
                "developer_key": "dev-a",
                "endpoint": "starred",
                "status": "partial",
                "pages": 2,
                "returned_count": 200,
                "truncated": True,
                "next_page": 3,
            }
        ]

        result = adapt_a_dataset_records_to_profile_evidence(
            manifest,
            records,
            developer_key="dev-a",
            as_of=AS_OF,
            allow_fixture=True,
        )
        weak = next(
            item for item in result["evidence"] if item.source_kind == "github_weak"
        )
        self.assertEqual(weak.details["source"], "github_rest")
        self.assertEqual(
            weak.details["source_location"],
            "weak_interest/evidence/star-page.json",
        )
        self.assertEqual(weak.details["source_sha256"], "a" * 64)
        self.assertEqual(weak.details["identity_source_sha256"], "b" * 64)
        self.assertEqual(weak.details["repository_source_sha256"], "c" * 64)
        self.assertEqual(weak.details["repository_primary_language"], "Python")
        self.assertEqual(
            weak.details["language_mapping"],
            {
                "source_field": "repository.primary_language",
                "source_value": "Python",
                "target_feature": "preference.language.python",
            },
        )
        self.assertEqual(
            weak.details["evidence_location"],
            "https://github.com/example/project",
        )
        self.assertEqual(weak.details["event_time_basis"], "github_starred_at")
        self.assertEqual(weak.details["coverage_status"], "partial")
        self.assertTrue(weak.details["coverage_truncated"])
        self.assertEqual(weak.details["coverage_next_page"], 3)
        self.assertIn("source_location", result["project_behavior_audit"][0])

    def test_real_weak_interest_requires_traceable_source_assets(self) -> None:
        manifest, records, _ = load_snapshot(FIXTURE)
        manifest = copy.deepcopy(manifest)
        records = copy.deepcopy(records)
        manifest["fixture_only"] = False
        manifest["weak_interest_coverage"] = [
            {
                "developer_key": "dev-a",
                "endpoint": "starred",
                "status": "completed",
                "pages": 1,
                "returned_count": 1,
                "truncated": False,
                "next_page": None,
            }
        ]
        records["project_behaviors"][0].update(
            {
                "source_location": "behavior.json",
                "identity_source_location": "identity.json",
            }
        )
        records["repositories"][0]["source_location"] = "repository.json"
        records["_source_assets"] = {
            "behavior.json": "a" * 64,
            "repository.json": "c" * 64,
        }
        with self.assertRaisesRegex(
            ValueError,
            "weak-interest source asset is missing: identity.json",
        ):
            adapt_a_dataset_records_to_profile_evidence(
                manifest,
                records,
                developer_key="dev-a",
                as_of=AS_OF,
            )

    def test_database_reader_resolves_source_asset_hashes(self) -> None:
        manifest = self.connection.execute(
            "SELECT manifest_json FROM midterm_dataset_snapshot "
            "WHERE dataset_version = ?",
            (VERSION,),
        ).fetchone()
        manifest_payload = json.loads(manifest[0])
        manifest_payload["weak_interest_coverage"] = [
            {
                "developer_key": "dev-a",
                "endpoint": "starred",
                "status": "completed",
                "pages": 1,
                "returned_count": 1,
                "truncated": False,
                "next_page": None,
            }
        ]
        self.connection.execute(
            "UPDATE midterm_dataset_snapshot SET manifest_json = ? "
            "WHERE dataset_version = ?",
            (json.dumps(manifest_payload), VERSION),
        )
        row = self.connection.execute(
            "SELECT ordinal, payload_json FROM midterm_dataset_behavior "
            "WHERE dataset_version = ?",
            (VERSION,),
        ).fetchone()
        behavior = json.loads(row[1])
        behavior.update(
            {
                "source_location": "records.json",
                "identity_source_location": "records.json",
                "evidence_location": "https://github.com/example/project",
            }
        )
        self.connection.execute(
            "UPDATE midterm_dataset_behavior SET payload_json = ? "
            "WHERE dataset_version = ? AND ordinal = ?",
            (json.dumps(behavior), VERSION, row[0]),
        )
        repository_row = self.connection.execute(
            "SELECT ordinal, payload_json FROM midterm_dataset_repository "
            "WHERE dataset_version = ?",
            (VERSION,),
        ).fetchone()
        repository = json.loads(repository_row[1])
        repository["source_location"] = "records.json"
        self.connection.execute(
            "UPDATE midterm_dataset_repository SET payload_json = ? "
            "WHERE dataset_version = ? AND ordinal = ?",
            (json.dumps(repository), VERSION, repository_row[0]),
        )
        self.connection.commit()

        result = self.database_result()
        weak = next(
            item for item in result["evidence"] if item.source_kind == "github_weak"
        )
        expected_hash = hashlib.sha256(
            (FIXTURE / "records.json").read_bytes()
        ).hexdigest()
        self.assertEqual(weak.details["source_sha256"], expected_hash)
        self.assertEqual(weak.details["identity_source_sha256"], expected_hash)
        self.assertEqual(weak.details["repository_source_sha256"], expected_hash)
        self.assertEqual(weak.details["coverage_status"], "completed")

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
        manifest = copy.deepcopy(manifest)
        records = copy.deepcopy(records)
        manifest.update(
            {
                "dataset_version": "real-scope-v0.2",
                "fixture_only": False,
                "not_delivered": ["star", "fork", "complete_repository_history"],
                "weak_interest_coverage": [
                    {
                        "developer_key": "dev-a",
                        "endpoint": "starred",
                        "status": "completed",
                        "pages": 1,
                        "returned_count": 1,
                        "truncated": False,
                        "next_page": None,
                    }
                ],
            }
        )
        records["project_behaviors"][0].update(
            {
                "source_location": "behavior.json",
                "identity_source_location": "identity.json",
            }
        )
        records["repositories"][0]["source_location"] = "repository.json"
        records["_source_assets"] = {
            "behavior.json": "a" * 64,
            "identity.json": "b" * 64,
            "repository.json": "c" * 64,
        }
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
