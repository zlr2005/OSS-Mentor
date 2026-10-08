from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from oss_mentor.api import RecommendationApi
from oss_mentor.services.auth_service import AuthService, AuthSettings, SESSION_COOKIE_NAME
from oss_mentor.services.profile_service import ProfileService
from oss_mentor.services.recommendation_service import RecommendationService
from oss_mentor.storage.identity import IdentityStore
from oss_mentor.storage.profiles import SQLiteProfileStorage

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db" / "sqlite" / "001_mvp.sql"


def candidate(candidate_id: int, score: float) -> dict:
    return {
        "task_candidate_id": candidate_id,
        "repository": "example/demo",
        "issue_number": candidate_id,
        "title": f"Task {candidate_id}",
        "html_url": f"https://github.com/example/demo/issues/{candidate_id}",
        "newcomer_label_signal": 1,
        "estimated_code_difficulty": 1,
        "estimated_setup_difficulty": 1,
        "newcomer_score": score,
        "growth_value_score": score,
        "text_clarity_score": score,
        "primary_language": "Python",
        "task_types": ["testing"],
        "requirements": [
            {"skill_name": "Python", "minimum_level": 1, "importance": 1.0}
        ],
        "candidate_availability": "available",
        "github_verified_at": "2026-09-29T00:00:00Z",
        "warnings_json": "[]",
    }


class MemoryCandidateStore:
    database_path = Path("memory.sqlite3")

    def __init__(self) -> None:
        self.tasks = [candidate(1, 100), candidate(2, 70)]
        self.feedback: dict[tuple[str, int], str] = {}

    def list_profiles_public(self):
        return []

    def matchable_candidates(self):
        return self.tasks

    def feedback_states(self, context, task_candidate_ids):
        return {
            candidate_id: self.feedback[(context, candidate_id)]
            for candidate_id in task_candidate_ids
            if (context, candidate_id) in self.feedback
        }

    def record_feedback(
        self, *, task_candidate_id, feedback_context, service_track, feedback_state
    ):
        self.feedback[(feedback_context, task_candidate_id)] = feedback_state
        return {
            "task_candidate_id": task_candidate_id,
            "feedback_context": feedback_context,
            "service_track": service_track,
            "feedback_state": feedback_state,
            "changed": True,
        }


class RecommendationApiIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.storage = SQLiteProfileStorage(
            Path(temporary.name) / "recommendation-api.sqlite3", MIGRATION
        )
        self.storage.initialize()
        identities = IdentityStore(self.storage)
        self.user_id = identities.create_user(
            github_user_id=701, github_login="api-user"
        )
        self.session_id = "api-session"
        identities.create_session(
            user_id=self.user_id,
            session_id=self.session_id,
            expires_at="2099-01-01T00:00:00Z",
        )
        self.auth = AuthService(identities, AuthSettings(session_secret="test-secret"))
        self.profile_service = ProfileService(self.storage)
        profile = json.loads(
            (ROOT / "fixtures" / "contracts" / "v0.5" / "profiles.json").read_text()
        )["profiles"][0]
        self.profile_service.save_manual_profile(profile, user_id=self.user_id)
        self.store = MemoryCandidateStore()
        self.recommendations = RecommendationService(
            self.store,
            profile_service=self.profile_service,
            auth_service=self.auth,
        )
        self.api = RecommendationApi(
            self.store,
            auth_service=self.auth,
            profile_service=self.profile_service,
            recommendation_service=self.recommendations,
        )

    def call(self, *, path, body=None, session=True, query=None):
        cookies = {SESSION_COOKIE_NAME: self.session_id} if session else {}
        return self.api.handle(
            "GET" if body is None else "POST",
            path,
            query=query,
            body=body,
            cookies=cookies,
        )

    def test_requires_authenticated_session(self) -> None:
        response = self.call(path="/api/v1/me/recommendations", session=False)
        self.assertEqual(401, response.status)
        self.assertEqual("authentication_required", response.body["error"]["code"])

    def test_requires_an_owned_profile(self) -> None:
        identities = IdentityStore(self.storage)
        other_id = identities.create_user(github_user_id=702, github_login="no-profile")
        identities.create_session(
            user_id=other_id,
            session_id="no-profile-session",
            expires_at="2099-01-01T00:00:00Z",
        )
        response = self.api.handle(
            "GET",
            "/api/v1/me/recommendations",
            cookies={SESSION_COOKIE_NAME: "no-profile-session"},
        )
        self.assertEqual(404, response.status)
        self.assertEqual("profile_required", response.body["error"]["code"])

    def test_returns_batch_contract_and_request_id(self) -> None:
        response = self.call(
            path="/api/v1/me/recommendations", query={"limit": ["1"]}
        )
        self.assertEqual(200, response.status, response.body)
        self.assertEqual(1, response.body["count"])
        self.assertEqual("v0.5", response.body["api_version"])
        self.assertEqual("developer-task-match-v0.3", response.body["match_version"])
        self.assertIn("run_id", response.body)
        self.assertIn("request_id", response.body)
        self.assertNotIn("user_id", response.body["profile"])
        item = response.body["items"][0]
        self.assertEqual(1, item["task_candidate_id"])
        self.assertIn("reasons", item)
        self.assertIn("feedback_state", item)

    def test_invalid_limit_is_rejected(self) -> None:
        response = self.call(
            path="/api/v1/me/recommendations", query={"limit": ["0"]}
        )
        self.assertEqual(400, response.status)
        self.assertEqual("invalid_limit", response.body["error"]["code"])

    def test_authenticated_feedback_uses_server_owned_context(self) -> None:
        recommendations = self.call(path="/api/v1/me/recommendations").body
        context = recommendations["feedback_context"]
        response = self.api.handle(
            "POST",
            "/api/v1/feedback",
            body={
                "task_candidate_id": 1,
                "feedback_context": context,
                "feedback_state": "not_suitable",
            },
            cookies={SESSION_COOKIE_NAME: self.session_id},
        )
        self.assertEqual(200, response.status, response.body)
        self.assertEqual("not_suitable", self.store.feedback[(context, 1)])
        refreshed = self.call(path="/api/v1/me/recommendations").body
        penalized = next(
            item for item in refreshed["items"] if item["task_candidate_id"] == 1
        )
        self.assertEqual("not_suitable", penalized["feedback_state"])

    def test_forged_user_feedback_context_is_rejected(self) -> None:
        response = self.api.handle(
            "POST",
            "/api/v1/feedback",
            body={
                "task_candidate_id": 1,
                "feedback_context": "user:999:profile:someone-else",
                "feedback_state": "not_suitable",
            },
            cookies={SESSION_COOKIE_NAME: self.session_id},
        )
        self.assertEqual(403, response.status)
        self.assertEqual("insufficient_permission", response.body["error"]["code"])


if __name__ == "__main__":
    unittest.main()
