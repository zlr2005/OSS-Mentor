"""Bounded, public-only GitHub evidence collection for the signed-in user."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import quote

CONSENT_VERSION = "profile-import-consent-v0.1"
MAX_REPOSITORIES = 10
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
REPOSITORY_NAME = re.compile(r"^[A-Za-z0-9-]{1,39}/(?!\.\.?$)[A-Za-z0-9_.-]{1,100}$")


class GitHubProfileError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward authorization to a redirect target from an upstream response.
        return None


class GitHubProfileSource:
    """Sample public repositories/events, never treat it as a full contribution history."""

    def __init__(self, *, opener=None) -> None:
        self._opener = opener or urllib.request.build_opener(_NoRedirect())

    def _get(self, path: str, token: str | None = None):
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "oss-mentor",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request("https://api.github.com" + path, headers=headers)
        try:
            with self._opener.open(request, timeout=10) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise GitHubProfileError(502, "github_upstream_error", "GitHub response exceeds limit")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            # Do not expose upstream bodies, request headers, URLs or tokens.
            if exc.code == 429 or (exc.code == 403 and
                                  (exc.headers.get("X-RateLimit-Remaining") == "0" or
                                   exc.headers.get("Retry-After"))):
                raise GitHubProfileError(429, "rate_limited", "GitHub rate limit reached") from None
            if exc.code == 401:
                raise GitHubProfileError(401, "authentication_required", "GitHub authorization expired; log in again") from None
            if exc.code == 403:
                raise GitHubProfileError(403, "insufficient_permission", "GitHub access is not permitted") from None
            raise GitHubProfileError(502, "github_upstream_error", "GitHub request failed") from None
        except (urllib.error.URLError, OSError, ValueError, UnicodeError):
            raise GitHubProfileError(502, "github_upstream_error", "GitHub response unavailable or invalid") from None

    def collect(self, *, access_token: str, github_user_id: int, consent_version: str) -> dict:
        if consent_version != CONSENT_VERSION:
            raise ValueError("the current public-data import consent is required")
        user = self._get("/user", access_token)
        if not isinstance(user, dict) or type(user.get("id")) is not int:
            raise GitHubProfileError(502, "github_upstream_error", "Invalid GitHub identity response")
        if user["id"] != github_user_id:
            raise GitHubProfileError(403, "insufficient_permission", "GitHub identity does not match session")
        login = user.get("login")
        if not isinstance(login, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,39}", login):
            raise GitHubProfileError(502, "github_upstream_error", "Invalid GitHub identity response")
        # These endpoints are public. Repository detail/language requests deliberately
        # carry NO token, so a newly private repository cannot be read even with a
        # broader-than-required OAuth grant.
        encoded_login = quote(login, safe="")
        repositories = self._get(f"/users/{encoded_login}/repos?type=owner&sort=pushed&per_page=10")
        events = self._get(f"/users/{encoded_login}/events/public?per_page=100")
        if not isinstance(repositories, list) or not isinstance(events, list):
            raise GitHubProfileError(502, "github_upstream_error", "Invalid GitHub collection response")
        repositories, events = repositories[:10], events[:100]
        public_events = [
            event for event in events
            if isinstance(event, dict) and event.get("public") is True
            and isinstance(event.get("actor"), dict) and event["actor"].get("id") == github_user_id
            and isinstance(event.get("repo"), dict)
            and isinstance(event["repo"].get("name"), str)
            and REPOSITORY_NAME.fullmatch(event["repo"]["name"])
        ]
        public_events.sort(key=lambda event: str(event.get("created_at", "")), reverse=True)
        names = list(dict.fromkeys(event["repo"]["name"] for event in public_events))
        names.extend(
            repo["full_name"] for repo in repositories
            if isinstance(repo, dict) and repo.get("private") is False
            and isinstance(repo.get("full_name"), str) and REPOSITORY_NAME.fullmatch(repo["full_name"])
        )
        names = list(dict.fromkeys(names))[:MAX_REPOSITORIES]
        rows = []
        for name in names:
            path = "/repos/" + quote(name, safe="/")
            # Fail unavailable upstream requests rather than persist an apparently
            # complete import. A newly private repository is inaccessible without auth.
            repo = self._get(path)
            if not isinstance(repo, dict) or repo.get("private") is not False or repo.get("archived") is True:
                continue
            languages = self._get(path + "/languages")
            if not isinstance(languages, dict) or any(
                not isinstance(key, str) or type(value) is not int or value < 0
                for key, value in languages.items()
            ):
                raise GitHubProfileError(502, "github_upstream_error", "Invalid GitHub language response")
            counts = {"commits": 0, "pull_requests": 0, "issues": 0, "reviews": 0}
            times, seen = [], set()
            for event in public_events:
                if event["repo"]["name"] != name or event.get("id") in seen:
                    continue
                seen.add(event.get("id"))
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    continue
                kind, action = event.get("type"), payload.get("action")
                if kind == "PullRequestEvent" and action == "opened":
                    counts["pull_requests"] += 1
                elif kind == "IssuesEvent" and action == "opened":
                    counts["issues"] += 1
                elif kind == "PullRequestReviewEvent" and action == "created":
                    counts["reviews"] += 1
                elif kind == "PushEvent":
                    # Modern public PushEvent can omit commit counts. Do NOT invent
                    # one commit per event or attribute the whole repository history.
                    size = payload.get("size")
                    if type(size) is int and size >= 0:
                        counts["commits"] += size
                else:
                    continue
                timestamp = event.get("created_at")
                try:
                    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    if parsed.tzinfo is not None:
                        times.append(parsed.astimezone(timezone.utc))
                except (AttributeError, ValueError):
                    continue
            rows.append({
                "full_name": name, "private": False, "archived": False,
                "languages": languages, "contributions": counts, "contributed_paths": [],
                "first_contribution_at": min(times).isoformat() if times else None,
                "last_contribution_at": max(times).isoformat() if times else None,
            })
        return {
            "schema_version": "github-profile-input-v0.1",
            "observed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "consent_version": consent_version,
            "user": {"login": login, "name": user.get("name") if isinstance(user.get("name"), str) else None},
            "repositories": rows,
            "collection": {
                "mode": "bounded_public_sample", "repository_limit": MAX_REPOSITORIES,
                "event_limit": 100, "history_complete": False,
                "limitations": ["Public events are a limited, delayed sample, not lifetime totals.",
                               "Missing commit counts and contributed file paths are not inferred.",
                               "Language bytes describe repositories, not authored code or proficiency."],
            },
        }
