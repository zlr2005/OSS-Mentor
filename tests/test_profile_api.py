from __future__ import annotations

import copy
import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from oss_mentor.api import RecommendationApi, make_handler
from oss_mentor.services.auth_service import AuthService, AuthSettings, SESSION_COOKIE_NAME
from oss_mentor.services.github_profile_source import CONSENT_VERSION, GitHubProfileError
from oss_mentor.services.profile_service import ProfileService
from oss_mentor.storage.identity import IdentityStore
from oss_mentor.storage.profiles import SQLiteProfileStorage

ROOT = Path(__file__).resolve().parents[1]


class FixtureSource:
    def __init__(self):
        self.calls = []

    def collect(self, **kwargs):
        self.calls.append(kwargs)
        return json.loads((ROOT / "fixtures/contracts/v0.5/github_user.json").read_text())


class ProfileApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = SQLiteProfileStorage(Path(temporary.name) / "api.sqlite3",
                                          ROOT / "db/sqlite/001_mvp.sql")
        self.store.initialize()
        self.identities = IdentityStore(self.store)
        self.auth = AuthService(self.identities, AuthSettings(
            client_id="fixture-client", client_secret="fixture-secret", session_secret="fixture-session"
        ))
        state = self.auth.start_oauth(return_to="/profile")["state"]
        with patch.object(self.auth, "_exchange_code", return_value={"access_token": "fixture-token"}), patch.object(
            self.auth, "_fetch_github_user", return_value={"id": 101, "login": "fixture-dev"}
        ):
            self.session = self.auth.handle_callback(code="fixture-code", state=state)["session_id"]
        self.user_id = self.auth.current_user(self.session)["user_id"]
        other_id = self.identities.create_user(github_user_id=102, github_login="other")
        self.identities.create_session(user_id=other_id, session_id="other-session", expires_at="2099-01-01T00:00:00Z")
        self.source = FixtureSource()
        self.api = RecommendationApi(self.store, self.auth, github_profile_source=self.source)
        self.profile = json.loads((ROOT / "fixtures/contracts/v0.5/profiles.json").read_text())["profiles"][0]
        self.editable = {key: value for key, value in self.profile.items()
                         if key not in {"profile_key", "field_metadata", "profile_source", "consent_version"}}

    def call(self, method="GET", path="/api/v1/me/profile", body=None, session=None, query=None):
        return self.api.handle(method, path, body=body, query=query,
                               cookies={SESSION_COOKIE_NAME: session or self.session})

    def create(self):
        result = self.call("PUT", body=copy.deepcopy(self.editable))
        self.assertEqual(200, result.status, result.body)
        return result.body["profile"]

    def imported(self):
        self.create()
        result = self.call("POST", "/api/v1/me/profile/import-github", {"consent_version": CONSENT_VERSION})
        self.assertEqual(200, result.status, result.body)
        return next(s for s in result.body["suggestions"] if s["field_name"] == "skills.build_tooling")

    def decision(self, target, decision="accept", **kwargs):
        return self.call("POST", f"/api/v1/me/profile/suggestions/{target['profile_field_suggestion_id']}/{decision}",
                         {}, **kwargs)

    def test_auth_missing_profile_and_unconfigured_service(self):
        for method, path, body in [
            ("GET", "/api/v1/me/profile", None), ("PUT", "/api/v1/me/profile", self.editable),
            ("POST", "/api/v1/me/profile/import-github", {"consent_version": CONSENT_VERSION}),
            ("POST", "/api/v1/me/profile/suggestions/1/accept", {}),
        ]:
            with self.subTest(path=path, method=method):
                response = self.api.handle(method, path, body=body)
                self.assertEqual(401, response.status)
                self.assertEqual(response.body["request_id"], response.body["error"]["request_id"])
        self.assertEqual(404, self.call().status)
        self.api.profile_service = None
        self.assertEqual(503, self.call().status)
        self.api.auth_service = None
        self.assertEqual(503, self.call().status)

    def test_contract_fixture_routes_are_registered_and_protected(self):
        contract = json.loads((ROOT / "fixtures/contracts/v0.5/profile_api.json").read_text())
        self.assertTrue(contract["routes_implemented"])
        for route in contract["routes"]:
            response = self.api.handle(route["method"], route["path"].replace("{suggestion_id}", "42"),
                                       body=route.get("request_example", {}))
            self.assertEqual(401, response.status, route)

    def test_save_read_and_reload_database(self):
        saved = self.create()
        self.assertNotIn("user_id", saved)
        self.assertNotIn("user_key", saved)
        self.assertNotEqual("fixture-user", saved["profile_key"])
        fresh = RecommendationApi(SQLiteProfileStorage(self.store.database_path, self.store.migration_path), self.auth)
        response = fresh.handle("GET", "/api/v1/me/profile", cookies={SESSION_COOKIE_NAME: self.session})
        self.assertEqual(saved, response.body["profile"])
        self.assertEqual([], response.body["suggestions"])
        self.assertEqual(CONSENT_VERSION, response.body["consent_version"])

    def test_validate_input_and_reject_client_identity_or_provenance(self):
        for patch_value in [
            {"user_id": 2}, {"profile_key": "someone-else"}, {"user_key": "2"},
            {"field_metadata": {}}, {"profile_source": "import"},
            {"max_code_difficulty": True}, {"skills": {"python": 99}},
            {"service_track": []}, {"preferred_languages": [42]}, {"locks": {"skills.Python": "true"}},
        ]:
            with self.subTest(patch_value=patch_value):
                response = self.call("PUT", body={**self.editable, **patch_value})
                self.assertEqual(422, response.status, response.body)
        self.assertEqual(404, self.call().status)
        self.assertEqual(400, self.call("PUT", body=[]).status)
        self.assertEqual(400, self.call(query={"user_id": ["2"]}).status)

    def test_private_profiles_are_not_public_or_available_by_guessed_key(self):
        saved = self.create()
        self.api.profile_service.save_manual_profile({**self.profile, "profile_key": "demo"})
        self.assertEqual(["demo"], [item["profile_key"] for item in self.api.handle("GET", "/api/v1/profiles").body["items"]])
        result = self.api.handle("GET", "/api/v1/recommendations", {"profile_key": [saved["profile_key"]]})
        self.assertEqual(404, result.status)
        result = self.api.handle("POST", "/api/v1/feedback", body={
            "task_candidate_id": 1, "feedback_state": "interested",
            "feedback_context": "preset:" + saved["profile_key"]
        })
        self.assertEqual(400, result.status)
        self.assertEqual(404, self.call(session="other-session").status)

    def test_import_requires_current_consent_before_network(self):
        self.create()
        for body in ({}, {"consent_version": "old"}, {"consent_version": CONSENT_VERSION, "github_login": "other"}):
            self.assertEqual(422, self.call("POST", "/api/v1/me/profile/import-github", body).status)
        self.assertEqual([], self.source.calls)

    def test_credential_restart_logout_and_expired_session(self):
        self.assertEqual(("fixture-token", 101), self.auth.github_credential(self.session))
        self.create()
        restarted = AuthService(self.identities, self.auth.settings)
        self.api.auth_service = restarted
        self.assertEqual(200, self.call().status)
        self.assertEqual(503, self.call("POST", "/api/v1/me/profile/import-github", {"consent_version": CONSENT_VERSION}).status)
        self.auth.logout(self.session)
        self.assertIsNone(self.auth.github_credential(self.session))
        self.assertEqual(401, self.call().status)
        with self.store.connect() as connection:
            values = connection.execute("SELECT access_token_ref FROM oauth_identity").fetchall()
        self.assertTrue(all(value[0] != "fixture-token" for value in values))

    def test_import_maps_upstream_errors_without_persisting(self):
        self.create()
        for status, code in [(401, "authentication_required"), (403, "insufficient_permission"),
                             (429, "rate_limited"), (502, "github_upstream_error")]:
            with patch.object(self.source, "collect", side_effect=GitHubProfileError(status, code, "sanitized")):
                result = self.call("POST", "/api/v1/me/profile/import-github", {"consent_version": CONSENT_VERSION})
                self.assertEqual(status, result.status)
                self.assertEqual(code, result.body["error"]["code"])
        with self.store.connect() as connection:
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM github_profile_import").fetchone()[0])

    def test_suggestion_owner_replay_and_reload(self):
        target = self.imported()
        self.assertEqual("fixture-token", self.source.calls[0]["access_token"])
        self.call("PUT", body=self.editable, session="other-session")
        self.assertEqual(404, self.decision(target, session="other-session").status)
        accepted = self.decision(target)
        self.assertEqual(200, accepted.status)
        self.assertEqual("accepted", accepted.body["suggestion"]["status"])
        self.assertIsNotNone(accepted.body["suggestion"]["resolved_at"])
        self.assertEqual(409, self.decision(target, "reject").status)
        self.assertEqual(accepted.body["suggestions"], self.call().body["suggestions"])
        self.assertEqual(1, self.call().body["profile"]["skills"]["build_tooling"])

    def test_logout_during_collection_does_not_persist_import(self):
        self.create()
        def revoked(**kwargs):
            self.auth.logout(self.session)
            return FixtureSource().collect(**kwargs)
        with patch.object(self.source, "collect", side_effect=revoked):
            response = self.call("POST", "/api/v1/me/profile/import-github",
                                 {"consent_version": CONSENT_VERSION})
        self.assertEqual(401, response.status)
        with self.store.connect() as connection:
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM github_profile_import").fetchone()[0])

    def test_https_callback_cookie_is_secure(self):
        self.auth.settings.base_url = "https://mentor.example"
        state = self.auth.start_oauth(return_to="/profile")["state"]
        with patch.object(self.auth, "_exchange_code", return_value={"access_token": "fixture-token"}), patch.object(
            self.auth, "_fetch_github_user", return_value={"id": 101, "login": "fixture-dev"}
        ):
            response = self.api.handle("GET", "/api/v1/auth/github/callback",
                                       {"state": [state], "code": ["fixture"]})
        self.assertIn("Secure", response.cookies[0][2])

    def test_current_locks_and_manual_provenance_block_stale_suggestions(self):
        target = self.imported()
        edited = copy.deepcopy(self.editable)
        edited["skills"]["build_tooling"] = 3
        edited["locks"] = {"skills.build_tooling": True}
        self.assertEqual(200, self.call("PUT", body=edited).status)
        self.assertEqual(422, self.decision(target).status)
        profile = self.call().body["profile"]
        self.assertEqual("user_input", profile["field_metadata"]["skills.build_tooling"]["source"])
        self.assertEqual(3, profile["skills"]["build_tooling"])

    def test_atomic_rollback_if_status_write_fails(self):
        target = self.imported()
        before = self.call().body["profile"]
        with patch.object(SQLiteProfileStorage, "mark_suggestion_status", side_effect=sqlite3.OperationalError("injected")):
            response = self.decision(target)
        self.assertEqual(503, response.status)
        self.assertNotIn("injected", str(response.body))
        self.assertEqual(before, self.call().body["profile"])
        reloaded = next(item for item in self.call().body["suggestions"]
                        if item["profile_field_suggestion_id"] == target["profile_field_suggestion_id"])
        self.assertEqual("pending", reloaded["status"])

    def test_concurrent_decisions_commit_only_one_consistent_result(self):
        target = self.imported()
        profile_key = self.call().body["profile"]["profile_key"]
        barrier = threading.Barrier(2)
        def resolve(decision):
            barrier.wait()
            service = ProfileService(SQLiteProfileStorage(self.store.database_path, self.store.migration_path))
            try:
                method = service.accept_suggestion if decision == "accept" else service.reject_suggestion
                return method(profile_key=profile_key, suggestion_id=target["profile_field_suggestion_id"])["suggestion"]["status"]
            except ValueError as exc:
                self.assertIn("state_conflict", str(exc))
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(resolve, ["accept", "reject"]))
        self.assertEqual(1, results.count("conflict"))
        profile = self.call().body["profile"]
        if "accepted" in results:
            self.assertEqual(1, profile["skills"]["build_tooling"])
        else:
            self.assertNotIn("build_tooling", profile["skills"])

    @contextmanager
    def http_server(self):
        handler = make_handler(self.api, static_root=ROOT / "web")
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.auth.settings.base_url = f"http://127.0.0.1:{server.server_port}"
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def request(self, server, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        defaults = {"Content-Type": "application/json", "Cookie": f"{SESSION_COOKIE_NAME}={self.session}"}
        defaults.update(headers or {})
        try:
            connection.request(method, path, body=body, headers=defaults)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_http_put_get_assets_and_structured_parse_errors(self):
        with self.http_server() as server, patch.object(BaseHTTPRequestHandler, "log_message"):
            status, headers, raw = self.request(server, "PUT", "/api/v1/me/profile", json.dumps(self.editable))
            self.assertEqual(200, status)
            self.assertEqual("no-store", headers["Cache-Control"])
            self.assertIn("profile", json.loads(raw))
            self.assertEqual(200, self.request(server, "GET", "/api/v1/me/profile")[0])
            for route in ("/profile", "/profile.html", "/assets/profile.js", "/assets/profile.css",
                          "/login", "/assets/login.js", "/assets/login.css"):
                self.assertEqual(200, self.request(server, "GET", route)[0])
            self.assertEqual(404, self.request(server, "GET", "/../pyproject.toml")[0])
            for raw, headers, expected in [
                ("{", {}, 400), ("{}", {"Content-Type": "text/plain"}, 415),
                # Verify header-limit rejection before transmitting an oversized body.
                ("", {"Content-Length": "33000"}, 413),
                ("{}", {"Origin": "https://foreign.example"}, 403),
            ]:
                status, _, data = self.request(server, "PUT", "/api/v1/me/profile", raw, headers)
                self.assertEqual(expected, status)
                decoded = json.loads(data)
                self.assertEqual(decoded["request_id"], decoded["error"]["request_id"])

    def test_http_callback_log_redacts_query(self):
        with self.http_server() as server, patch.object(BaseHTTPRequestHandler, "log_message") as log:
            self.request(server, "GET", "/api/v1/auth/github/callback?code=secret-code&state=secret-state")
            self.assertNotIn("secret-code", str(log.call_args_list))
            self.assertNotIn("secret-state", str(log.call_args_list))

    def test_http_oauth_to_import_and_decision_flow(self):
        with self.http_server() as server, patch.object(BaseHTTPRequestHandler, "log_message"):
            status, _, raw = self.request(server, "GET", "/api/v1/auth/github/start?return_to=/profile")
            self.assertEqual(200, status)
            state = json.loads(raw)["state"]
            with patch.object(self.auth, "_exchange_code", return_value={"access_token": "fixture-token"}), patch.object(
                self.auth, "_fetch_github_user", return_value={"id": 101, "login": "fixture-dev"}
            ):
                status, headers, _ = self.request(
                    server, "GET", f"/api/v1/auth/github/callback?code=fixture&state={state}",
                    headers={"Accept": "text/html"}
                )
            self.assertEqual(303, status)
            self.assertEqual("/profile", headers["Location"])
            cookie = {"Cookie": headers["Set-Cookie"].split(";")[0]}
            self.assertEqual(200, self.request(server, "PUT", "/api/v1/me/profile",
                                              json.dumps(self.editable), cookie)[0])
            status, _, raw = self.request(server, "POST", "/api/v1/me/profile/import-github",
                                         json.dumps({"consent_version": CONSENT_VERSION}), cookie)
            self.assertEqual(200, status)
            target = next(item for item in json.loads(raw)["suggestions"] if item["field_name"] == "skills.build_tooling")
            path = f"/api/v1/me/profile/suggestions/{target['profile_field_suggestion_id']}/accept"
            self.assertEqual(200, self.request(server, "POST", path, "{}", cookie)[0])
            self.assertEqual(409, self.request(server, "POST", path, "{}", cookie)[0])
            status, _, raw = self.request(server, "GET", "/api/v1/me/profile", headers=cookie)
            self.assertEqual(200, status)
            self.assertEqual(1, json.loads(raw)["profile"]["skills"]["build_tooling"])

    def test_login_assets_are_csp_compatible_and_redirect_targets_are_local(self):
        html = (ROOT / "web/login.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>", html)
        self.assertNotIn("<style>", html)
        self.assertIn('src="/assets/login.js"', html)
        for target in ("//foreign.example", "/\\foreign.example", "/path\r\nInjected: yes",
                       "/\t/foreign.example"):
            response = self.api.handle("GET", "/api/v1/auth/github/start", {"return_to": [target]})
            self.assertEqual(400, response.status)


if __name__ == "__main__":
    unittest.main()
