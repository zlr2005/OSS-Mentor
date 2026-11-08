"""Dependency-free HTTP API for OSS-Mentor."""

from __future__ import annotations

import copy
import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

from oss_mentor.candidate_sync import CandidateDetail, CandidateService
from oss_mentor.collector.github_client import GitHubApiError, RateLimitExceeded
from oss_mentor.contracts_midterm import (
    IdempotencyConflictError,
    ProjectRecommendationItem,
    PublicCandidateItem,
    StalePlanError,
)
from oss_mentor.developer_profiles import (
    ALLOWED_LANGUAGES,
    ALLOWED_OPERATING_SYSTEMS,
    ALLOWED_TASK_TYPES,
    CUSTOM_PROFILE_VERSION,
    custom_profile_for_matching,
)
from oss_mentor.matching import (
    MATCH_VERSION_V2,
    MATCH_VERSION_V3,
    recommendation_availability,
)
from oss_mentor.services.auth_service import (
    AuthService,
    GitHubAuthError,
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
)
from oss_mentor.sqlite_store import SQLiteCandidateStore
from oss_mentor.services.profile_service import ProfileService, as_developer_profile_v2
from oss_mentor.services.recommendation_service import (
    ModelUnavailableError,
    RecommendationService,
    feedback_context_for_user,
)
from oss_mentor.services.github_profile_source import (
    CONSENT_VERSION, GitHubProfileError, GitHubProfileSource,
)
from oss_mentor.services.midterm_profile_service import (
    MidtermProfileNotConfirmedError,
    MidtermProfileRequiredError,
    MidtermProfileService,
    SQLiteMidtermProfileApiStore,
    verified_developer_key,
)
from oss_mentor.services.model_gateway import (
    ModelUnavailable,
    current_model_metadata,
    ProfileAwareScorer,
    ShadowProfileScorer,
    scorer_with_profile,
)
from oss_mentor.storage.profiles import SQLiteProfileStorage
from oss_mentor.storage.recommendations import SQLiteRecommendationStorage
from oss_mentor.storage.candidates import SQLiteCandidateStorage


API_VERSION = "v0.5"
MIDTERM_API_VERSION = "v0.6"
MAX_JSON_BODY_BYTES = 32 * 1024
FEEDBACK_STATES = {"interested", "not_suitable", "started", "completed"}
PROFILE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
TASK_DETAIL_PATH_PATTERN = re.compile(r"^/api/v1/tasks/([1-9][0-9]{0,18})$")
RECOMMENDATION_RUN_PATH_PATTERN = re.compile(
    r"^/api/v1/recommendation-runs/([A-Za-z0-9-]{1,80})$"
)
REFRESH_TRUE = {"1", "true", "yes"}
REFRESH_FALSE = {"0", "false", "no", ""}
MIDTERM_PROFILE_PATHS = {
    "/api/v1/me/profile/questionnaire",
    "/api/v1/me/profile/assessment-preview",
    "/api/v1/me/profile/assessment-confirm",
    "/api/v1/me/profile/enhanced-features",
}
CLIENT_OWNER_FIELDS = {"user_id", "profile_key", "developer_key", "base_profile_version"}


@dataclass(frozen=True, slots=True)
class ApiResponse:
    status: int
    body: dict[str, Any]
    cookies: tuple[tuple[str, str, dict[str, str | None]], ...] = ()


@dataclass(frozen=True, slots=True)
class StaticAsset:
    body: bytes
    content_type: str


class _RecommendationProfileAdapter:
    """Expose the contract loader expected by RecommendationService."""

    def __init__(self, profile_service: Any) -> None:
        self.profile_service = profile_service

    def profile_for_user_contract(self, user_id: int):
        profile = self.profile_service.profile_for_user(user_id)
        return as_developer_profile_v2(profile) if profile is not None else None


_STATIC_ROUTES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/login": ("login.html", "text/html; charset=utf-8"),
    "/status": ("status.html", "text/html; charset=utf-8"),
    "/profile": ("profile.html", "text/html; charset=utf-8"),
    "/profile.html": ("profile.html", "text/html; charset=utf-8"),
    "/recommendations": ("recommendations.html", "text/html; charset=utf-8"),
    "/recommendations.html": ("recommendations.html", "text/html; charset=utf-8"),
    "/recommendation-run": ("recommendation-run.html", "text/html; charset=utf-8"),
    "/recommendation-run.html": ("recommendation-run.html", "text/html; charset=utf-8"),
    "/task": ("task.html", "text/html; charset=utf-8"),
    "/task.html": ("task.html", "text/html; charset=utf-8"),
    "/assets/app.css": ("assets/app.css", "text/css; charset=utf-8"),
    "/assets/ui.js": ("assets/ui.js", "text/javascript; charset=utf-8"),
    "/assets/run.js": ("assets/run.js", "text/javascript; charset=utf-8"),
    "/assets/questionnaire.js": ("assets/questionnaire.js", "text/javascript; charset=utf-8"),
    "/assets/task.js": ("assets/task.js", "text/javascript; charset=utf-8"),
    "/assets/task.css": ("assets/task.css", "text/css; charset=utf-8"),
    "/assets/recommendations.js": ("assets/recommendations.js", "text/javascript; charset=utf-8"),
    "/assets/recommendations.css": ("assets/recommendations.css", "text/css; charset=utf-8"),
    "/assets/profile.js": ("assets/profile.js", "text/javascript; charset=utf-8"),
    "/assets/profile.css": ("assets/profile.css", "text/css; charset=utf-8"),
    "/assets/session-nav.js": ("assets/session-nav.js", "text/javascript; charset=utf-8"),
    "/assets/login.js": ("assets/login.js", "text/javascript; charset=utf-8"),
    "/assets/login.css": ("assets/login.css", "text/css; charset=utf-8"),
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


_PATH_HINT_PATTERN = re.compile(
    r"(?<![\w./-])("
    r"(?:src|packages|lib|app|tests|docs|providers|modules|pkg|internal|crates)"
    r"/[\w./-]+"
    r"|[\w.-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|kt|rb|md|yml|yaml|json|toml)"
    r")",
    re.IGNORECASE,
)


def _body_summary(value: Any, limit: int = 360) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    first = paragraphs[0] if paragraphs else text
    first = re.sub(r"^#{1,6}\s*", "", first)
    first = re.sub(r"\s+", " ", first)
    return first[:limit].strip()


def _impacted_paths(value: Any, limit: int = 6) -> list[str]:
    seen: list[str] = []
    for match in _PATH_HINT_PATTERN.findall(str(value or "")):
        path = match.strip("`'\"(),.;:")
        if path and path not in seen:
            seen.append(path)
        if len(seen) >= limit:
            break
    return seen


def _task_guidance(
    row: dict[str, Any],
    task: dict[str, Any] | None,
) -> dict[str, Any]:
    body = row.get("body_text")
    goal = _body_summary(body)
    paths = _impacted_paths(body)
    repository = str(row["repository"])
    has_guide = bool(task and task.get("has_contributing_guide"))
    return {
        "goal": goal,
        "goal_source": "issue_body_excerpt" if goal else "unavailable",
        "impacted_paths": paths,
        "impacted_paths_source": "body_text_heuristic" if paths else "unavailable",
        "steps": [
            "阅读上游 Issue 与评论，确认验收条件和当前是否仍可领取。",
            "查看仓库 CONTRIBUTING / 开发文档，完成本地环境与测试命令准备。",
            "定位相关模块并复现问题；改动前先确认已有测试与相邻实现。",
            "补充或更新测试，运行对应测试子集，再按仓库规范提交 PR。",
        ],
        "steps_source": "generic_guidance",
        "contributing_url": (
            f"https://github.com/{repository}/blob/HEAD/CONTRIBUTING.md"
            if has_guide
            else None
        ),
    }


def _candidate_detail_body(
    row: dict[str, Any],
    task: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Task detail straight from the local snapshot; never calls the network."""

    task = task or {}
    repository = str(row["repository"])
    requirements = [
        {
            "skill_name": str(item.get("skill_name") or ""),
            "minimum_level": int(item.get("minimum_level") or 0),
            "importance": float(item.get("importance") or 0),
        }
        for item in (task.get("requirements") or [])
        if item.get("skill_name")
    ]
    return {
        "task_candidate_id": int(row["task_candidate_id"]),
        "repository_full_name": repository,
        "repository_url": f"https://github.com/{repository}",
        "issue_number": int(row["issue_number"]),
        "title": str(row["title"]),
        "body_text": row.get("body_text"),
        "html_url": str(row["html_url"]),
        "availability": str(row["candidate_availability"]),
        "availability_reasons": list(row.get("availability_reasons") or []),
        "verified_at": row.get("github_verified_at"),
        "refreshed": False,
        "labels": list(task.get("labels") or []),
        "task_types": list(task.get("task_types") or []),
        "comment_count": int(
            task.get("comment_count") or row.get("comment_count") or 0
        ),
        "last_activity_at": task.get("last_activity_at")
        or row.get("last_activity_at"),
        "primary_language": task.get("primary_language")
        or row.get("primary_language"),
        "estimated_code_difficulty": task.get("estimated_code_difficulty"),
        "estimated_setup_difficulty": task.get("estimated_setup_difficulty"),
        "newcomer_label_signal": bool(task.get("newcomer_label_signal")),
        "skill_requirements": requirements,
        "guidance": _task_guidance(row, task),
    }


def _refreshed_candidate_detail_body(detail: CandidateDetail) -> dict[str, Any]:
    return {
        **asdict(detail),
        "availability_reasons": list(detail.availability_reasons),
    }


class RecommendationApi:
    """Transport-independent API routes, suitable for unit testing."""

    def __init__(
        self,
        store: SQLiteCandidateStore,
        auth_service: AuthService | None = None,
        profile_service: ProfileService | None = None,
        github_profile_source: GitHubProfileSource | None = None,
        recommendation_service: RecommendationService | None = None,
        refresh_worker=None,
        candidate_service: CandidateService | None = None,
        midterm_profile_store: SQLiteMidtermProfileApiStore | None = None,
        midterm_profile_service: MidtermProfileService | None = None,
        questionnaire_path: Path | None = None,
        advanced_adapter_mode: str = "off",
    ) -> None:
        if advanced_adapter_mode not in {"off", "shadow", "serve"}:
            raise ValueError("advanced_adapter_mode must be off, shadow or serve")
        self.advanced_adapter_mode = advanced_adapter_mode
        self.store = store
        self.refresh_worker = refresh_worker
        self.candidate_service = candidate_service
        self.candidate_storage: SQLiteCandidateStorage | None = None
        if isinstance(store, SQLiteCandidateStore):
            self.candidate_storage = SQLiteCandidateStorage(
                store.database_path, store.migration_path
            )
        self.auth_service = auth_service
        self.profile_service = profile_service
        if self.profile_service is None and isinstance(store, SQLiteCandidateStore):
            self.profile_service = ProfileService(
                SQLiteProfileStorage(store.database_path, store.migration_path)
            )
        self.github_profile_source = github_profile_source or GitHubProfileSource()
        recommendation_store = None
        if isinstance(store, SQLiteCandidateStore):
            recommendation_store = SQLiteRecommendationStorage(
                store.database_path, store.migration_path
            )
        recommendation_profile_service = self.profile_service
        if (
            recommendation_profile_service is not None
            and not callable(
                getattr(recommendation_profile_service, "profile_for_user_contract", None)
            )
        ):
            recommendation_profile_service = _RecommendationProfileAdapter(
                recommendation_profile_service
            )
        self.recommendation_service = recommendation_service or RecommendationService(
            candidate_store=store,
            profile_service=recommendation_profile_service,
            auth_service=self.auth_service,
            recommendation_store=recommendation_store,
        )
        if self.recommendation_service.auth_service is None:
            self.recommendation_service.auth_service = self.auth_service
        if self.recommendation_service.profile_service is None:
            self.recommendation_service.profile_service = recommendation_profile_service
        if self.recommendation_service.recommendation_store is None:
            self.recommendation_service.recommendation_store = recommendation_store
        self.midterm_profile_store = midterm_profile_store
        self.midterm_profile_service = midterm_profile_service
        if (
            self.midterm_profile_service is None
            and self.profile_service is not None
            and isinstance(store, SQLiteCandidateStore)
        ):
            self.midterm_profile_store = midterm_profile_store or SQLiteMidtermProfileApiStore(
                store.database_path,
                store.migration_path,
            )
            if questionnaire_path is None:
                questionnaire_path = (
                    Path(__file__).resolve().parents[2]
                    / "config/midterm_profile/questionnaire_v0.1.json"
                )
            self.midterm_profile_service = MidtermProfileService(
                store=self.midterm_profile_store,
                profile_service=self.profile_service,
                questionnaire_path=questionnaire_path,
            )
            if store.database_path.is_file():
                self.midterm_profile_store.initialize()
                self.midterm_profile_service.initialize()

    @staticmethod
    def _profile_body(profile: dict) -> dict:
        # Internal row/owner identifiers never become a client-selected identity.
        return {key: value for key, value in profile.items()
                if key not in {"user_id", "user_key", "developer_profile_id"}}

    def _is_public_profile(self, profile_key: str) -> bool:
        return any(item["profile_key"] == profile_key for item in self.store.list_profiles_public())

    def _midterm_profile_route(self, method, path, query, body, cookies) -> ApiResponse:
        allowed = "GET" if path in {
            "/api/v1/me/profile/questionnaire",
            "/api/v1/me/profile/enhanced-features",
        } else "POST"
        if method != allowed:
            return self._error(
                405,
                "method_not_allowed",
                "method is not supported for this route",
            )
        if self.auth_service is None:
            return self._error(503, "service_not_ready", "auth service is not configured")
        user = self.auth_service.current_user(cookies.get(SESSION_COOKIE_NAME))
        if user is None:
            return self._error(401, "authentication_required", "login is required")
        service = self.midterm_profile_service
        if service is None:
            return self._error(
                503,
                "service_not_ready",
                "midterm profile service is not configured",
            )
        try:
            service.initialize()
        except (RuntimeError, ValueError):
            return self._error(
                503,
                "service_not_ready",
                "midterm profile storage is not ready",
            )
        user_id = int(user["user_id"])

        if method == "GET":
            if path == "/api/v1/me/profile/questionnaire":
                if query:
                    return self._error(
                        400,
                        "invalid_request",
                        "questionnaire does not accept query parameters",
                    )
                return ApiResponse(
                    200,
                    {
                        "questionnaire": service.questionnaire_payload(),
                        "api_version": MIDTERM_API_VERSION,
                    },
                )
            unsupported = sorted(set(query) - {"profile_version"})
            if unsupported:
                return self._error(
                    400,
                    "invalid_request",
                    f"unsupported query parameter: {unsupported[0]}",
                )
            versions = query.get("profile_version") or []
            if len(versions) > 1:
                return self._error(
                    400,
                    "invalid_request",
                    "profile_version must be supplied at most once",
                )
            profile_version = versions[0].strip() if versions else None
            if versions and not profile_version:
                return self._error(
                    400,
                    "invalid_request",
                    "profile_version must be a non-empty string",
                )
            try:
                features = service.enhanced_features(
                    user_id=user_id,
                    profile_version=profile_version,
                    developer_key=verified_developer_key(user.get("github_login")),
                )
            except MidtermProfileRequiredError as exc:
                return self._error(404, "profile_required", str(exc))
            except MidtermProfileNotConfirmedError as exc:
                return self._error(404, "profile_not_confirmed", str(exc))
            except ValueError as exc:
                return self._error(422, "profile_validation_failed", str(exc))
            return ApiResponse(
                200,
                {
                    "features": features.to_dict(),
                    "api_version": MIDTERM_API_VERSION,
                },
            )

        if query:
            return self._error(
                400,
                "invalid_request",
                "assessment routes do not accept query parameters",
            )
        if not isinstance(body, dict):
            return self._error(400, "invalid_request", "request body must be a JSON object")
        if set(body) & CLIENT_OWNER_FIELDS:
            return self._error(
                400,
                "invalid_request",
                "identity and profile versions are supplied by the server",
            )
        try:
            if path == "/api/v1/me/profile/assessment-preview":
                if set(body) != {"questionnaire_version", "answers", "idempotency_key"}:
                    return self._error(
                        400,
                        "invalid_request",
                        "assessment preview requires questionnaire_version, answers and idempotency_key",
                    )
                result = service.create_preview(
                    user_id=user_id,
                    questionnaire_version=body["questionnaire_version"],
                    answers=body["answers"],
                    idempotency_key=body["idempotency_key"],
                )
                return ApiResponse(
                    200,
                    {**result, "api_version": MIDTERM_API_VERSION},
                )
            if path == "/api/v1/me/profile/assessment-confirm":
                if set(body) != {"plan_id", "decisions", "idempotency_key"}:
                    return self._error(
                        400,
                        "invalid_request",
                        "assessment confirmation requires plan_id, decisions and idempotency_key",
                    )
                confirmation = service.confirm(
                    user_id=user_id,
                    plan_id=body["plan_id"],
                    decisions=body["decisions"],
                    idempotency_key=body["idempotency_key"],
                )
                return ApiResponse(
                    200,
                    {
                        "confirmation": confirmation.to_dict(),
                        "api_version": MIDTERM_API_VERSION,
                    },
                )
        except MidtermProfileRequiredError as exc:
            return self._error(404, "profile_required", str(exc))
        except KeyError:
            return self._error(404, "not_found", "assessment plan or preview was not found")
        except (StalePlanError, IdempotencyConflictError) as exc:
            return self._error(409, "state_conflict", str(exc))
        except ValueError as exc:
            return self._error(422, "profile_validation_failed", str(exc))
        return self._error(404, "not_found", "route was not found")

    def _profile_route(self, method, path, query, body, cookies) -> ApiResponse:
        suggestion = re.fullmatch(
            r"/api/v1/me/profile/suggestions/([1-9][0-9]{0,18})/(accept|reject)", path
        )
        allowed = {"GET", "PUT"} if path == "/api/v1/me/profile" else {"POST"}
        if method not in allowed:
            return self._error(405, "method_not_allowed", "method is not supported for this route")
        if self.auth_service is None:
            return self._error(503, "service_not_ready", "auth service is not configured")
        session_id = cookies.get(SESSION_COOKIE_NAME)
        user = self.auth_service.current_user(session_id)
        if user is None:
            return self._error(401, "authentication_required", "login is required")
        if self.profile_service is None:
            return self._error(503, "service_not_ready", "profile storage is not configured")
        if query:
            return self._error(400, "invalid_request", "profile routes do not accept query parameters")
        if method != "GET" and not isinstance(body, dict):
            return self._error(400, "invalid_request", "request body must be a JSON object")
        service = self.profile_service
        try:
            current = service.profile_for_user(user["user_id"])
            if method == "PUT":
                profile = service.update_owned_profile(user["user_id"], body)
                return ApiResponse(200, {"profile": self._profile_body(profile), "api_version": API_VERSION})
            if current is None:
                return self._error(404, "not_found", "create a profile before importing or resolving suggestions")
            if method == "GET":
                return ApiResponse(200, {
                    "profile": self._profile_body(current),
                    "suggestions": service.storage.list_suggestions(profile_key=current["profile_key"]),
                    "consent_version": CONSENT_VERSION, "api_version": API_VERSION,
                })
            if path.endswith("/import-github"):
                if set(body) != {"consent_version"} or body["consent_version"] != CONSENT_VERSION:
                    return self._error(422, "profile_validation_failed", "current public-data import consent is required")
                credential = self.auth_service.github_credential(session_id)
                if credential is None:
                    return self._error(503, "service_not_ready", "GitHub credential unavailable; log in again")
                token, github_user_id = credential
                payload = self.github_profile_source.collect(
                    access_token=token, github_user_id=github_user_id, consent_version=CONSENT_VERSION
                )
                # Network work runs outside SQLite's write transaction. Recheck the
                # session afterwards, then bind the import to its current owned profile.
                if self.auth_service.current_user(session_id) is None:
                    return self._error(401, "authentication_required", "session expired during import")
                with service.storage.transaction() as storage:
                    fresh = storage.profile_for_user(user["user_id"])
                    if fresh is None:
                        return self._error(404, "not_found", "profile was not found")
                    result = ProfileService(storage).import_github_profile(
                        profile_key=fresh["profile_key"], github_payload=payload
                    )
                return ApiResponse(200, {**result, "collection": payload.get("collection", {}),
                                         "api_version": API_VERSION})
            if suggestion:
                if body:
                    return self._error(400, "invalid_request", "suggestion decisions require an empty JSON object")
                with service.storage.transaction() as storage:
                    fresh = storage.profile_for_user(user["user_id"])
                    if fresh is None:
                        return self._error(404, "not_found", "profile was not found")
                    scoped = ProfileService(storage)
                    resolve = scoped.accept_suggestion if suggestion[2] == "accept" else scoped.reject_suggestion
                    result = resolve(profile_key=fresh["profile_key"], suggestion_id=int(suggestion[1]))
                result["profile"] = self._profile_body(result["profile"])
                return ApiResponse(200, {**result, "api_version": API_VERSION})
        except GitHubProfileError as exc:
            return self._error(exc.status, exc.code, str(exc))
        except KeyError:
            return self._error(404, "not_found", "profile or suggestion was not found")
        except ValueError as exc:
            if str(exc).startswith("state_conflict:"):
                return self._error(409, "state_conflict", "suggestion is already resolved")
            return self._error(422, "profile_validation_failed", str(exc))
        return self._error(404, "not_found", "route was not found")

    def _recommendation_storage(self) -> SQLiteRecommendationStorage | None:
        migration_path = getattr(self.store, "migration_path", None)
        if migration_path is None:
            return None
        return SQLiteRecommendationStorage(self.store.database_path, migration_path)

    def _load_recommendation_run(self, run_id: str) -> dict[str, Any] | None:
        storage = self._recommendation_storage()
        if storage is None:
            return None
        return storage.find_recommendation_batch(run_id)

    def _run_permission_error(
        self,
        batch: dict[str, Any],
        cookies: dict[str, str],
    ) -> ApiResponse | None:
        context = str(batch.get("feedback_context") or "")
        if not context.startswith("user:"):
            return None
        if self.auth_service is None:
            return self._error(
                503,
                "service_not_ready",
                "authentication service is not configured",
            )
        user = self.auth_service.current_user(cookies.get(SESSION_COOKIE_NAME))
        if user is None:
            return self._error(401, "authentication_required", "login is required")
        if not context.startswith(f"user:{int(user['user_id'])}:"):
            return self._error(
                403,
                "insufficient_permission",
                "recommendation run does not belong to the current user",
            )
        return None

    @staticmethod
    def _model_metadata_for_batch(batch: Any) -> dict[str, Any]:
        if isinstance(batch, dict):
            rankings = list(batch.get("rankings") or [])
            match_version = str(batch.get("match_version") or "")
            warnings = list(batch.get("warnings") or [])
        else:
            rankings = list(getattr(batch, "rankings", ()) or ())
            match_version = str(getattr(batch, "match_version", "") or "")
            warnings = list(getattr(batch, "warnings", ()) or ())
        if not rankings or not rankings[0].get("scoring_engine"):
            return current_model_metadata().to_dict()
        first = rankings[0]
        return {
            "variant": str(first["scoring_engine"]),
            "version": str(first.get("model_version") or "unknown"),
            "status": "ok",
            "dataset_version": first.get("dataset_version"),
            "mapping_version": None,
            "feature_versions": {
                "matching": match_version,
                "graph": str(first.get("graph_version") or ""),
                "encoder": str(first.get("encoder_version") or ""),
            },
            "warnings": warnings,
        }

    def _save_run_ui_metadata(
        self,
        run_id: str,
        profile: Any,
        model: dict[str, Any],
    ) -> None:
        payload = {
            "profile_key": getattr(profile, "profile_key", None),
            "display_name": getattr(profile, "display_name", None),
            "service_track": getattr(profile, "service_track", None),
            "profile_source": getattr(profile, "profile_source", None),
            "profile_version": getattr(profile, "profile_version", None),
        }
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO recommendation_run_ui_metadata (
                    run_id, profile_json, model_json, created_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    profile_json = excluded.profile_json,
                    model_json = excluded.model_json,
                    created_at = excluded.created_at
                """,
                (
                    run_id,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    json.dumps(model, ensure_ascii=False, sort_keys=True),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def _load_run_ui_metadata(self, run_id: str) -> dict[str, Any]:
        with self.store.connect() as connection:
            row = connection.execute(
                """
                SELECT profile_json, model_json
                FROM recommendation_run_ui_metadata
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return {}
        return {
            "profile": json.loads(row["profile_json"] or "{}"),
            "model": json.loads(row["model_json"] or "{}"),
        }

    def _save_shadow_comparison(
        self,
        *,
        run_id: str,
        user_id: int,
        batch: Any,
        paired: dict[str, Any],
    ) -> None:
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO recommendation_advanced_adapter_shadow (
                    run_id, user_id, mode, model_version,
                    profile_adapter_version, profile_key, profile_version,
                    profile_mode, candidate_snapshot_hash, paired_json, created_at
                ) VALUES (?, ?, 'shadow', ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    model_version = excluded.model_version,
                    profile_adapter_version = excluded.profile_adapter_version,
                    profile_key = excluded.profile_key,
                    profile_version = excluded.profile_version,
                    profile_mode = excluded.profile_mode,
                    candidate_snapshot_hash = excluded.candidate_snapshot_hash,
                    paired_json = excluded.paired_json,
                    created_at = excluded.created_at
                """,
                (
                    run_id,
                    user_id,
                    str(paired.get("model_version") or ""),
                    str(paired.get("profile_adapter_version") or ""),
                    str(paired.get("profile_key") or ""),
                    str(paired.get("profile_version") or ""),
                    str(paired.get("profile_mode") or ""),
                    str(batch.candidate_hash),
                    json.dumps(paired, ensure_ascii=False, sort_keys=True),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def _load_shadow_comparison(self, run_id: str) -> dict[str, Any] | None:
        with self.store.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM recommendation_advanced_adapter_shadow
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "mode": str(row["mode"]),
            "model_version": str(row["model_version"]),
            "profile_adapter_version": str(row["profile_adapter_version"]),
            "profile_key": str(row["profile_key"]),
            "profile_version": str(row["profile_version"]),
            "profile_mode": str(row["profile_mode"]),
            "candidate_snapshot_hash": str(row["candidate_snapshot_hash"]),
            "paired": json.loads(row["paired_json"]),
            "created_at": str(row["created_at"]),
        }

    def _recommendation_run_route(
        self,
        run_id: str,
        cookies: dict[str, str],
    ) -> ApiResponse:
        if self._recommendation_storage() is None:
            return self._error(
                503,
                "service_not_ready",
                "recommendation run storage is not configured",
            )
        batch = self._load_recommendation_run(run_id)
        if batch is None:
            return self._error(404, "not_found", "recommendation run was not found")
        denied = self._run_permission_error(batch, cookies)
        if denied is not None:
            return denied
        ui_metadata = self._load_run_ui_metadata(run_id)
        shadow = self._load_shadow_comparison(run_id)
        model = ui_metadata.get("model") or self._model_metadata_for_batch(batch)
        return ApiResponse(
            200,
            {
                "run_id": batch["run_id"],
                "feedback_context": batch.get("feedback_context"),
                "service_track": batch["service_track"],
                "match_version": batch["match_version"],
                "profile_snapshot_hash": batch["profile_snapshot_hash"],
                "candidate_snapshot_hash": batch["candidate_snapshot_hash"],
                "warnings": list(batch.get("warnings") or []),
                "created_at": batch["created_at"],
                "items": list(batch.get("items") or []),
                "profile": ui_metadata.get("profile") or None,
                "model": model,
                "advanced_adapter": model.get("advanced_adapter"),
                "advanced_adapter_shadow": shadow,
                "api_version": API_VERSION,
            },
        )

    def _public_candidates_route(
        self,
        query: dict[str, list[str]],
    ) -> ApiResponse:
        raw_limit = (query.get("limit") or ["6"])[0]
        try:
            limit = int(raw_limit)
        except ValueError:
            return self._error(400, "invalid_limit", "limit must be an integer")
        if not 1 <= limit <= 30:
            return self._error(400, "invalid_limit", "limit must be between 1 and 30")
        tasks = [
            task
            for task in self.store.matchable_candidates()
            if str(task.get("candidate_availability") or "")
            in {"available", "temporarily_unverified"}
        ]
        tasks.sort(
            key=lambda task: str(task.get("last_activity_at") or ""),
            reverse=True,
        )
        items = [
            PublicCandidateItem(
                task_candidate_id=int(task["task_candidate_id"]),
                repository_full_name=str(task["repository"]),
                issue_number=int(task["issue_number"]),
                title=str(task["title"]),
                html_url=str(task["html_url"]),
                availability=str(task.get("candidate_availability") or "available"),
                verified_at=task.get("github_verified_at"),
                primary_language=task.get("primary_language"),
                labels=tuple(task.get("labels") or []),
                task_types=tuple(task.get("task_types") or []),
                comment_count=int(task.get("comment_count") or 0),
                last_activity_at=task.get("last_activity_at"),
                newcomer_label_signal=bool(task.get("newcomer_label_signal")),
                estimated_code_difficulty=task.get("estimated_code_difficulty"),
                estimated_setup_difficulty=task.get("estimated_setup_difficulty"),
            ).to_dict()
            for task in tasks[:limit]
        ]
        return ApiResponse(
            200,
            {
                "items": items,
                "count": len(items),
                "personalized": False,
                "warnings": ["public_candidates_are_not_personalized"],
                "match_version": MATCH_VERSION_V3,
                "model": current_model_metadata().to_dict(),
                "api_version": API_VERSION,
            },
        )

    def _project_recommendations_route(
        self,
        query: dict[str, list[str]],
        cookies: dict[str, str],
    ) -> ApiResponse:
        if self._recommendation_storage() is None:
            return self._error(
                503,
                "service_not_ready",
                "recommendation run storage is not configured",
            )
        run_id = (query.get("run_id") or [""])[0].strip()
        if not run_id:
            return self._error(
                400,
                "missing_run_id",
                "run_id query parameter is required",
            )
        batch = self._load_recommendation_run(run_id)
        if batch is None:
            return self._error(404, "not_found", "recommendation run was not found")
        denied = self._run_permission_error(batch, cookies)
        if denied is not None:
            return denied
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in batch.get("items") or []:
            grouped.setdefault(str(item["repository_full_name"]), []).append(item)
        projects: list[dict[str, Any]] = []
        for repository, entries in grouped.items():
            scores = [float(item.get("score") or 0.0) for item in entries]
            basis = [
                f"本次推荐中该仓库有 {len(entries)} 个任务命中",
                f"最高匹配分 {round(max(scores) * 100)}",
            ]
            if len(entries) >= 2:
                basis.append("同一仓库多次命中，可作为持续参与入口")
            projects.append(
                ProjectRecommendationItem(
                    repository_full_name=repository,
                    repository_url=f"https://github.com/{repository}",
                    score=sum(scores) / len(scores),
                    ranking_source="issue_aggregation_compat_v0.1",
                    basis=tuple(basis),
                    related_issues=tuple(
                        {
                            "task_candidate_id": int(item["task_candidate_id"]),
                            "issue_number": int(item["issue_number"]),
                            "title": str(item["title"]),
                            "html_url": str(item["html_url"]),
                            "score": float(item.get("score") or 0.0),
                        }
                        for item in entries
                    ),
                    modules=(),
                    contributing_url=None,
                    checked_at=batch.get("created_at"),
                    status="available",
                    warnings=("modules_not_available",),
                ).to_dict()
            )
        projects.sort(key=lambda item: (-item["score"], item["repository_full_name"]))
        return ApiResponse(
            200,
            {
                "run_id": run_id,
                "items": projects,
                "count": len(projects),
                "ranking_source": "issue_aggregation_compat_v0.1",
                "warnings": [
                    "project_ranking_from_issue_aggregation",
                    "project_model_not_connected",
                ],
                "api_version": API_VERSION,
            },
        )

    def _me_recommendations_route(self, query, cookies) -> ApiResponse:
        if (
            self.recommendation_service is None
            or self.auth_service is None
            or self.profile_service is None
        ):
            return self._error(503, "service_not_ready", "recommendation service is not configured")
        raw_limit = (query.get("limit") or ["10"])[0]
        try:
            limit = int(raw_limit)
        except ValueError:
            return self._error(400, "invalid_limit", "limit must be an integer")
        if not 1 <= limit <= 100:
            return self._error(400, "invalid_limit", "limit must be between 1 and 100")
        user = self.auth_service.current_user(cookies.get(SESSION_COOKIE_NAME))
        if user is None:
            return self._error(401, "authentication_required", "login is required")
        user_id = int(user["user_id"])
        profile = self.profile_service.profile_for_user(user_id)
        if profile is None:
            return self._error(
                404,
                "profile_required",
                "create a profile before requesting recommendations",
            )
        developer_key = verified_developer_key(user.get("github_login"))
        enhanced_profile = None
        if self.midterm_profile_service is not None:
            try:
                enhanced_profile = self.midterm_profile_service.enhanced_features(
                    user_id=user_id,
                    developer_key=developer_key,
                ).to_dict()
            except (
                MidtermProfileRequiredError,
                MidtermProfileNotConfirmedError,
            ):
                enhanced_profile = None
            except ValueError as exc:
                return self._error(
                    422,
                    "profile_validation_failed",
                    str(exc),
                )
        return self._recommendations(
            profile,
            limit=limit,
            feedback_context=feedback_context_for_user(
                user_id,
                str(profile["profile_key"]),
            ),
            developer_key=developer_key,
            enhanced_profile=enhanced_profile,
            owner_user_id=user_id,
        )

    @staticmethod
    def _error(status: int, code: str, message: str) -> ApiResponse:
        return ApiResponse(status, {"error": {"code": code, "message": message}})

    def _task_detail_route(self, path: str, query: dict[str, list[str]]) -> ApiResponse:
        """Serve one task detail; GitHub re-verification is opt-in via refresh=true."""

        match = TASK_DETAIL_PATH_PATTERN.fullmatch(path)
        if match is None:
            return self._error(404, "not_found", "route was not found")
        task_candidate_id = int(match.group(1))
        unsupported = sorted(set(query) - {"refresh"})
        if unsupported:
            return self._error(
                400, "invalid_request", f"unsupported query parameter: {unsupported[0]}"
            )
        raw_refresh = (query.get("refresh") or ["0"])[0].strip().lower()
        if raw_refresh in REFRESH_TRUE:
            refresh = True
        elif raw_refresh in REFRESH_FALSE:
            refresh = False
        else:
            return self._error(400, "invalid_request", "refresh must be true or false")

        if refresh:
            if self.candidate_service is None:
                return self._error(
                    503,
                    "service_not_ready",
                    "task re-verification is not configured for this server",
                )
            try:
                detail = self.candidate_service.candidate_detail(task_candidate_id)
            except KeyError:
                return self._error(404, "not_found", "task candidate was not found")
            except RateLimitExceeded:
                return self._error(429, "rate_limited", "GitHub rate limit exhausted")
            except GitHubApiError as exc:
                if getattr(exc, "status_code", None) == 429:
                    return self._error(429, "rate_limited", "GitHub rate limit exhausted")
                return self._error(
                    502, "github_upstream_error", "GitHub returned an unrecoverable error"
                )
            return ApiResponse(
                200,
                {
                    **_refreshed_candidate_detail_body(detail),
                    "model": current_model_metadata().to_dict(),
                    "api_version": API_VERSION,
                },
            )

        if self.candidate_storage is None:
            return self._error(503, "service_not_ready", "candidate storage is not configured")
        row = self.candidate_storage.candidate_detail_row(task_candidate_id)
        if row is None:
            return self._error(404, "not_found", "task candidate was not found")
        task = next(
            (
                item
                for item in self.store.matchable_candidates()
                if int(item["task_candidate_id"]) == task_candidate_id
            ),
            None,
        )
        return ApiResponse(
            200,
            {
                **_candidate_detail_body(row, task),
                "model": current_model_metadata().to_dict(),
                "api_version": API_VERSION,
            },
        )

    def _database_ready(self) -> tuple[bool, str | None]:
        """Separate "process is alive" from "database cannot be used"."""

        if not self.store.database_path.is_file():
            return False, "database_missing"
        if self.candidate_storage is None:
            return True, None
        try:
            with self.candidate_storage.connect() as connection:
                connection.execute("SELECT 1").fetchone()
        except sqlite3.Error:
            return False, "database_unavailable"
        return True, None

    def _recommendations(
        self,
        profile: dict[str, Any],
        *,
        limit: int,
        feedback_context: str | None = None,
        developer_key: str | None = None,
        enhanced_profile: dict[str, Any] | None = None,
        owner_user_id: int | None = None,
    ) -> ApiResponse:
        tasks = self.store.matchable_candidates()
        task_by_id = {int(task['task_candidate_id']): task for task in tasks}
        contract_profile = as_developer_profile_v2(profile)
        recommendation_service = self.recommendation_service
        try:
            scorer = scorer_with_profile(
                recommendation_service.model_scorer,
                enhanced_profile,
                mode=self.advanced_adapter_mode,
                service_track=str(profile.get("service_track") or ""),
            )
        except ModelUnavailable as exc:
            return self._error(503, "model_unavailable", str(exc))
        shadow_scorer = scorer if isinstance(scorer, ShadowProfileScorer) else None
        if scorer is not recommendation_service.model_scorer:
            recommendation_service = copy.copy(recommendation_service)
            recommendation_service.model_scorer = scorer
        batch = recommendation_service.recommend(
            profile=contract_profile,
            limit=limit,
            feedback_context=feedback_context,
            developer_key=developer_key,
        )
        shadow_recorded = False
        if shadow_scorer is not None and owner_user_id is not None:
            paired = shadow_scorer.paired_payload()
            if paired is not None:
                self._save_shadow_comparison(
                    run_id=batch.run_id,
                    user_id=owner_user_id,
                    batch=batch,
                    paired=paired,
                )
                shadow_recorded = True
        advanced_adapter = self._advanced_adapter_metadata(
            scorer=scorer,
            profile_snapshot=enhanced_profile,
            batch=batch,
            shadow_recorded=shadow_recorded,
        )
        if self._load_recommendation_run(batch.run_id) is not None:
            model_metadata = self._model_metadata_for_batch(batch)
            model_metadata["advanced_adapter_mode"] = self.advanced_adapter_mode
            model_metadata["advanced_adapter"] = advanced_adapter
            self._save_run_ui_metadata(
                batch.run_id,
                contract_profile,
                model_metadata,
            )
        return ApiResponse(
            200,
            {
                "profile": {
                    "profile_key": profile["profile_key"],
                    "display_name": profile["display_name"],
                    "service_track": profile["service_track"],
                    "profile_source": profile.get("profile_source", "stored"),
                    "profile_version": contract_profile.profile_version,
                },
                "items": [
                    {
                        **item.to_dict(),
                        # Additive aliases retained for the existing v0.4 web client.
                        "track": item.service_track,
                        "repository": item.repository_full_name,
                        "match_score": round(item.score * 100.0, 2),
                        "skill_coverage": (
                            len(item.matched_skills)
                            / max(1, len(item.matched_skills) + len(item.missing_skills))
                        ),
                        "maximum_skill_gap": 1 if item.missing_skills else 0,
                        "skill_gaps": [
                            {"skill": name, "gap": 0, "developer_level": 1, "required_level": 1}
                            for name in item.matched_skills
                        ] + [
                            {"skill": name, "gap": 1, "developer_level": 0, "required_level": 1}
                            for name in item.missing_skills
                        ],
                        'difficulty': {
                            'code': task_by_id[item.task_candidate_id].get('estimated_code_difficulty'),
                            'setup': task_by_id[item.task_candidate_id].get('estimated_setup_difficulty'),
                        },
                        'verified_at': task_by_id[item.task_candidate_id].get('github_verified_at'),
                        'verification_expires_at': task_by_id[item.task_candidate_id].get('verification_expires_at'),
                    }
                    for item in batch.items
                ],
                "count": len(batch.items),
                "feedback_context": feedback_context,
                "run_id": batch.run_id,
                "service_track": batch.service_track,
                "match_version": batch.match_version,
                "profile_hash": batch.profile_hash,
                "candidate_hash": batch.candidate_hash,
                "created_at": batch.created_at,
                "warnings": list(batch.warnings),
                "rankings": list(batch.rankings),
                "advanced_adapter": advanced_adapter,
                "api_version": API_VERSION,
            },
        )

    def _advanced_adapter_metadata(
        self,
        *,
        scorer: Any,
        profile_snapshot: dict[str, Any] | None,
        batch: Any,
        shadow_recorded: bool,
    ) -> dict[str, Any]:
        base_scorer = getattr(scorer, "base_scorer", scorer)
        adapter_manifest = (
            getattr(base_scorer, "profile_adapter_manifest", None) or {}
        )
        rankings = list(getattr(batch, "rankings", ()) or ())
        profile_mode = (
            str(rankings[0].get("profile_mode"))
            if rankings and rankings[0].get("profile_mode")
            else None
        )
        profile_applied = (
            self.advanced_adapter_mode == "serve"
            and isinstance(scorer, ProfileAwareScorer)
            and profile_snapshot is not None
        )
        return {
            "mode": self.advanced_adapter_mode,
            "active": profile_applied,
            "profile_applied": profile_applied,
            "profile_adapter_version": (
                str(adapter_manifest.get("adapter_version"))
                if adapter_manifest.get("adapter_version")
                else None
            ),
            "profile_mode": profile_mode,
            "profile_key": (
                str(profile_snapshot.get("profile_key"))
                if profile_snapshot and profile_snapshot.get("profile_key")
                else None
            ),
            "profile_version": (
                str(profile_snapshot.get("profile_version"))
                if profile_snapshot and profile_snapshot.get("profile_version")
                else None
            ),
            "shadow_recorded": shadow_recorded,
        }

    def _validate_feedback_context(self, value: Any) -> tuple[str, str]:
        if not isinstance(value, str) or not 1 <= len(value) <= 140:
            raise ValueError("feedback_context is invalid")
        if value.startswith("preset:"):
            profile_key = value.removeprefix("preset:")
            if not PROFILE_KEY_PATTERN.fullmatch(profile_key):
                raise ValueError("feedback_context is invalid")
            if not self._is_public_profile(profile_key):
                raise ValueError("feedback_context profile was not found")
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
        try:
            response = self._dispatch(method, path, query, body, cookies or {})
        except sqlite3.Error:
            response = self._error(503, "service_not_ready", "database is unavailable or requires migration")
        except ModelUnavailableError as exc:
            response = self._error(503, exc.code, str(exc))
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
        run_match = RECOMMENDATION_RUN_PATH_PATTERN.fullmatch(path)
        if run_match is not None:
            if method != "GET":
                return self._error(
                    405,
                    "method_not_allowed",
                    "method is not supported for this route",
                )
            return self._recommendation_run_route(run_match.group(1), cookies)
        if path.startswith("/api/v1/tasks/"):
            if method != "GET":
                return self._error(
                    405, "method_not_allowed", "method is not supported for this route"
                )
            return self._task_detail_route(path, query)
        if method == "GET" and path == "/api/v1/public-candidates":
            return self._public_candidates_route(query)
        if method == "GET" and path == "/api/v1/me/project-recommendations":
            return self._project_recommendations_route(query, cookies)
        if method == "GET" and path == "/api/v1/me/recommendations":
            return self._me_recommendations_route(query, cookies)
        if path in MIDTERM_PROFILE_PATHS:
            return self._midterm_profile_route(method, path, query, body, cookies)
        if path in {"/api/v1/me/profile", "/api/v1/me/profile/import-github"} or re.fullmatch(
            r"/api/v1/me/profile/suggestions/[1-9][0-9]{0,18}/(accept|reject)", path
        ):
            return self._profile_route(method, path, query, body, cookies)
        if method == "GET" and path == "/health":
            database_ready, database_error = self._database_ready()
            return ApiResponse(
                200 if database_ready else 503,
                {
                    "status": "ok" if database_ready else "not_ready",
                    "api_version": API_VERSION,
                    "database_ready": database_ready,
                    **({"database_error": database_error} if database_error else {}),
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
                if not self._is_public_profile(profile_key):
                    raise ValueError("profile is not public")
                profile = self.store.profile_for_matching(profile_key)
            except ValueError:
                return self._error(404, "profile_not_found", "developer profile was not found")
            return self._recommendations(
                profile,
                limit=limit,
                feedback_context=f"preset:{profile_key}",
            )
        if method == "GET" and path == "/api/v1/me/recommendations":
            user = self.auth_service.current_user(cookies.get(SESSION_COOKIE_NAME)) if self.auth_service else None
            if user is None:
                return self._error(401, "authentication_required", "login is required")
            profile = self.profile_service.profile_for_user(int(user["user_id"])) if self.profile_service else None
            if profile is None:
                return self._error(404, "profile_required", "create a profile before requesting recommendations")
            try:
                limit = int((query.get("limit") or ["10"])[0])
            except ValueError:
                return self._error(400, "invalid_limit", "limit must be an integer")
            if not 1 <= limit <= 100:
                return self._error(400, "invalid_limit", "limit must be between 1 and 100")
            return self._recommendations(
                profile,
                limit=limit,
                feedback_context=feedback_context_for_user(int(user["user_id"]), str(profile["profile_key"])),
                developer_key=verified_developer_key(user.get("github_login")),
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
            unknown = set(body) - {
                "task_candidate_id",
                "feedback_context",
                "feedback_state",
                "run_id",
            }
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
            supplied_context = body.get("feedback_context")
            if isinstance(supplied_context, str) and supplied_context.startswith("user:"):
                if self.auth_service is None or self.profile_service is None:
                    return self._error(503, "service_not_ready", "authenticated feedback is not configured")
                user = self.auth_service.current_user(cookies.get(SESSION_COOKIE_NAME))
                if user is None:
                    return self._error(401, "authentication_required", "login is required")
                profile = self.profile_service.profile_for_user(int(user["user_id"]))
                if profile is None:
                    return self._error(404, "profile_required", "create a profile before recording feedback")
                expected_context = feedback_context_for_user(
                    int(user["user_id"]), str(profile["profile_key"])
                )
                if supplied_context != expected_context:
                    return self._error(
                        403,
                        "insufficient_permission",
                        "feedback context does not belong to the current user",
                    )
                feedback_context = expected_context
                service_track = str(profile["service_track"])
            else:
                try:
                    feedback_context, service_track = self._validate_feedback_context(supplied_context)
                except ValueError as exc:
                    return self._error(400, "invalid_feedback_context", str(exc))
            run_id = body.get("run_id")
            if run_id is not None:
                if (
                    not isinstance(run_id, str)
                    or not re.fullmatch(r"[A-Za-z0-9-]{1,80}", run_id)
                ):
                    return self._error(
                        400,
                        "invalid_run_id",
                        "run_id must be a recommendation run identifier",
                    )
                if self._recommendation_storage() is None:
                    return self._error(
                        503,
                        "service_not_ready",
                        "recommendation run storage is not configured",
                    )
                recommendation_run = self._load_recommendation_run(run_id)
                if recommendation_run is None:
                    return self._error(
                        404,
                        "recommendation_run_not_found",
                        "recommendation run was not found",
                    )
                denied = self._run_permission_error(recommendation_run, cookies)
                if denied is not None:
                    return denied
                if str(recommendation_run.get("feedback_context") or "") != feedback_context:
                    return self._error(
                        403,
                        "insufficient_permission",
                        "feedback context does not match the recommendation run",
                    )
                if not any(
                    int(item.get("task_candidate_id") or 0) == task_candidate_id
                    for item in recommendation_run.get("items") or ()
                ):
                    return self._error(
                        404,
                        "task_not_in_recommendation_run",
                        "task candidate was not shown in the recommendation run",
                    )
            try:
                feedback_args = {
                    "task_candidate_id": task_candidate_id,
                    "feedback_context": feedback_context,
                    "service_track": service_track,
                    "feedback_state": feedback_state,
                }
                if run_id is not None:
                    feedback_args["run_id"] = run_id
                feedback = self.store.record_feedback(
                    **feedback_args,
                )
            except ValueError as exc:
                if run_id is not None:
                    return self._error(409, "invalid_feedback_run", str(exc))
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
                    'automatic_refresh': self.refresh_worker.status() if self.refresh_worker else {'enabled': False},
                    "api_version": API_VERSION,
                    "match_version": MATCH_VERSION_V2,
                    "model": current_model_metadata().to_dict(),
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
                            **({"Secure": None} if getattr(
                                getattr(self.auth_service, "settings", None), "base_url", ""
                            ).startswith("https://") else {}),
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
            "/api/v1/public-candidates",
            "/api/v1/status",
            "/api/v1/auth/github/start",
            "/api/v1/auth/github/callback",
            "/api/v1/auth/logout",
            "/api/v1/me",
            "/api/v1/me/recommendations",
            "/api/v1/me/project-recommendations",
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
        server_version = "OSS-Mentor-MVP/0.6"

        def _parse_cookies(self) -> dict[str, str]:
            cookies: dict[str, str] = {}
            raw = self.headers.get("Cookie", "")
            for part in raw.split(";"):
                if "=" in part:
                    name, _, value = part.partition("=")
                    cookies[name.strip()] = value.strip()
            return cookies

        def _send_json(self, response: ApiResponse, *, allow: str | None = None,
                       location: str | None = None) -> None:
            request_id = response.body.setdefault("request_id", str(uuid4()))
            if "error" in response.body:
                response.body["error"].setdefault("request_id", request_id)
            encoded = json.dumps(
                response.body, ensure_ascii=False, sort_keys=True
            ).encode("utf-8")
            self.send_response(response.status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            if location:
                self.send_header("Location", location)
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
                    "img-src 'self' data: https://github.com https://avatars.githubusercontent.com; "
                    "connect-src 'self'; base-uri 'none'; "
                    "frame-ancestors 'none'",
                )
                self.end_headers()
                self.wfile.write(asset.body)
                return
            response = api.handle(
                "GET",
                parsed.path,
                parse_qs(parsed.query, keep_blank_values=True),
                cookies=self._parse_cookies(),
                request_id=str(uuid4()),
            )
            if (parsed.path == "/api/v1/auth/github/callback" and response.status == 200
                    and "text/html" in self.headers.get("Accept", "")):
                self._send_json(ApiResponse(303, response.body, response.cookies),
                                location=response.body["return_to"])
            else:
                self._send_json(response)

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
            self._read_json_request("POST")

        def do_PUT(self) -> None:  # noqa: N802 - stdlib handler contract
            self._read_json_request("PUT")

        def _read_json_request(self, method: str) -> None:
            parsed = urlsplit(self.path)
            path = parsed.path
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
            previous_timeout = self.connection.gettimeout()
            try:
                self.connection.settimeout(10)
                raw = self.rfile.read(content_length)
            except (TimeoutError, OSError):
                self._send_json(api._error(400, "invalid_request", "request body could not be read"))
                return
            finally:
                self.connection.settimeout(previous_timeout)
            if len(raw) != content_length:
                self._send_json(api._error(400, "invalid_request", "request body is incomplete"))
                return
            # Consume bounded bodies before rejecting headers, so Windows can deliver
            # the JSON error without aborting a socket with unread request bytes.
            if path.startswith("/api/v1/me/profile"):
                origin = self.headers.get("Origin")
                settings = getattr(api.auth_service, "settings", None)
                expected = getattr(settings, "base_url", "http://" + self.headers.get("Host", ""))
                site = urlsplit(expected)
                if (origin is not None and origin != f"{site.scheme}://{site.netloc}") or (
                    self.headers.get("Sec-Fetch-Site") == "cross-site"
                ):
                    self._send_json(api._error(403, "insufficient_permission", "cross-origin profile writes are not allowed"))
                    return
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
            if content_type != "application/json":
                self._send_json(api._error(415, "unsupported_media_type", "Content-Type must be application/json"))
                return
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._send_json(api._error(400, "invalid_json", "request body contains invalid JSON"))
                return
            response = api.handle(
                method, path, parse_qs(parsed.query, keep_blank_values=True),
                body=payload, cookies=self._parse_cookies(), request_id=str(uuid4()),
            )
            self._send_json(response)

        def log_message(self, format: str, *args: object) -> None:
            # OAuth callback query strings contain credentials: never log them.
            super().log_message("%s %s", self.command, urlsplit(self.path).path)

    return Handler


def serve(
    store: SQLiteCandidateStore,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    cors_origin: str | None = None,
    static_root: Path | None = None,
    auth_service: AuthService | None = None,
    refresh_worker=None,
    recommendation_service: RecommendationService | None = None,
    candidate_service: CandidateService | None = None,
    midterm_profile_service: MidtermProfileService | None = None,
    questionnaire_path: Path | None = None,
    advanced_adapter_mode: str = "off",
) -> None:
    api = RecommendationApi(
        store,
        auth_service=auth_service,
        recommendation_service=recommendation_service,
        candidate_service=candidate_service,
        refresh_worker=refresh_worker,
        midterm_profile_service=midterm_profile_service,
        questionnaire_path=questionnaire_path,
        advanced_adapter_mode=advanced_adapter_mode,
    )
    server = ThreadingHTTPServer(
        (host, port),
        make_handler(api, cors_origin=cors_origin, static_root=static_root),
    )
    try:
        if refresh_worker:
            refresh_worker.start()
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if refresh_worker:
            refresh_worker.stop()
        server.server_close()
