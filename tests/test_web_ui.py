from __future__ import annotations

import unittest
from pathlib import Path

from oss_mentor.api import load_static_asset


class WebUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1] / "web"

    def asset(self, path: str):
        asset = load_static_asset(self.root, path)
        self.assertIsNotNone(asset, path)
        return asset

    def test_home_is_recommendation_first(self) -> None:
        html = self.asset("/").body.decode("utf-8")
        for identifier in (
            'id="hero-title"',
            'id="issue-list"',
            'id="run-context"',
            'id="public-candidates"',
            'id="project-opportunities"',
            'id="session-status"',
            'id="session-actions"',
            'href="/recommendations"',
            'href="/profile"',
            'href="/recommendation-run"',
            'href="/status"',
        ):
            self.assertIn(identifier, html)
        # The home page must not contain the old profile-editing form.
        self.assertNotIn('id="recommendation-form"', html)
        self.assertNotIn('data-skill="typescript"', html)
        self.assertNotIn('aria-current="page">首页</a>', html)
        self.assertIn('class="brand" href="/"', html)

    def test_home_script_uses_real_recommendation_contracts(self) -> None:
        script = self.asset("/assets/recommendations.js").body.decode("utf-8")
        shared = self.asset("/assets/ui.js").body.decode("utf-8")
        for path in (
            "/api/v1/me",
            "/api/v1/me/recommendations?limit=10",
            "/api/v1/public-candidates?limit=",
            "/api/v1/me/project-recommendations?run_id=",
        ):
            self.assertIn(path, script)
        self.assertIn("/api/v1/feedback", shared)
        self.assertIn("service_track", shared)
        self.assertIn("matched_skills", shared)
        self.assertIn("missing_skills", shared)
        self.assertIn("reasons", shared)
        self.assertIn("score", shared)
        self.assertIn("model-503", script)
        self.assertIn('data-session-action="relogin"', script)
        self.assertIn('data-session-action="logout"', script)
        self.assertIn("/api/v1/auth/logout", script)
        self.assertIn('"Content-Type": "application/json"', script)
        self.assertIn('body: "{}"', script)
        self.assertIn("/login?return_to=", script)
        self.assertNotIn("coder_profile", script + shared)

    def test_recommendations_page_has_filters_and_states(self) -> None:
        html = self.asset("/recommendations").body.decode("utf-8")
        script = self.asset("/assets/recommendations.js").body.decode("utf-8")
        for identifier in ("issue-list", "run-context", "recommendation-message", "list-toolbar", "recommendation-search", "public-candidates", "project-opportunities", "session-actions"):
            self.assertIn(f'id="{identifier}"', html)
        for track in ("all", "newcomer", "growth"):
            self.assertIn(f'data-track="{track}"', html)
        for state in (
            "loading",
            "empty",
            "no-profile",
            "stale-preview",
            "model-503",
            "upstream",
            "version-mismatch",
            "migration-failure",
        ):
            self.assertIn(f'preview === "{state}"', script)
        self.assertIn("error.status === 401", script)
        self.assertIn("profile_required", script)

    def test_profile_page_keeps_real_flow_and_exposes_questionnaire(self) -> None:
        html = self.asset("/profile").body.decode("utf-8")
        script = self.asset("/assets/profile.js").body.decode("utf-8")
        session_nav = self.asset("/assets/session-nav.js").body.decode("utf-8")
        questionnaire = self.asset("/assets/questionnaire.js").body.decode("utf-8")
        self.assertIn('<header class="topbar">', html)
        self.assertIn('<nav class="nav"', html)
        self.assertIn("data-session-nav", html)
        self.assertIn('src="/assets/session-nav.js"', html)
        self.assertIn('href="/recommendations"', html)
        self.assertIn('href="/status"', html)
        self.assertIn("/api/v1/auth/logout", script)
        self.assertIn("handleLogout", script)
        self.assertIn("/api/v1/me", session_nav)
        self.assertIn("/api/v1/auth/logout", session_nav)
        self.assertIn('body: "{}"', session_nav)
        self.assertIn('id="profile-questionnaire"', html)
        self.assertIn('id="questionnaire-form"', html)
        self.assertIn('id="questionnaire-preview"', html)
        self.assertIn("/api/v1/me/profile/questionnaire", questionnaire)
        self.assertIn("/api/v1/me/profile/assessment-preview", questionnaire)
        self.assertIn("/api/v1/me/profile/assessment-confirm", questionnaire)
        self.assertIn("idempotency_key", questionnaire)

    def test_status_page_uses_real_status_and_run_endpoints(self) -> None:
        html = self.asset("/status").body.decode("utf-8")
        script = self.asset("/assets/status.js").body.decode("utf-8")
        for identifier in ("status-grid", "quality-grid", "feedback-grid", "run-grid", "status-matrix"):
            self.assertIn(f'id="{identifier}"', html)
        self.assertIn("/api/v1/status", script)
        self.assertIn("/api/v1/feedback/summary", script)
        self.assertIn("/api/v1/recommendation-runs/", script)
        self.assertIn("rule_legacy", script)
        self.assertIn("version-mismatch", script)
        self.assertIn("migration-failure", script)

    def test_task_page_is_allowlisted_and_wired(self) -> None:
        html = self.asset("/task").body.decode("utf-8")
        script = self.asset("/assets/task.js").body.decode("utf-8")
        for identifier in ("task-body", "task-message", "refresh-task", "feedback-actions", "task-github"):
            self.assertIn(f'id="{identifier}"', html)
        for state in ("interested", "not_suitable", "started", "completed"):
            self.assertIn(f'data-feedback="{state}"', html)
        self.assertIn("/api/v1/tasks/", script)
        self.assertIn("refresh=true", script)
        self.assertIn("/api/v1/feedback", script)
        self.assertIn("任务不存在或已失效", script)
        self.assertNotIn("ISSUES[0]", script)

    def test_recommendation_run_page_is_allowlisted_and_wired(self) -> None:
        html = self.asset("/recommendation-run").body.decode("utf-8")
        script = self.asset("/assets/run.js").body.decode("utf-8")
        for identifier in ("run-summary", "profile-summary", "run-warnings", "run-items"):
            self.assertIn(f'id="{identifier}"', html)
        self.assertIn("/api/v1/recommendation-runs/", script)
        self.assertIn("profile_snapshot_hash", script)

    def test_static_router_is_allowlist_based(self) -> None:
        self.assertIsNone(load_static_asset(self.root, "/../pyproject.toml"))
        self.assertIsNone(load_static_asset(self.root, "/assets/unknown.js"))
        for path, content_type in (
            ("/assets/app.css", "text/css; charset=utf-8"),
            ("/assets/ui.js", "text/javascript; charset=utf-8"),
            ("/assets/run.js", "text/javascript; charset=utf-8"),
            ("/assets/questionnaire.js", "text/javascript; charset=utf-8"),
            ("/assets/session-nav.js", "text/javascript; charset=utf-8"),
        ):
            asset = self.asset(path)
            self.assertEqual(content_type, asset.content_type)

    def test_css_has_motion_and_mobile_guards(self) -> None:
        css = self.asset("/assets/app.css").body.decode("utf-8")
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn("@media (max-width:560px)", css)
        self.assertIn(".run-context", css)
        self.assertIn(".repo-mark", css)
        self.assertIn(".session-actions", css)
        self.assertIn(".session-action--danger", css)


if __name__ == "__main__":
    unittest.main()
