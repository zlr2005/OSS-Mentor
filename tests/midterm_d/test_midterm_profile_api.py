from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from oss_mentor.api import RecommendationApi
from oss_mentor.services.auth_service import AuthService, AuthSettings, SESSION_COOKIE_NAME
from oss_mentor.storage.identity import IdentityStore
from oss_mentor.storage.profiles import SQLiteProfileStorage


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "db" / "sqlite"
QUESTIONNAIRE_VERSION = "2026-10-18.v1"


class MidtermProfileApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.storage = SQLiteProfileStorage(
            Path(self.temporary.name) / "midterm-api.sqlite3",
            MIGRATIONS / "001_mvp.sql",
        )
        self.storage.initialize()
        identities = IdentityStore(self.storage)
        self.user_id = identities.create_user(
            github_user_id=101,
            github_login="Fixture-Dev",
        )
        self.other_user_id = identities.create_user(
            github_user_id=102,
            github_login="Other-Dev",
        )
        identities.create_session(
            user_id=self.user_id,
            session_id="owner-session",
            expires_at="2099-01-01T00:00:00Z",
        )
        identities.create_session(
            user_id=self.other_user_id,
            session_id="other-session",
            expires_at="2099-01-01T00:00:00Z",
        )
        self.auth = AuthService(identities, AuthSettings(session_secret="fixture-session"))
        self.api = RecommendationApi(self.storage, auth_service=self.auth)
        self.profile = json.loads(
            (ROOT / "fixtures/contracts/v0.5/profiles.json").read_text(encoding="utf-8")
        )["profiles"][0]
        self.api.profile_service.save_manual_profile(
            copy.deepcopy(self.profile),
            user_id=self.user_id,
        )
        scenarios = json.loads(
            (ROOT / "fixtures/midterm/b/assessment_scenarios_v0.1.json").read_text(
                encoding="utf-8"
            )
        )
        self.answers = next(
            item for item in scenarios["scenarios"] if item["id"] == "zero_history"
        )["submission"]["answers"]

    def call(
        self,
        method: str,
        path: str,
        body=None,
        *,
        session: str | None = "owner-session",
        query=None,
    ):
        cookies = {} if session is None else {SESSION_COOKIE_NAME: session}
        return self.api.handle(method, path, body=body, query=query, cookies=cookies)

    def preview(self, *, key: str = "preview:main", answers=None):
        return self.call(
            "POST",
            "/api/v1/me/profile/assessment-preview",
            {
                "questionnaire_version": QUESTIONNAIRE_VERSION,
                "answers": copy.deepcopy(answers or self.answers),
                "idempotency_key": key,
            },
        )

    def owner_profile_for_other_user(self) -> None:
        self.api.profile_service.save_manual_profile(
            {**copy.deepcopy(self.profile), "profile_key": "other-user-profile"},
            user_id=self.other_user_id,
        )

    def test_new_routes_require_login_and_a_profile(self) -> None:
        for method, path, body in (
            ("GET", "/api/v1/me/profile/questionnaire", None),
            (
                "POST",
                "/api/v1/me/profile/assessment-preview",
                {
                    "questionnaire_version": QUESTIONNAIRE_VERSION,
                    "answers": self.answers,
                    "idempotency_key": "unauth",
                },
            ),
            (
                "POST",
                "/api/v1/me/profile/assessment-confirm",
                {"plan_id": "plan:x", "decisions": {"suggestion:x": "accept"}, "idempotency_key": "x"},
            ),
            ("GET", "/api/v1/me/profile/enhanced-features", None),
        ):
            with self.subTest(path=path):
                response = self.call(method, path, body, session=None)
                self.assertEqual(401, response.status, response.body)
                self.assertEqual(
                    response.body["request_id"],
                    response.body["error"]["request_id"],
                )

        response = self.call(
            "POST",
            "/api/v1/me/profile/assessment-preview",
            {
                "questionnaire_version": QUESTIONNAIRE_VERSION,
                "answers": self.answers,
                "idempotency_key": "no-profile",
            },
            session="other-session",
        )
        self.assertEqual(404, response.status, response.body)
        self.assertEqual("profile_required", response.body["error"]["code"])

        self.owner_profile_for_other_user()
        response = self.call(
            "GET",
            "/api/v1/me/profile/enhanced-features",
            session="other-session",
        )
        self.assertEqual(404, response.status, response.body)
        self.assertEqual("profile_not_confirmed", response.body["error"]["code"])

    def test_questionnaire_preview_confirmation_and_public_export(self) -> None:
        questionnaire = self.call("GET", "/api/v1/me/profile/questionnaire")
        self.assertEqual(200, questionnaire.status, questionnaire.body)
        self.assertEqual("v0.6", questionnaire.body["api_version"])
        self.assertEqual(
            QUESTIONNAIRE_VERSION,
            questionnaire.body["questionnaire"]["questionnaire_version"],
        )

        first = self.preview()
        self.assertEqual(200, first.status, first.body)
        self.assertEqual("v0.6", first.body["api_version"])
        self.assertTrue(first.body["submission_id"].startswith("assessment-submission:"))
        replay = self.preview()
        self.assertEqual(200, replay.status, replay.body)
        self.assertEqual(first.body["submission_id"], replay.body["submission_id"])
        self.assertEqual(
            first.body["preview"]["preview_id"],
            replay.body["preview"]["preview_id"],
        )
        self.assertEqual(
            first.body["plan"]["plan_id"],
            replay.body["plan"]["plan_id"],
        )

        conflict_answers = copy.deepcopy(self.answers)
        conflict_answers["open_source_stage"] = "explored"
        conflict = self.preview(answers=conflict_answers)
        self.assertEqual(409, conflict.status, conflict.body)
        self.assertEqual("state_conflict", conflict.body["error"]["code"])

        suggestions = [
            row
            for row in first.body["plan"]["suggestions"]
            if row["status"] == "pending"
        ]
        self.assertGreaterEqual(len(suggestions), 2)
        decisions = {
            row["suggestion_id"]: ("accept" if index == 0 else "reject")
            for index, row in enumerate(suggestions)
        }
        confirm_body = {
            "plan_id": first.body["plan"]["plan_id"],
            "decisions": decisions,
            "idempotency_key": "confirm:main",
        }
        confirmed = self.call(
            "POST",
            "/api/v1/me/profile/assessment-confirm",
            confirm_body,
        )
        self.assertEqual(200, confirmed.status, confirmed.body)
        confirmation = confirmed.body["confirmation"]
        self.assertEqual(1, len(confirmation["applied_suggestion_ids"]))
        self.assertEqual(len(decisions) - 1, len(confirmation["rejected_suggestion_ids"]))
        self.assertEqual(
            confirmation["new_profile_version"],
            confirmation["profile_state"]["profile_version"],
        )

        replay = self.call(
            "POST",
            "/api/v1/me/profile/assessment-confirm",
            confirm_body,
        )
        self.assertEqual(200, replay.status, replay.body)
        self.assertEqual(
            confirmation["confirmation_id"],
            replay.body["confirmation"]["confirmation_id"],
        )

        reused = copy.deepcopy(confirm_body)
        reused["decisions"] = {
            suggestion_id: (
                "reject" if decision == "accept" else "accept"
            )
            for suggestion_id, decision in decisions.items()
        }
        conflict = self.call(
            "POST",
            "/api/v1/me/profile/assessment-confirm",
            reused,
        )
        self.assertEqual(409, conflict.status, conflict.body)

        features = self.call("GET", "/api/v1/me/profile/enhanced-features")
        self.assertEqual(200, features.status, features.body)
        exported = features.body["features"]
        self.assertEqual("enhanced-profile-export-v0.1", exported["schema_version"])
        self.assertEqual("fixture-dev", exported["developer_key"])
        self.assertNotEqual(exported["profile_key"], exported["developer_key"])
        self.assertEqual(23, len(exported["feature_names"]))
        self.assertEqual(
            confirmation["new_profile_version"],
            exported["profile_version"],
        )

        historical = self.call(
            "GET",
            "/api/v1/me/profile/enhanced-features",
            query={"profile_version": [exported["profile_version"]]},
        )
        self.assertEqual(200, historical.status, historical.body)
        self.assertEqual(
            exported["profile_version"],
            historical.body["features"]["profile_version"],
        )

    def test_identity_fields_and_unknown_queries_are_rejected(self) -> None:
        preview = self.preview().body
        for field, value in (
            ("user_id", self.other_user_id),
            ("profile_key", "other-user-profile"),
            ("developer_key", "other-dev"),
            ("base_profile_version", "profile:forged"),
        ):
            body = {
                "questionnaire_version": QUESTIONNAIRE_VERSION,
                "answers": self.answers,
                "idempotency_key": f"forged:{field}",
                field: value,
            }
            response = self.call(
                "POST",
                "/api/v1/me/profile/assessment-preview",
                body,
            )
            self.assertEqual(400, response.status, response.body)
            self.assertEqual("invalid_request", response.body["error"]["code"])

        response = self.call(
            "POST",
            "/api/v1/me/profile/assessment-confirm",
            {
                "plan_id": preview["plan"]["plan_id"],
                "decisions": {},
                "idempotency_key": "bad-confirm",
                "profile_key": "forged",
            },
        )
        self.assertEqual(400, response.status, response.body)

        response = self.call(
            "GET",
            "/api/v1/me/profile/enhanced-features",
            query={"developer_key": ["other-dev"]},
        )
        self.assertEqual(400, response.status, response.body)
        self.assertEqual("invalid_request", response.body["error"]["code"])

    def test_cross_user_and_stale_plan_protection(self) -> None:
        first = self.preview(key="preview:stale-first")
        second = self.preview(key="preview:stale-second")
        self.assertEqual(200, first.status, first.body)
        self.assertEqual(200, second.status, second.body)
        decisions = {
            row["suggestion_id"]: "accept"
            for row in first.body["plan"]["suggestions"]
            if row["status"] == "pending"
        }
        self.owner_profile_for_other_user()
        cross_user = self.call(
            "POST",
            "/api/v1/me/profile/assessment-confirm",
            {
                "plan_id": first.body["plan"]["plan_id"],
                "decisions": decisions,
                "idempotency_key": "cross-user",
            },
            session="other-session",
        )
        self.assertEqual(404, cross_user.status, cross_user.body)

        confirmed = self.call(
            "POST",
            "/api/v1/me/profile/assessment-confirm",
            {
                "plan_id": first.body["plan"]["plan_id"],
                "decisions": decisions,
                "idempotency_key": "stale-first-confirm",
            },
        )
        self.assertEqual(200, confirmed.status, confirmed.body)
        stale = self.call(
            "POST",
            "/api/v1/me/profile/assessment-confirm",
            {
                "plan_id": second.body["plan"]["plan_id"],
                "decisions": {
                    row["suggestion_id"]: "accept"
                    for row in second.body["plan"]["suggestions"]
                    if row["status"] == "pending"
                },
                "idempotency_key": "stale-second-confirm",
            },
        )
        self.assertEqual(409, stale.status, stale.body)
        self.assertEqual("state_conflict", stale.body["error"]["code"])

    def test_confirmation_failure_maps_to_503_and_rolls_back(self) -> None:
        preview = self.preview(key="preview:rollback")
        self.assertEqual(200, preview.status, preview.body)
        decisions = {
            row["suggestion_id"]: "accept"
            for row in preview.body["plan"]["suggestions"]
            if row["status"] == "pending"
        }
        with self.storage.connect() as connection:
            connection.executescript(
                """
                CREATE TRIGGER fail_midterm_api_feature
                BEFORE INSERT ON developer_profile_feature
                BEGIN SELECT RAISE(ABORT, 'injected api failure'); END;
                """
            )
        response = self.call(
            "POST",
            "/api/v1/me/profile/assessment-confirm",
            {
                "plan_id": preview.body["plan"]["plan_id"],
                "decisions": decisions,
                "idempotency_key": "confirm:rollback",
            },
        )
        self.assertEqual(503, response.status, response.body)
        self.assertEqual("service_not_ready", response.body["error"]["code"])
        with self.storage.connect() as connection:
            for table in (
                "profile_assessment_confirmation",
                "profile_assessment_decision",
                "developer_profile_feature_state",
                "developer_profile_feature",
                "developer_profile_current_feature",
            ):
                self.assertEqual(
                    0,
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
                    table,
                )
        plan = self.api.midterm_profile_service.store.get_confirmation_plan(
            user_id=self.user_id,
            plan_id=preview.body["plan"]["plan_id"],
        )
        self.assertEqual("open", plan.status)


class MidtermOpenApiTests(unittest.TestCase):
    def test_public_service_and_storage_exports_are_wired(self) -> None:
        from oss_mentor import CONTRACTS_MIDTERM_VERSION, EnhancedProfileFeatures
        from oss_mentor.services import MidtermProfileService
        from oss_mentor.storage import MidtermProfileStore, SQLiteMidtermProfileStore

        self.assertEqual("contracts-midterm-v0.6.0", CONTRACTS_MIDTERM_VERSION)
        self.assertTrue(EnhancedProfileFeatures)
        self.assertTrue(callable(MidtermProfileService))
        self.assertTrue(MidtermProfileStore)
        self.assertTrue(SQLiteMidtermProfileStore)

    def test_openapi_documents_midterm_routes_and_identity_rules(self) -> None:
        document = (ROOT / "docs/openapi_v0.6.yaml").read_text(encoding="utf-8")
        for path in (
            "/api/v1/me/profile/questionnaire:",
            "/api/v1/me/profile/assessment-preview:",
            "/api/v1/me/profile/assessment-confirm:",
            "/api/v1/me/profile/enhanced-features:",
        ):
            self.assertIn(path, document)
        self.assertIn("developer_key", document)
        self.assertIn("must come from the authenticated session", document)
        self.assertIn("profile_mode", document)
        self.assertIn("cold_start_population", document)
        self.assertIn("coverage_state", document)
        self.assertIn("model_unavailable", document)
        self.assertIn("409", document)


if __name__ == "__main__":
    unittest.main()
