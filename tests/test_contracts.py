from __future__ import annotations

import unittest

from oss_mentor.contracts import (
    CANDIDATE_AVAILABILITY_STATES,
    FEEDBACK_STATES,
    REASON_CODE_SKILL_MATCH,
    REASON_CODES,
    SERVICE_TRACKS,
    SYNC_RUN_STATUSES,
    TASK_TYPES,
    Difficulty,
    Reason,
    RecommendationItemV3,
)


def _make_item(**overrides):
    fields = {
        "task_candidate_id": 1,
        "repository_full_name": "owner/repo",
        "issue_number": 7,
        "title": "Fix bug",
        "html_url": "https://github.com/owner/repo/issues/7",
        "service_track": "newcomer",
        "score": 0.8,
        "difficulty": Difficulty(code=1, setup=1),
        "matched_skills": ("Python",),
        "missing_skills": ("pytest",),
        "reasons": (
            Reason(
                code=REASON_CODE_SKILL_MATCH,
                label="技能匹配",
                evidence="Python 技能满足",
                score_delta=0.2,
            ),
        ),
        "warnings": (),
        "availability": "available",
        "verified_at": "2026-07-29T12:30:00Z",
        "feedback_state": None,
    }
    fields.update(overrides)
    return RecommendationItemV3(**fields)


class ContractTests(unittest.TestCase):
    def test_fixed_enums_have_no_synonyms(self) -> None:
        self.assertEqual(("newcomer", "growth"), SERVICE_TRACKS)
        self.assertEqual(4, len(FEEDBACK_STATES))
        self.assertEqual(6, len(TASK_TYPES))
        self.assertEqual(7, len(CANDIDATE_AVAILABILITY_STATES))
        self.assertEqual(5, len(SYNC_RUN_STATUSES))
        self.assertEqual(10, len(REASON_CODES))

    def test_recommendation_item_serializes_to_contract_shape(self) -> None:
        item = _make_item()
        payload = item.to_dict()
        self.assertEqual(1, payload["task_candidate_id"])
        self.assertEqual("owner/repo", payload["repository_full_name"])
        self.assertEqual({"code": 1, "setup": 1}, payload["difficulty"])
        self.assertEqual(["Python"], payload["matched_skills"])
        self.assertEqual(
            REASON_CODE_SKILL_MATCH, payload["reasons"][0]["code"]
        )

    def test_score_must_be_in_unit_range(self) -> None:
        with self.assertRaises(ValueError):
            _make_item(score=1.5)
        with self.assertRaises(ValueError):
            _make_item(score=-0.1)

    def test_unavailable_task_cannot_be_recommended(self) -> None:
        with self.assertRaises(ValueError):
            _make_item(availability="closed")

    def test_reasons_are_required(self) -> None:
        with self.assertRaises(ValueError):
            _make_item(reasons=())

    def test_unknown_reason_code_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Reason(code="magic_score", label="x", evidence="y", score_delta=0.1)

    def test_invalid_service_track_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _make_item(service_track="expert")

    def test_invalid_feedback_state_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _make_item(feedback_state="clicked")

    def test_difficulty_out_of_range_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Difficulty(code=4, setup=1)
        with self.assertRaises(ValueError):
            Difficulty(code=1, setup=-1)


if __name__ == "__main__":
    unittest.main()
