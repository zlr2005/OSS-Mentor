from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]

from oss_mentor.profiling.assessment import build_assessment_preview, load_questionnaire
from oss_mentor.profiling.evidence import interpret_github_contributions


class GithubEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(
            (ROOT / "fixtures/midterm/b/github_evidence_scenarios_v0.1.json").read_text(
                encoding="utf-8"
            )
        )

    def interpret(self, records=None, **overrides):
        fixture = self.fixture
        return interpret_github_contributions(
            fixture["records"] if records is None else records,
            fixture["code_documents"],
            developer_key=fixture["developer_key"],
            observed_at=fixture["observed_at"],
            as_of=fixture["as_of"],
            collection_status=overrides.get("collection_status", "partial"),
            collection_limitations=overrides.get(
                "collection_limitations", ["fixture intentionally contains excluded rows"]
            ),
        )

    def test_interprets_source_test_and_documentation_without_count_scoring(self) -> None:
        batch = self.interpret()
        by_feature = {}
        for row in batch["evidence"]:
            by_feature.setdefault(row["feature_name"], []).append(row)
        self.assertEqual(by_feature["capability.code_change"][0]["value"], 0.5)
        self.assertEqual(by_feature["capability.testing"][0]["value"], 0.5)
        self.assertEqual(by_feature["capability.test_design"][0]["value"], 0.25)
        self.assertEqual(by_feature["capability.documentation"][0]["value"], 0.5)
        self.assertTrue(all(row["source_kind"] == "github_direct" for row in batch["evidence"]))
        self.assertEqual(len(by_feature["capability.open_source_experience"]), 3)

    def test_unconfirmed_future_and_missing_code_are_retained_but_excluded(self) -> None:
        batch = self.interpret()
        self.assertEqual(
            batch["coverage"]["exclusion_counts"],
            {
                "author_identity_not_confirmed": 1,
                "code_document_missing": 1,
                "event_after_profile_as_of": 1,
            },
        )
        excluded = [row for row in batch["records"] if row["status"] == "excluded"]
        self.assertEqual(len(excluded), 3)
        self.assertTrue(all(not row["generated_evidence_refs"] for row in excluded))

    def test_duplicate_reimport_is_idempotent_and_reported(self) -> None:
        batch = self.interpret()
        self.assertEqual(batch["coverage"]["input_record_count"], 7)
        self.assertEqual(batch["coverage"]["unique_record_count"], 6)
        duplicated = [row for row in batch["records"] if row["duplicate_count"]]
        self.assertEqual(len(duplicated), 1)
        self.assertEqual(duplicated[0]["duplicate_count"], 1)
        without_duplicate = self.interpret(self.fixture["records"][:-1])
        self.assertEqual(batch["evidence"], without_duplicate["evidence"])

    def test_zero_history_and_failed_collection_remain_valid_unknown_inputs(self) -> None:
        batch = self.interpret(
            [],
            collection_status="failed",
            collection_limitations=["upstream timeout"],
        )
        self.assertEqual(batch["evidence"], [])
        self.assertEqual(batch["coverage"]["status"], "failed")
        self.assertEqual(batch["coverage"]["limitations"], ["upstream timeout"])

    def test_output_is_deterministic_and_does_not_mutate_inputs(self) -> None:
        records = copy.deepcopy(self.fixture["records"])
        documents = copy.deepcopy(self.fixture["code_documents"])
        first = interpret_github_contributions(
            records,
            documents,
            developer_key=self.fixture["developer_key"],
            observed_at=self.fixture["observed_at"],
            as_of=self.fixture["as_of"],
            collection_status="partial",
        )
        second = interpret_github_contributions(
            records,
            documents,
            developer_key=self.fixture["developer_key"],
            observed_at=self.fixture["observed_at"],
            as_of=self.fixture["as_of"],
            collection_status="partial",
        )
        self.assertEqual(first, second)
        self.assertEqual(records, self.fixture["records"])
        self.assertEqual(documents, self.fixture["code_documents"])

    def test_evidence_rows_feed_existing_assessment_preview(self) -> None:
        batch = self.interpret()
        questionnaire = load_questionnaire(
            ROOT / "config/midterm_profile/questionnaire_v0.1.json"
        )
        scenarios = json.loads(
            (ROOT / "fixtures/midterm/b/assessment_scenarios_v0.1.json").read_text(
                encoding="utf-8"
            )
        )
        submission = scenarios["scenarios"][0]["submission"]
        preview = build_assessment_preview(
            questionnaire,
            submission,
            external_evidence=batch["evidence"],
        )
        rows = {row["feature_name"]: row for row in preview["features"]}
        self.assertEqual(rows["capability.code_change"]["source_kind"], "github_direct")
        self.assertEqual(rows["capability.documentation"]["source_kind"], "github_direct")

    def test_other_developer_is_rejected(self) -> None:
        record = copy.deepcopy(self.fixture["records"][0])
        record["developer_key"] = "developer:other"
        with self.assertRaisesRegex(ValueError, "another developer"):
            self.interpret([record])


if __name__ == "__main__":
    unittest.main()
