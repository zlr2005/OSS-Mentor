from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]

from oss_mentor.profiling.temporal_training import (
    build_temporal_training_profile,
    load_questionnaire_contract,
    validate_temporal_training_export,
    validate_temporal_training_profile,
)


class TemporalTrainingProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.backfill = self.base / "backfill"
        (self.backfill / "code").mkdir(parents=True)
        (self.backfill / "api_cache").mkdir(parents=True)
        self.content = b"print('hello')\n"
        self.content_sha = hashlib.sha256(self.content).hexdigest()
        (self.backfill / "code/example.txt").write_bytes(self.content)
        (self.backfill / "api_cache/example.json").write_text(
            json.dumps(
                {
                    "observed_at": "2026-11-02T16:29:14Z",
                    "payload": {
                        "author": {
                            "id": 42,
                            "login": "ExampleUser",
                            "type": "User",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        self.key = ("example/project", "a" * 40, "src/app.py")
        self.row = {
            "repository": self.key[0],
            "commit_sha": self.key[1],
            "path": self.key[2],
            "github_numeric_id": 42,
            "event_at": "2025-01-15T12:00:00Z",
            "content_available": True,
            "content_sha256": self.content_sha,
            "sources": [
                {
                    "source": "backfill",
                    "github_numeric_id": 42,
                    "event_at": "2025-01-15T12:00:00Z",
                    "content_sha256": self.content_sha,
                    "content_location": "code/example.txt",
                    "evidence_ref": "api_cache/example.json",
                    "file_version_key": "history-file:example",
                }
            ],
        }
        self.group = {
            "github_numeric_id": 42,
            "cutoff_at": "2025-04-01T00:00:00Z",
            "split": "train",
            "x_contribution_keys": [list(self.key)],
            "y": [{"must_not_be_consumed": True}],
        }
        self.questionnaire = load_questionnaire_contract(
            ROOT / "config/midterm_profile/questionnaire_v0.1.json"
        )

    def build(self, group=None, row=None):
        return build_temporal_training_profile(
            self.group if group is None else group,
            contributions_by_key={self.key: self.row if row is None else row},
            source_roots={"backfill": self.backfill},
            questionnaire=self.questionnaire,
        )

    def test_builds_contract_valid_time_safe_profile(self) -> None:
        profile, audit = self.build()
        validate_temporal_training_profile(profile, verify_hash=True)
        self.assertEqual(profile["schema_version"], "enhanced-profile-training-export-v0.1")
        self.assertEqual(profile["profile_state"], "research_frozen")
        self.assertEqual(profile["delivery_status"], "preview_not_for_training")
        self.assertEqual(profile["developer_key"], "exampleuser")
        self.assertEqual(profile["github_numeric_id"], 42)
        self.assertEqual(len(profile["feature_names"]), 23)
        self.assertEqual(len(profile["locked_mask"]), 23)
        self.assertFalse(any(profile["locked_mask"]))
        self.assertEqual(profile["current_interests"], [])

        features = {name: index for index, name in enumerate(profile["feature_names"])}
        for name in (
            "capability.code_change",
            "capability.open_source_experience",
            "preference.language.python",
        ):
            self.assertTrue(profile["observed_mask"][features[name]])
            self.assertGreater(profile["values"][features[name]], 0.0)
        unknown = features["constraint.weekly_time"]
        self.assertFalse(profile["observed_mask"][unknown])
        self.assertEqual(profile["values"][unknown], 0.0)
        self.assertEqual(audit["x_contribution_count"], 1)
        self.assertEqual(audit["observed_feature_count"], 3)

    def test_outcome_payload_is_not_consumed(self) -> None:
        first, _ = self.build()
        changed = copy.deepcopy(self.group)
        changed["y"] = [{"different_future_label": "ignored"}]
        second, _ = self.build(group=changed)
        self.assertEqual(first, second)

    def test_preview_status_does_not_change_profile_hash(self) -> None:
        preview, _ = self.build()
        candidate, _ = build_temporal_training_profile(
            self.group,
            contributions_by_key={self.key: self.row},
            source_roots={"backfill": self.backfill},
            questionnaire=self.questionnaire,
            delivery_status="research_frozen_candidate",
        )
        self.assertNotEqual(preview["delivery_status"], candidate["delivery_status"])
        self.assertEqual(preview["profile_hash"], candidate["profile_hash"])

    def test_independent_export_validator_reports_coverage(self) -> None:
        profile, _ = self.build()
        report, failures = validate_temporal_training_export(
            [profile],
            expected_feature_names=self.questionnaire["feature_names"],
            source="fixture.jsonl",
        )
        self.assertEqual(failures, [])
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["records"]["valid"], 1)
        self.assertEqual(report["profiles_per_developer"]["by_developer"], {"exampleuser": 1})
        self.assertEqual(
            report["time_coverage"]["cutoffs"],
            {"2025-04-01T00:00:00Z": 1},
        )

    def test_independent_export_validator_rejects_duplicate_snapshot(self) -> None:
        profile, _ = self.build()
        report, failures = validate_temporal_training_export(
            [profile, copy.deepcopy(profile)],
            expected_feature_names=self.questionnaire["feature_names"],
        )
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["records"]["invalid"], 1)
        self.assertIn("duplicate profile", failures[0]["detail"])

    def test_rejects_contribution_at_or_after_cutoff(self) -> None:
        group = copy.deepcopy(self.group)
        group["cutoff_at"] = "2025-01-15T12:00:00Z"
        with self.assertRaisesRegex(ValueError, "not strictly before cutoff"):
            self.build(group=group)

    def test_rejects_content_hash_mismatch(self) -> None:
        row = copy.deepcopy(self.row)
        row["content_sha256"] = "0" * 64
        row["sources"][0]["content_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "fail SHA-256"):
            self.build(row=row)

    def test_rejects_unverified_numeric_identity(self) -> None:
        evidence = self.backfill / "api_cache/example.json"
        payload = json.loads(evidence.read_text(encoding="utf-8"))
        payload["payload"]["author"]["id"] = 43
        evidence.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "does not map GitHub numeric ID"):
            self.build()

    def test_cli_writes_three_split_preview(self) -> None:
        dataset = self.base / "dataset"
        ab_root = self.base / "ab"
        output = self.base / "output"
        dataset.mkdir()
        ab_root.mkdir()
        (ab_root / "manifest.json").write_text("{}\n", encoding="utf-8")
        (self.backfill / "manifest.json").write_text("{}\n", encoding="utf-8")
        with (dataset / "file_contributions.jsonl").open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(self.row) + "\n")
        split_cutoffs = {
            "train": "2025-04-01T00:00:00Z",
            "validation": "2026-01-01T00:00:00Z",
            "test": "2026-07-01T00:00:00Z",
        }
        for split, cutoff in split_cutoffs.items():
            group = copy.deepcopy(self.group)
            group["split"] = split
            group["cutoff_at"] = cutoff
            (dataset / f"{split}.jsonl").write_text(
                json.dumps(group) + "\n", encoding="utf-8"
            )
        for name in ("source_manifest.json", "summary.json"):
            (dataset / name).write_text("{}\n", encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/midterm_b/export_temporal_training_profiles.py"),
                "--dataset-root",
                str(dataset),
                "--ab-root",
                str(ab_root),
                "--backfill-root",
                str(self.backfill),
                "--output-dir",
                str(output),
                "--preview-per-split",
                "1",
                "--source-commit",
                "fixture-commit",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        profiles = [
            json.loads(line)
            for line in (output / "profiles.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        manifest = json.loads((output / "profile_manifest.json").read_text(encoding="utf-8"))
        validation = json.loads(
            (output / "profile_validation_report.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(profiles), 3)
        self.assertEqual({row["split"] for row in profiles}, set(split_cutoffs))
        self.assertFalse(manifest["training_allowed"])
        self.assertEqual(manifest["profiles"]["failed"], 0)
        self.assertEqual(validation["status"], "passed")
        self.assertEqual(validation["records"]["valid"], 3)
        self.assertEqual((output / "profile_failures.jsonl").read_text(encoding="utf-8"), "")

    def test_release_flag_requires_full_selection(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/midterm_b/export_temporal_training_profiles.py"),
                "--backfill-root",
                str(self.backfill),
                "--output-dir",
                str(self.base / "invalid-release"),
                "--preview-per-split",
                "1",
                "--release-training-package",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires --preview-per-split 0", result.stderr)


if __name__ == "__main__":
    unittest.main()
