from __future__ import annotations

import unittest
from html.parser import HTMLParser
from pathlib import Path

from oss_mentor.api import load_static_asset


class IdCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()

    def handle_starttag(self, tag, attrs) -> None:
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.add(attributes["id"])


class WebUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1] / "web"

    def test_page_contains_profile_and_recommendation_regions(self) -> None:
        asset = load_static_asset(self.root, "/")
        self.assertIsNotNone(asset)
        parser = IdCollector()
        parser.feed(asset.body.decode("utf-8"))
        self.assertTrue(
            {
                "profile-select",
                "quick-language",
                "quick-os",
                "custom-mode",
                "custom-fields",
                "custom-track",
                "recommend-button",
                "recommendation-list",
                "result-count",
                "message",
                "inventory-status",
            }.issubset(parser.ids)
        )

    def test_javascript_calls_api_and_renders_required_fields(self) -> None:
        script = load_static_asset(self.root, "/assets/app.js")
        self.assertIsNotNone(script)
        text = script.body.decode("utf-8")
        self.assertIn("/api/v1/profiles", text)
        self.assertIn("/api/v1/recommendations", text)
        self.assertIn("/api/v1/recommendations/custom", text)
        self.assertIn("/api/v1/recommendation-options", text)
        self.assertIn("/api/v1/feedback", text)
        self.assertIn("buildQuickProfile", text)
        self.assertIn("data-feedback-state", text)
        self.assertIn("profile_persisted", (Path(__file__).resolve().parents[1] / "src" / "oss_mentor" / "api.py").read_text(encoding="utf-8"))
        self.assertIn("match_score", text)
        self.assertIn("skill_gaps", text)
        self.assertIn("为什么推荐", text)

        html = load_static_asset(self.root, "/").body.decode("utf-8")
        self.assertIn('data-skill="typescript"', html)
        self.assertIn('data-skill="java"', html)
        self.assertIn('data-skill="go"', html)
        self.assertIn('data-skill="rust"', html)

    def test_static_router_is_allowlist_based(self) -> None:
        self.assertIsNone(load_static_asset(self.root, "/../pyproject.toml"))
        self.assertIsNone(load_static_asset(self.root, "/assets/unknown.js"))
        css = load_static_asset(self.root, "/assets/styles.css")
        self.assertEqual("text/css; charset=utf-8", css.content_type)
        self.assertIn("@media (max-width: 640px)", css.body.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
