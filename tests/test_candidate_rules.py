from __future__ import annotations

import unittest

from oss_mentor.candidate_rules import evaluate_candidate


def candidate(**overrides):
    value = {
        "state": "open",
        "is_pull_request": False,
        "assignment_state": "unassigned",
        "is_locked": False,
        "has_linked_open_pr": None,
        "source_system": "github_rest",
        "github_issue_id": 123,
        "body_text": "Clear details",
        "labels": [],
    }
    value.update(overrides)
    return value


class CandidateRuleTests(unittest.TestCase):
    def test_verified_open_unassigned_issue_is_eligible(self) -> None:
        result = evaluate_candidate(
            candidate(labels=["Good First Issue"])
        )
        self.assertEqual("eligible", result.eligibility)
        self.assertTrue(result.newcomer_label_signal)
        self.assertIn("linked_pr_not_checked", result.warnings)

    def test_verified_issue_with_no_linked_pr_has_no_warning(self) -> None:
        result = evaluate_candidate(candidate(has_linked_open_pr=False))
        self.assertEqual("eligible", result.eligibility)
        self.assertNotIn("linked_pr_not_checked", result.warnings)

    def test_discovery_record_stays_unknown(self) -> None:
        result = evaluate_candidate(
            candidate(source_system="ecosystems", github_issue_id=None)
        )
        self.assertEqual("unknown", result.eligibility)
        self.assertEqual(("requires_github_verification",), result.reasons)

    def test_assigned_issue_is_temporarily_ineligible(self) -> None:
        result = evaluate_candidate(candidate(assignment_state="assigned"))
        self.assertEqual("temporarily_ineligible", result.eligibility)
        self.assertIn("already_assigned", result.reasons)

    def test_needs_clarification_label_is_temporarily_ineligible(self) -> None:
        result = evaluate_candidate(
            candidate(
                labels=["first-contribution", "status: needs clarification"],
                has_linked_open_pr=False,
            )
        )
        self.assertEqual("temporarily_ineligible", result.eligibility)
        self.assertIn(
            "blocking_label:status: needs clarification", result.reasons
        )

    def test_closed_pull_request_is_excluded(self) -> None:
        result = evaluate_candidate(candidate(state="closed", is_pull_request=True))
        self.assertEqual("excluded", result.eligibility)
        self.assertEqual(
            ("pull_request_not_issue", "not_open"), result.reasons
        )


if __name__ == "__main__":
    unittest.main()
