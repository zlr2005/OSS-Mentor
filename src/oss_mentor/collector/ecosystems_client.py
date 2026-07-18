"""Small read-only client for the Ecosyste.ms Issues API."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import Message
from typing import Any, Callable, Mapping


class EcosystemsApiError(RuntimeError):
    """Raised when the Ecosyste.ms API cannot satisfy a request."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        url: str | None = None,
        payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.url = url
        self.payload = payload


@dataclass(frozen=True, slots=True)
class EcosystemsResponse:
    url: str
    status_code: int
    headers: dict[str, str]
    payload: Any
    fetched_at: datetime


def _headers_to_dict(headers: Message | Mapping[str, str] | None) -> dict[str, str]:
    if headers is None:
        return {}
    return {str(key).lower(): str(value) for key, value in headers.items()}


class EcosystemsClient:
    """Bounded, same-origin HTTP client for public Ecosyste.ms metadata."""

    RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

    def __init__(
        self,
        *,
        api_base: str,
        user_agent: str,
        timeout_seconds: int = 30,
        max_retries: int = 5,
        backoff_base_seconds: int = 1,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self._opener = opener
        self._sleep = sleep
        self.request_count = 0
        parsed = urllib.parse.urlsplit(self.api_base)
        self._allowed_origin = (parsed.scheme.lower(), parsed.netloc.lower())

    def _build_url(
        self, path_or_url: str, params: Mapping[str, Any] | None = None
    ) -> str:
        if path_or_url.startswith(("https://", "http://")):
            url = path_or_url
        else:
            path = path_or_url if path_or_url.startswith("/") else f"/{path_or_url}"
            url = f"{self.api_base}{path}"
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme.lower(), parsed.netloc.lower()) != self._allowed_origin:
            raise EcosystemsApiError(
                f"refusing to request URL outside API origin: {url}", url=url
            )
        if params:
            existing = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            query = urllib.parse.urlencode([*existing, *params.items()], doseq=True)
            url = urllib.parse.urlunsplit(parsed._replace(query=query))
        return url

    @staticmethod
    def _decode(body: bytes) -> Any:
        if not body:
            return None
        text = body.decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"unparsed_text": text}

    def get(
        self, path_or_url: str, *, params: Mapping[str, Any] | None = None
    ) -> EcosystemsResponse:
        url = self._build_url(path_or_url, params)
        request = urllib.request.Request(
            url=url,
            method="GET",
            headers={"Accept": "application/json", "User-Agent": self.user_agent},
        )
        for attempt in range(self.max_retries + 1):
            fetched_at = datetime.now(timezone.utc)
            self.request_count += 1
            try:
                with self._opener(request, timeout=self.timeout_seconds) as response:
                    return EcosystemsResponse(
                        url=response.geturl(),
                        status_code=int(response.status),
                        headers=_headers_to_dict(response.headers),
                        payload=self._decode(response.read()),
                        fetched_at=fetched_at,
                    )
            except urllib.error.HTTPError as exc:
                headers = _headers_to_dict(exc.headers)
                payload = self._decode(exc.read())
                if exc.code in self.RETRYABLE_STATUS_CODES and attempt < self.max_retries:
                    retry_after = headers.get("retry-after")
                    try:
                        delay = float(retry_after) if retry_after else 0.0
                    except ValueError:
                        delay = 0.0
                    self._sleep(min(max(delay, self.backoff_base_seconds * 2**attempt), 60.0))
                    continue
                raise EcosystemsApiError(
                    f"Ecosyste.ms API returned HTTP {exc.code} for {url}",
                    status_code=exc.code,
                    url=url,
                    payload=payload,
                ) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt < self.max_retries:
                    self._sleep(min(float(self.backoff_base_seconds * 2**attempt), 60.0))
                    continue
                raise EcosystemsApiError(
                    f"Ecosyste.ms request failed after retries: {url}", url=url
                ) from exc
        raise AssertionError("unreachable retry loop")

    @staticmethod
    def repository_path(full_name: str) -> str:
        encoded = urllib.parse.quote(full_name, safe="")
        return f"/hosts/GitHub/repositories/{encoded}"

    def get_repository(self, full_name: str) -> EcosystemsResponse:
        return self.get(self.repository_path(full_name))

    def get_issues(
        self,
        full_name: str,
        *,
        state: str = "open",
        per_page: int = 20,
        pull_request: bool = False,
        label: str | None = None,
    ) -> EcosystemsResponse:
        params: dict[str, Any] = {
            "state": state,
            "pull_request": str(pull_request).lower(),
            "sort": "updated_at",
            "order": "desc",
            "per_page": per_page,
        }
        if label:
            params["label"] = label
        return self.get(
            f"{self.repository_path(full_name)}/issues",
            params=params,
        )
