from __future__ import annotations

import io
import json
import unittest
import urllib.error
from urllib.parse import urlsplit

from oss_mentor.developer_profiles import build_github_profile_import
from oss_mentor.services.github_profile_source import (
    CONSENT_VERSION, MAX_RESPONSE_BYTES, GitHubProfileError, GitHubProfileSource, _NoRedirect,
)


class Opener:
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []

    def open(self, request, timeout):
        self.calls.append(request)
        path = urlsplit(request.full_url).path
        value = self.payloads[path]
        if isinstance(value, Exception):
            raise value
        return io.BytesIO(value if isinstance(value, bytes) else json.dumps(value).encode())


def event(identifier, kind, payload, **extra):
    return {"id": identifier, "type": kind, "payload": payload, "public": True,
            "actor": {"id": 101}, "repo": {"name": "fixture-dev/code"},
            "created_at": "2026-09-20T12:00:00Z", **extra}


class GitHubProfileSourceTests(unittest.TestCase):
    def setUp(self):
        self.payloads = {
            "/user": {"id": 101, "login": "fixture-dev", "name": "Fixture", "email": "never-copy@example.invalid"},
            "/users/fixture-dev/repos": [
                {"full_name": "fixture-dev/code", "private": False},
                {"full_name": "fixture-dev/private", "private": True},
            ],
            "/users/fixture-dev/events/public": [
                event("1", "PushEvent", {"size": 3, "commits": [{"message": "do not store raw text"}]}),
                event("2", "PullRequestEvent", {"action": "opened", "pull_request": {"body": "do not store"}}),
                event("3", "IssuesEvent", {"action": "opened"}),
                event("4", "PullRequestReviewEvent", {"action": "created"}),
                event("5", "PushEvent", {"head": "no-count-do-not-invent"}),
                event("6", "PushEvent", {"size": 999}, public=False),
                event("7", "PushEvent", {"size": 999}, actor={"id": 999}),
                event("8", "IssuesEvent", {"action": "closed"}),
            ],
            "/repos/fixture-dev/code": {"private": False, "archived": False},
            "/repos/fixture-dev/code/languages": {"Python": 1000, "Shell": 100},
        }
        self.opener = Opener(self.payloads)
        self.source = GitHubProfileSource(opener=self.opener)

    def collect(self):
        return self.source.collect(access_token="fixture-token", github_user_id=101, consent_version=CONSENT_VERSION)

    def test_public_only_sanitization_and_real_domain_conversion(self):
        payload = self.collect()
        self.assertEqual({"commits": 3, "pull_requests": 1, "issues": 1, "reviews": 1},
                         payload["repositories"][0]["contributions"])
        self.assertEqual([], payload["repositories"][0]["contributed_paths"])
        for secret in ("fixture-token", "never-copy", "do not store", "fixture-dev/private"):
            self.assertNotIn(secret, json.dumps(payload))
        self.assertEqual("Bearer fixture-token", self.opener.calls[0].get_header("Authorization"))
        self.assertTrue(all(request.get_header("Authorization") is None for request in self.opener.calls[1:]))
        self.assertTrue(all(urlsplit(request.full_url).netloc == "api.github.com" for request in self.opener.calls))
        imported = build_github_profile_import(payload)
        self.assertEqual(3, imported["activity_summary"]["commits"])
        self.assertFalse(payload["collection"]["history_complete"])

    def test_consent_and_identity_checked_before_collecting(self):
        with self.assertRaises(ValueError):
            self.source.collect(access_token="fixture-token", github_user_id=101, consent_version="old")
        self.assertEqual([], self.opener.calls)
        self.payloads["/user"]["id"] = 102
        with self.assertRaises(GitHubProfileError) as caught:
            self.collect()
        self.assertEqual(403, caught.exception.status)
        self.assertEqual(1, len(self.opener.calls))

    def test_private_and_unknown_visibility_never_get_language_requests(self):
        for repo in ({"private": True}, {}, {"private": False, "archived": True}):
            self.opener.calls.clear()
            self.payloads["/repos/fixture-dev/code"] = repo
            self.assertEqual([], self.collect()["repositories"])
            self.assertFalse(any(request.full_url.endswith("/languages") for request in self.opener.calls))

    def test_invalid_upstream_json_shape_timeout_and_size_are_sanitized(self):
        for value in (b"not JSON fixture-token", b"x" * (MAX_RESPONSE_BYTES + 1),
                      urllib.error.URLError("fixture-token"), []):
            self.payloads["/user"] = value
            with self.assertRaises(GitHubProfileError) as caught:
                self.collect()
            self.assertIn(caught.exception.status, (403, 502))
            self.assertNotIn("fixture-token", str(caught.exception))

    def test_http_error_mapping_does_not_echo_upstream_body(self):
        for upstream, headers, status, code in [
            (401, {}, 401, "authentication_required"),
            (403, {}, 403, "insufficient_permission"),
            (403, {"X-RateLimit-Remaining": "0"}, 429, "rate_limited"),
            (403, {"Retry-After": "60"}, 429, "rate_limited"),
            (429, {}, 429, "rate_limited"), (500, {}, 502, "github_upstream_error"),
            (302, {}, 502, "github_upstream_error"),
        ]:
            self.payloads["/user"] = urllib.error.HTTPError(
                "https://api.github.com/user", upstream, "fixture-token", headers, io.BytesIO(b"fixture-token")
            )
            with self.assertRaises(GitHubProfileError) as caught:
                self.collect()
            self.assertEqual((status, code), (caught.exception.status, caught.exception.code))
            self.assertNotIn("fixture-token", str(caught.exception))

    def test_redirect_handler_never_forwards_authorization(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "", {}, "https://foreign.example"))

    def test_empty_public_history_is_valid(self):
        self.payloads["/users/fixture-dev/repos"] = []
        self.payloads["/users/fixture-dev/events/public"] = []
        payload = self.collect()
        self.assertEqual([], payload["repositories"])
        self.assertEqual(0, build_github_profile_import(payload)["activity_summary"]["commits"])

    def test_repository_cap_and_event_deduplication(self):
        events = self.payloads["/users/fixture-dev/events/public"]
        events.append(events[0])
        repos = self.payloads["/users/fixture-dev/repos"]
        for number in range(20):
            name = f"fixture-dev/repo{number}"
            repos.append({"full_name": name, "private": False})
            self.payloads["/repos/" + name] = {"private": False}
            self.payloads["/repos/" + name + "/languages"] = {}
        payload = self.collect()
        # One of the first ten repository rows is private and must be discarded.
        self.assertEqual(9, len(payload["repositories"]))
        self.assertEqual(3, payload["repositories"][0]["contributions"]["commits"])


if __name__ == "__main__":
    unittest.main()
