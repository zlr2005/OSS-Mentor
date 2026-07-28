from __future__ import annotations

import unittest
from pathlib import Path

from oss_mentor.api import RecommendationApi


class FakeStore:
    database_path = Path("demo.sqlite3")

    def __init__(self):
        self.feedback: dict[tuple[str, int], str] = {}

    def list_profiles_public(self):
        return [{"profile_key": "demo", "service_track": "newcomer"}]

    def profile_for_matching(self, profile_key):
        if profile_key != "demo":
            raise ValueError("missing")
        return {
            "profile_key": "demo",
            "display_name": "Demo",
            "service_track": "newcomer",
            "preferred_languages": ["Python"],
            "operating_systems": ["macos"],
            "preferred_task_types": ["bug_fix"],
            "max_code_difficulty": 1,
            "max_setup_difficulty": 2,
            "desired_skill_stretch": 0,
            "skills": {"python": 1},
        }

    def matchable_candidates(self):
        return [
            {
                "task_candidate_id": 1,
                "repository": "example/demo",
                "issue_number": 7,
                "title": "Example",
                "html_url": "https://github.com/example/demo/issues/7",
                "newcomer_label_signal": 1,
                "estimated_code_difficulty": 1,
                "estimated_setup_difficulty": 1,
                "newcomer_score": 80.0,
                "growth_value_score": 50.0,
                "primary_language": "Python",
                "task_types": ["bug_fix"],
                "requirements": [
                    {"skill_name": "Python", "minimum_level": 1, "importance": 1.0}
                ],
            }
        ]

    def feedback_states(self, feedback_context, task_candidate_ids):
        return {
            task_id: self.feedback[(feedback_context, task_id)]
            for task_id in task_candidate_ids
            if (feedback_context, task_id) in self.feedback
        }

    def record_feedback(
        self, *, task_candidate_id, feedback_context, service_track, feedback_state
    ):
        if task_candidate_id != 1:
            raise ValueError("missing")
        key = (feedback_context, task_candidate_id)
        changed = self.feedback.get(key) != feedback_state
        self.feedback[key] = feedback_state
        return {
            "task_candidate_id": task_candidate_id,
            "feedback_context": feedback_context,
            "service_track": service_track,
            "feedback_state": feedback_state,
            "changed": changed,
        }

    def feedback_summary(self):
        current = {
            "total": len(self.feedback),
            "interested": 0,
            "not_suitable": 0,
            "started": 0,
            "completed": 0,
        }
        for state in self.feedback.values():
            current[state] += 1
        return {
            "current": current,
            "by_track": {
                "newcomer": current,
                "growth": {
                    "total": 0,
                    "interested": 0,
                    "not_suitable": 0,
                    "started": 0,
                    "completed": 0,
                },
            },
            "transitions": {
                "interested_to_started": 0,
                "started_to_completed": 0,
            },
        }

    def system_status(self):
        return {
            "database_ready": True,
            "database_path": str(self.database_path),
            "repository_count": 1,
            "candidate_count": 1,
            "eligible_count": 1,
            "matchable_count": 1,
            "newcomer_count": 1,
            "last_sync_at": "2026-07-01T00:00:00+00:00",
            "features_extracted_count": 1,
            "type_identified_count": 1,
            "type_identification_rate": 1.0,
            "skill_coverage_count": 1,
            "skill_coverage_rate": 1.0,
        }


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = RecommendationApi(FakeStore())

    def test_health_and_profiles(self) -> None:
        health = self.api.handle("GET", "/health")
        profiles = self.api.handle("GET", "/api/v1/profiles")
        self.assertEqual(200, health.status)
        self.assertEqual("ok", health.body["status"])
        self.assertEqual("demo", profiles.body["items"][0]["profile_key"])

    def test_recommendations_are_explainable(self) -> None:
        response = self.api.handle(
            "GET",
            "/api/v1/recommendations",
            {"profile_key": ["demo"], "limit": ["5"]},
        )
        self.assertEqual(200, response.status)
        self.assertEqual(1, response.body["count"])
        self.assertEqual("newcomer", response.body["items"][0]["track"])
        self.assertIn("skill_gaps", response.body["items"][0])

    def test_input_errors_do_not_leak_internal_details(self) -> None:
        missing = self.api.handle("GET", "/api/v1/recommendations")
        invalid = self.api.handle(
            "GET", "/api/v1/recommendations", {"profile_key": ["missing"]}
        )
        method = self.api.handle("POST", "/api/v1/profiles")
        self.assertEqual(400, missing.status)
        self.assertEqual("missing_profile_key", missing.body["error"]["code"])
        self.assertEqual(404, invalid.status)
        self.assertEqual("profile_not_found", invalid.body["error"]["code"])
        self.assertEqual(405, method.status)

    def test_custom_profile_recommendations_are_transient(self) -> None:
        response = self.api.handle(
            "POST",
            "/api/v1/recommendations/custom",
            body={
                "limit": 5,
                "profile": {
                    "display_name": "我的画像",
                    "service_track": "newcomer",
                    "preferred_languages": ["Python"],
                    "operating_systems": ["macos"],
                    "preferred_task_types": ["bug_fix"],
                    "max_code_difficulty": 1,
                    "max_setup_difficulty": 2,
                    "desired_skill_stretch": 0,
                    "skills": {"Python": 1, "testing": 1},
                },
            },
        )
        self.assertEqual(200, response.status)
        self.assertEqual(1, response.body["count"])
        self.assertEqual("user_input", response.body["profile"]["profile_source"])
        self.assertFalse(response.body["profile_persisted"])

    def test_custom_option_inventory_prevents_empty_language_choice(self) -> None:
        response = self.api.handle(
            "POST",
            "/api/v1/recommendation-options",
            body={
                "profile": {
                    "service_track": "newcomer",
                    "preferred_languages": ["Python"],
                    "operating_systems": ["macos"],
                    "preferred_task_types": ["bug_fix"],
                    "max_code_difficulty": 1,
                    "max_setup_difficulty": 2,
                    "desired_skill_stretch": 0,
                    "skills": {"Python": 1},
                }
            },
        )
        self.assertEqual(200, response.status)
        availability = response.body["availability"]
        self.assertEqual(1, availability["current_selection_count"])
        self.assertEqual(0, availability["language_counts"]["go"])

    def test_feedback_is_saved_and_returned_with_recommendations(self) -> None:
        saved = self.api.handle(
            "POST",
            "/api/v1/feedback",
            body={
                "task_candidate_id": 1,
                "feedback_context": "preset:demo",
                "feedback_state": "interested",
            },
        )
        recommendations = self.api.handle(
            "GET",
            "/api/v1/recommendations",
            {"profile_key": ["demo"]},
        )
        self.assertEqual(200, saved.status)
        self.assertTrue(saved.body["feedback"]["changed"])
        self.assertEqual("interested", recommendations.body["items"][0]["feedback_state"])
        self.assertEqual("preset:demo", recommendations.body["feedback_context"])

    def test_feedback_summary_route_returns_current_counts(self) -> None:
        self.api.handle(
            "POST",
            "/api/v1/feedback",
            body={
                "task_candidate_id": 1,
                "feedback_context": "preset:demo",
                "feedback_state": "interested",
            },
        )
        response = self.api.handle("GET", "/api/v1/feedback/summary")
        self.assertEqual(200, response.status)
        self.assertEqual(1, response.body["summary"]["current"]["total"])
        self.assertEqual(1, response.body["summary"]["current"]["interested"])

    def test_custom_profile_can_receive_anonymous_feedback_context(self) -> None:
        response = self.api.handle(
            "POST",
            "/api/v1/recommendations/custom",
            body={
                "feedback_client_id": "12345678-1234-4234-8234-123456789abc",
                "profile": {
                    "service_track": "growth",
                    "preferred_languages": ["Python"],
                    "operating_systems": ["macos"],
                    "preferred_task_types": ["bug_fix"],
                    "max_code_difficulty": 3,
                    "max_setup_difficulty": 3,
                    "desired_skill_stretch": 1,
                    "skills": {"Python": 2},
                },
            },
        )
        self.assertEqual(200, response.status)
        self.assertEqual(
            "custom:12345678-1234-4234-8234-123456789abc:growth",
            response.body["feedback_context"],
        )

    def test_custom_profile_rejects_invalid_or_extra_fields(self) -> None:
        invalid = self.api.handle(
            "POST",
            "/api/v1/recommendations/custom",
            body={"profile": {"service_track": "expert", "secret": "value"}},
        )
        self.assertEqual(400, invalid.status)
        self.assertEqual("invalid_profile", invalid.body["error"]["code"])

    def test_feedback_summary_returns_counts(self) -> None:
        self.api.handle(
            "POST",
            "/api/v1/feedback",
            body={
                "task_candidate_id": 1,
                "feedback_context": "preset:demo",
                "feedback_state": "interested",
            },
        )
        response = self.api.handle("GET", "/api/v1/feedback/summary")
        self.assertEqual(200, response.status)
        self.assertEqual(1, response.body["current"]["total"])
        self.assertEqual(1, response.body["current"]["interested"])
        self.assertIn("transitions", response.body)

    def test_status_endpoint_returns_system_info(self) -> None:
        response = self.api.handle("GET", "/api/v1/status")
        self.assertEqual(200, response.status)
        self.assertTrue(response.body["database_ready"])
        self.assertEqual(1, response.body["repository_count"])
        self.assertIn("api_version", response.body)
        self.assertIn("match_version", response.body)

    def test_feedback_rejects_invalid_state_and_context(self) -> None:
        invalid_state = self.api.handle(
            "POST",
            "/api/v1/feedback",
            body={
                "task_candidate_id": 1,
                "feedback_context": "preset:demo",
                "feedback_state": "clicked",
            },
        )
        invalid_context = self.api.handle(
            "POST",
            "/api/v1/feedback",
            body={
                "task_candidate_id": 1,
                "feedback_context": "custom:not-a-uuid:growth",
                "feedback_state": "started",
            },
        )
        self.assertEqual("invalid_feedback_state", invalid_state.body["error"]["code"])
        self.assertEqual("invalid_feedback_context", invalid_context.body["error"]["code"])


if __name__ == "__main__":
    unittest.main()
