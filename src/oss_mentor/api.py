"""Dependency-free read-only HTTP API for the OSS-Mentor MVP."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

from oss_mentor.developer_profiles import (
    ALLOWED_LANGUAGES,
    ALLOWED_OPERATING_SYSTEMS,
    ALLOWED_TASK_TYPES,
    CUSTOM_PROFILE_VERSION,
    custom_profile_for_matching,
)
from oss_mentor.matching import (
    MATCH_VERSION_V2,
    rank_for_profile,
    recommendation_availability,
)
from oss_mentor.services.auth_service import (
    AuthService,
    GitHubAuthError,
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
)
from oss_mentor.sqlite_store import SQLiteCandidateStore


API_VERSION = "v0.5"
MAX_JSON_BODY_BYTES = 32 * 1024
FEEDBACK_STATES = {"interested", "not_suitable", "started", "completed"}
PROFILE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


@dataclass(frozen=True, slots=True)
class ApiResponse:
    status: int
    body: dict[str, Any]
    cookies: tuple[tuple[str, str, dict[str, str | None]], ...] = ()


@dataclass(frozen=True, slots=True)
class StaticAsset:
    body: bytes
    content_type: str


_STATIC_ROUTES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/login": ("login.html", "text/html; charset=utf-8"),
    "/status": ("status.html", "text/html; charset=utf-8"),
    "/assets/styles.css": ("assets/styles.css", "text/css; charset=utf-8"),
    "/assets/app.js": ("assets/app.js", "text/javascript; charset=utf-8"),
    "/assets/status.js": ("assets/status.js", "text/javascript; charset=utf-8"),
}


def load_static_asset(static_root: Path | None, path: str) -> StaticAsset | None:
    route = _STATIC_ROUTES.get(path)
    if static_root is None or route is None:
        return None
    relative_path, content_type = route
    source = static_root.resolve() / relative_path
    if not source.is_file():
        return None
    return StaticAsset(source.read_bytes(), content_type)


class RecommendationApi:
    """Transport-independent API routes, suitable for unit testing."""

    def __init__(
        self,
        store: SQLiteCandidateStore,
        auth_service: AuthService | None = None,
    ) -> None:
        self.store = store
        self.auth_service = auth_service

    @staticmethod
    def _error(status: int, code: str, message: str) -> ApiResponse:
        return ApiResponse(status, {"error": {"code": code, "message": message}})

    def _recommendations(
        self,
        profile: dict[str, Any],
        *,
        limit: int,
        feedback_context: str | None = None,
    ) -> ApiResponse:
        matches = rank_for_profile(
            profile, self.store.matchable_candidates(), limit=limit
        )
        feedback_states = (
            self.store.feedback_states(
                feedback_context,
                [match.task_candidate_id for match in matches],
            )
            if feedback_context
            else {}
        )
        return ApiResponse(
            200,
            {
                "profile": {
                    "profile_key": profile["profile_key"],
                    "display_name": profile["display_name"],
                    "service_track": profile["service_track"],
                    "profile_source": profile.get("profile_source", "stored"),
                },
                "items": [
                    {
                        **asdict(match),
                        "feedback_state": feedback_states.get(match.task_candidate_id),
                    }
                    for match in matches
                ],
                "count": len(matches),
                "feedback_context": feedback_context,
                "api_version": API_VERSION,
            },
        )

    def _validate_feedback_context(self, value: Any) -> tuple[str, str]:
        if not isinstance(value, str) or not 1 <= len(value) <= 140:
            raise ValueError("feedback_context is invalid")
        if value.startswith("preset:"):
            profile_key = value.removeprefix("preset:")
            if not PROFILE_KEY_PATTERN.fullmatch(profile_key):
                raise ValueError("feedback_context is invalid")
            try:
                profile = self.store.profile_for_matching(profile_key)
            except ValueError as exc:
                raise ValueError("feedback_context profile was not found") from exc
            track = "newcomer" if profile["service_track"] == "newcomer" else "growth"
            return value, track
        parts = value.split(":")
        if len(parts) == 3 and parts[0] == "custom" and parts[2] in {"newcomer", "growth"}:
            try:
                client_id = UUID(parts[1])
            except (ValueError, AttributeError) as exc:
                raise ValueError("feedback_context is invalid") from exc
            canonical = f"custom:{client_id}:{parts[2]}"
            if canonical != value:
                raise ValueError("feedback_context is invalid")
            return value, parts[2]
        raise ValueError("feedback_context is invalid")

    def handle(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]] | None = None,
        body: Any = None,
        cookies: dict[str, str] | None = None,
        request_id: str | None = None,
    ) -> ApiResponse:
        request_id = request_id or str(uuid4())
        response = self._dispatch(method, path, query, body, cookies or {})
        body_out = {**response.body, "request_id": request_id}
        if "error" in body_out:
            body_out["error"]["request_id"] = request_id
        return ApiResponse(response.status, body_out, response.cookies)

    def _dispatch(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]] | None = None,
        body: Any = None,
        cookies: dict[str, str] | None = None,
    ) -> ApiResponse:
        query = query or {}
        cookies = cookies or {}
        if method == "GET" and path == "/health":
            return ApiResponse(
                200,
                {
                    "status": "ok",
                    "api_version": API_VERSION,
                    "database_ready": self.store.database_path.is_file(),
                },
            )
        if method == "GET" and path == "/api/v1/profiles":
            return ApiResponse(
                200,
                {
                    "items": self.store.list_profiles_public(),
                    "api_version": API_VERSION,
                },
            )
        if method == "GET" and path == "/api/v1/feedback/summary":
            return ApiResponse(
                200,
                {
                    "summary": self.store.feedback_summary(),
                    "api_version": API_VERSION,
                },
            )
        if method == "GET" and path == "/api/v1/recommendations":
            profile_key = (query.get("profile_key") or [""])[0].strip()
            if not profile_key:
                return self._error(
                    400, "missing_profile_key", "profile_key query parameter is required"
                )
            raw_limit = (query.get("limit") or ["10"])[0]
            try:
                limit = int(raw_limit)
            except ValueError:
                return self._error(400, "invalid_limit", "limit must be an integer")
            if not 1 <= limit <= 100:
                return self._error(400, "invalid_limit", "limit must be between 1 and 100")
            try:
                profile = self.store.profile_for_matching(profile_key)
            except ValueError:
                return self._error(404, "profile_not_found", "developer profile was not found")
            return self._recommendations(
                profile,
                limit=limit,
                feedback_context=f"preset:{profile_key}",
            )
        if method == "POST" and path == "/api/v1/recommendations/custom":
            if not isinstance(body, dict):
                return self._error(400, "invalid_body", "request body must be a JSON object")
            raw_limit = body.get("limit", 10)
            if isinstance(raw_limit, bool) or not isinstance(raw_limit, int):
                return self._error(400, "invalid_limit", "limit must be an integer")
            if not 1 <= raw_limit <= 100:
                return self._error(400, "invalid_limit", "limit must be between 1 and 100")
            try:
                profile = custom_profile_for_matching(body.get("profile"))
            except ValueError as exc:
                return self._error(400, "invalid_profile", str(exc))
            feedback_context = None
            client_id_value = body.get("feedback_client_id")
            if client_id_value is not None:
                try:
                    client_id = UUID(str(client_id_value))
                except (ValueError, AttributeError):
                    return self._error(
                        400, "invalid_feedback_client_id", "feedback_client_id must be a UUID"
                    )
                if str(client_id) != str(client_id_value):
                    return self._error(
                        400, "invalid_feedback_client_id", "feedback_client_id must use canonical UUID format"
                    )
                feedback_context = f"custom:{client_id}:{profile['service_track']}"
            response = self._recommendations(
                profile,
                limit=raw_limit,
                feedback_context=feedback_context,
            )
            response.body["custom_profile_version"] = CUSTOM_PROFILE_VERSION
            response.body["profile_persisted"] = False
            return response
        if method == "POST" and path == "/api/v1/recommendation-options":
            if not isinstance(body, dict):
                return self._error(400, "invalid_body", "request body must be a JSON object")
            try:
                profile = custom_profile_for_matching(body.get("profile"))
            except ValueError as exc:
                return self._error(400, "invalid_profile", str(exc))
            availability = recommendation_availability(
                profile,
                self.store.matchable_candidates(),
                languages=tuple(sorted(ALLOWED_LANGUAGES)),
                task_types=tuple(sorted(ALLOWED_TASK_TYPES)),
                operating_systems=tuple(sorted(ALLOWED_OPERATING_SYSTEMS)),
            )
            return ApiResponse(
                200,
                {
                    "availability": availability,
                    "api_version": API_VERSION,
                },
            )
        if method == "POST" and path == "/api/v1/feedback":
            if not isinstance(body, dict):
                return self._error(400, "invalid_body", "request body must be a JSON object")
            unknown = set(body) - {"task_candidate_id", "feedback_context", "feedback_state"}
            if unknown:
                return self._error(400, "invalid_feedback", "feedback contains unsupported fields")
            task_candidate_id = body.get("task_candidate_id")
            if (
                isinstance(task_candidate_id, bool)
                or not isinstance(task_candidate_id, int)
                or task_candidate_id < 1
            ):
                return self._error(
                    400, "invalid_task_candidate_id", "task_candidate_id must be a positive integer"
                )
            feedback_state = body.get("feedback_state")
            if feedback_state not in FEEDBACK_STATES:
                return self._error(
                    400,
                    "invalid_feedback_state",
                    "feedback_state must be interested, not_suitable, started, or completed",
                )
            try:
                feedback_context, service_track = self._validate_feedback_context(
                    body.get("feedback_context")
                )
            except ValueError as exc:
                return self._error(400, "invalid_feedback_context", str(exc))
            try:
                feedback = self.store.record_feedback(
                    task_candidate_id=task_candidate_id,
                    feedback_context=feedback_context,
                    service_track=service_track,
                    feedback_state=feedback_state,
                )
            except ValueError:
                return self._error(404, "task_not_found", "task candidate was not found")
            return ApiResponse(200, {"feedback": feedback, "api_version": API_VERSION})
        if method == "GET" and path == "/api/v1/status":
            try:
                status_data = self.store.system_status()
            except Exception:
                status_data = {
                    "database_ready": False,
                    "database_path": str(self.store.database_path),
                }
            return ApiResponse(
                200,
                {
                    **status_data,
                    "api_version": API_VERSION,
                    "match_version": MATCH_VERSION_V2,
                },
            )
        if method == "GET" and path == "/api/v1/auth/github/start":
            if self.auth_service is None:
                return self._error(503, "service_not_ready", "auth service is not configured")
            return_to = (query.get("return_to") or ["/"])[0]
            try:
                result = self.auth_service.start_oauth(return_to=return_to)
            except GitHubAuthError as exc:
                return self._error(400, "invalid_request", str(exc))
            if not result["oauth_configured"]:
                return ApiResponse(
                    200,
                    {
                        "oauth_configured": False,
                        "message": result["message"],
                    },
                )
            return ApiResponse(
                200,
                {
                    "oauth_configured": True,
                    "authorize_url": result["authorize_url"],
                    "state": result["state"],
                },
            )
        if method == "GET" and path == "/api/v1/auth/github/callback":
            if self.auth_service is None:
                return self._error(503, "service_not_ready", "auth service is not configured")
            code = (query.get("code") or [""])[0]
            state = (query.get("state") or [""])[0]
            if not code or not state:
                return self._error(400, "invalid_request", "code and state are required")
            try:
                result = self.auth_service.handle_callback(code=code, state=state)
            except GitHubAuthError as exc:
                return self._error(401, "authentication_required", str(exc))
            return ApiResponse(
                200,
                {
                    "user": result["user"],
                    "return_to": result["return_to"],
                },
                cookies=(
                    (
                        SESSION_COOKIE_NAME,
                        result["session_id"],
                        {
                            "Path": "/",
                            "HttpOnly": None,
                            "SameSite": "Lax",
                            "Max-Age": str(SESSION_TTL_SECONDS),
                        },
                    ),
                ),
            )
        if method == "POST" and path == "/api/v1/auth/logout":
            if self.auth_service is not None:
                self.auth_service.logout(cookies.get(SESSION_COOKIE_NAME))
            return ApiResponse(
                200,
                {"status": "ok"},
                cookies=(
                    (
                        SESSION_COOKIE_NAME,
                        "",
                        {"Path": "/", "HttpOnly": None, "SameSite": "Lax", "Max-Age": "0"},
                    ),
                ),
            )
        if method == "GET" and path == "/api/v1/me":
            if self.auth_service is None:
                return self._error(503, "service_not_ready", "auth service is not configured")
            user = self.auth_service.current_user(cookies.get(SESSION_COOKIE_NAME))
            if user is None:
                return self._error(401, "authentication_required", "login is required")
            return ApiResponse(200, {"user": user})
        if path in {
            "/health",
            "/api/v1/profiles",
            "/api/v1/recommendations",
            "/api/v1/recommendations/custom",
            "/api/v1/recommendation-options",
            "/api/v1/feedback",
            "/api/v1/feedback/summary",
            "/api/v1/status",
            "/api/v1/auth/github/start",
            "/api/v1/auth/github/callback",
            "/api/v1/auth/logout",
            "/api/v1/me",
        }:
            return self._error(405, "method_not_allowed", "method is not supported for this route")
        return self._error(404, "not_found", "route was not found")


def make_handler(
    api: RecommendationApi,
    *,
    cors_origin: str | None = None,
    static_root: Path | None = None,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "OSS-Mentor-MVP/0.4"

        def _parse_cookies(self) -> dict[str, str]:
            cookies: dict[str, str] = {}
            raw = self.headers.get("Cookie", "")
            for part in raw.split(";"):
                if "=" in part:
                    name, _, value = part.partition("=")
                    cookies[name.strip()] = value.strip()
            return cookies

        def _send_json(self, response: ApiResponse, *, allow: str | None = None) -> None:
            encoded = json.dumps(
                response.body, ensure_ascii=False, sort_keys=True
            ).encode("utf-8")
            self.send_response(response.status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if allow:
                self.send_header("Allow", allow)
            if cors_origin:
                self.send_header("Access-Control-Allow-Origin", cors_origin)
                self.send_header("Vary", "Origin")
            for name, value, attributes in response.cookies:
                parts = [f"{name}={value}"]
                for attribute, attribute_value in attributes.items():
                    parts.append(
                        attribute if attribute_value is None else f"{attribute}={attribute_value}"
                    )
                self.send_header("Set-Cookie", "; ".join(parts))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            parsed = urlsplit(self.path)
            asset = load_static_asset(static_root, parsed.path)
            if asset is not None:
                self.send_response(200)
                self.send_header("Content-Type", asset.content_type)
                self.send_header("Content-Length", str(len(asset.body)))
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
                    "frame-ancestors 'none'",
                )
                self.end_headers()
                self.wfile.write(asset.body)
                return
            response = api.handle(
                "GET",
                parsed.path,
                parse_qs(parsed.query),
                cookies=self._parse_cookies(),
                request_id=str(uuid4()),
            )
            self._send_json(response)

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
            path = urlsplit(self.path).path
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
            if content_type != "application/json":
                self._send_json(
                    api._error(415, "unsupported_media_type", "Content-Type must be application/json")
                )
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                content_length = -1
            if content_length < 0:
                self._send_json(api._error(400, "invalid_content_length", "invalid Content-Length"))
                return
            if content_length > MAX_JSON_BODY_BYTES:
                self._send_json(api._error(413, "body_too_large", "request body is too large"))
                return
            try:
                payload = json.loads(self.rfile.read(content_length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._send_json(api._error(400, "invalid_json", "request body contains invalid JSON"))
                return
            response = api.handle(
                "POST",
                path,
                body=payload,
                cookies=self._parse_cookies(),
                request_id=str(uuid4()),
            )
            self._send_json(response, allow="POST")

        def log_message(self, format: str, *args: object) -> None:
            # Keep standard access logging, but never include request bodies or headers.
            super().log_message(format, *args)

    return Handler


def serve(
    store: SQLiteCandidateStore,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    cors_origin: str | None = None,
    static_root: Path | None = None,
    auth_service: AuthService | None = None,
) -> None:
    api = RecommendationApi(store, auth_service=auth_service)
    server = ThreadingHTTPServer(
        (host, port),
        make_handler(api, cors_origin=cors_origin, static_root=static_root),
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
