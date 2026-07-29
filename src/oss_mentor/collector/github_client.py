"""Small, dependency-free GitHub REST client with bounded retries."""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import Message
from typing import Any, Callable, Iterator, Mapping


class GitHubApiError(RuntimeError):
    """A non-retryable GitHub API response."""

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


class RateLimitExceeded(GitHubApiError):
    """Raised instead of sleeping for a long rate-limit reset window."""

    def __init__(self, message: str, *, reset_at: datetime | None, url: str) -> None:
        super().__init__(message, status_code=403, url=url)
        self.reset_at = reset_at


@dataclass(frozen=True, slots=True)
class GitHubResponse:
    url: str
    status_code: int
    headers: dict[str, str]
    payload: Any
    fetched_at: datetime


def _headers_to_dict(headers: Message | Mapping[str, str] | None) -> dict[str, str]:
    if headers is None:
        return {}
    return {str(key).lower(): str(value) for key, value in headers.items()}


def parse_link_header(value: str | None) -> dict[str, str]:
    """Parse GitHub's RFC 8288-style pagination Link header."""

    links: dict[str, str] = {}
    if not value:
        return links
    for section in value.split(","):
        parts = [part.strip() for part in section.split(";")]
        if not parts or not parts[0].startswith("<") or not parts[0].endswith(">"):
            continue
        url = parts[0][1:-1]
        for parameter in parts[1:]:
            if parameter.startswith("rel="):
                relation = parameter[4:].strip().strip('"')
                for name in relation.split():
                    links[name] = url
    return links


class GitHubClient:
    """Read-only GitHub client used by the pilot collector.

    Retries are intentionally bounded. A long primary-rate-limit wait is surfaced
    to the scheduler rather than holding a worker for an hour.
    """

    RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

    def __init__(
        self,
        *,
        api_base: str,
        api_version: str,
        user_agent: str,
        token: str | None,
        timeout_seconds: int = 30,
        max_retries: int = 5,
        backoff_base_seconds: int = 1,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
        random_source: Callable[[], float] = random.random,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.api_version = api_version
        self.user_agent = user_agent
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self._opener = opener
        self._sleep = sleep
        self._random = random_source
        self.request_count = 0
        self.retry_count = 0
        self.rate_limit_remaining: int | None = None
        self.rate_limit_reset_at: datetime | None = None
        parsed = urllib.parse.urlsplit(self.api_base)
        self._allowed_origin = (parsed.scheme.lower(), parsed.netloc.lower())

    def _build_url(
        self,
        path_or_url: str,
        params: Mapping[str, Any] | None,
    ) -> str:
        if path_or_url.startswith(("https://", "http://")):
            url = path_or_url
        else:
            path = path_or_url if path_or_url.startswith("/") else f"/{path_or_url}"
            url = f"{self.api_base}{path}"

        parsed = urllib.parse.urlsplit(url)
        origin = (parsed.scheme.lower(), parsed.netloc.lower())
        if origin != self._allowed_origin:
            raise GitHubApiError(f"refusing to request URL outside API origin: {url}")

        if params:
            existing = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            encoded = urllib.parse.urlencode([*existing, *params.items()], doseq=True)
            parsed = parsed._replace(query=encoded)
            url = urllib.parse.urlunsplit(parsed)
        return url

    def _request_headers(self, extra_headers: Mapping[str, str] | None) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.api_version,
            "User-Agent": self.user_agent,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if extra_headers:
            headers.update(extra_headers)
        return headers

    @staticmethod
    def _decode_payload(body: bytes) -> Any:
        if not body:
            return None
        text = body.decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"unparsed_text": text}

    @staticmethod
    def _reset_datetime(headers: Mapping[str, str]) -> datetime | None:
        raw = headers.get("x-ratelimit-reset")
        if not raw:
            return None
        try:
            return datetime.fromtimestamp(int(raw), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None

    def _retry_delay(self, attempt: int, headers: Mapping[str, str]) -> float:
        retry_after = headers.get("retry-after")
        if retry_after:
            try:
                return min(float(retry_after), 60.0)
            except ValueError:
                pass
        exponential = self.backoff_base_seconds * (2**attempt)
        return min(float(exponential) + self._random(), 60.0)

    def _capture_rate_limit(self, headers: Mapping[str, str]) -> None:
        remaining = headers.get("x-ratelimit-remaining")
        if remaining is not None:
            try:
                self.rate_limit_remaining = int(remaining)
            except ValueError:
                pass
        reset_at = self._reset_datetime(headers)
        if reset_at is not None:
            self.rate_limit_reset_at = reset_at

    def get(
        self,
        path_or_url: str,
        *,
        params: Mapping[str, Any] | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> GitHubResponse:
        url = self._build_url(path_or_url, params)
        request = urllib.request.Request(
            url=url,
            method="GET",
            headers=self._request_headers(extra_headers),
        )

        for attempt in range(self.max_retries + 1):
            fetched_at = datetime.now(timezone.utc)
            self.request_count += 1
            try:
                with self._opener(request, timeout=self.timeout_seconds) as response:
                    headers = _headers_to_dict(response.headers)
                    self._capture_rate_limit(headers)
                    payload = self._decode_payload(response.read())
                    return GitHubResponse(
                        url=response.geturl(),
                        status_code=int(response.status),
                        headers=headers,
                        payload=payload,
                        fetched_at=fetched_at,
                    )
            except urllib.error.HTTPError as exc:
                headers = _headers_to_dict(exc.headers)
                self._capture_rate_limit(headers)
                payload = self._decode_payload(exc.read())
                if exc.code == 304:
                    return GitHubResponse(
                        url=url,
                        status_code=304,
                        headers=headers,
                        payload=None,
                        fetched_at=fetched_at,
                    )

                exhausted = headers.get("x-ratelimit-remaining") == "0"
                if exc.code == 403 and exhausted:
                    reset_at = self._reset_datetime(headers)
                    raise RateLimitExceeded(
                        "GitHub primary rate limit exhausted",
                        reset_at=reset_at,
                        url=url,
                    ) from exc

                retryable = exc.code in self.RETRYABLE_STATUS_CODES or (
                    exc.code == 403 and "retry-after" in headers
                )
                if retryable and attempt < self.max_retries:
                    self.retry_count += 1
                    self._sleep(self._retry_delay(attempt, headers))
                    continue
                raise GitHubApiError(
                    f"GitHub API returned HTTP {exc.code} for {url}",
                    status_code=exc.code,
                    url=url,
                    payload=payload,
                ) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt < self.max_retries:
                    self.retry_count += 1
                    self._sleep(self._retry_delay(attempt, {}))
                    continue
                raise GitHubApiError(
                    f"GitHub request failed after retries: {url}", url=url
                ) from exc

        raise AssertionError("unreachable retry loop")

    def iter_pages(
        self,
        path_or_url: str,
        *,
        params: Mapping[str, Any] | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Iterator[GitHubResponse]:
        """Yield every page by following GitHub's ``rel=next`` URL."""

        next_url: str | None = path_or_url
        next_params = dict(params or {})
        while next_url is not None:
            response = self.get(
                next_url,
                params=next_params,
                extra_headers=extra_headers,
            )
            yield response
            links = parse_link_header(response.headers.get("link"))
            next_url = links.get("next")
            next_params = {}
