"""Immutable gzip JSON storage for GitHub responses and lineage metadata."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import tempfile
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from oss_mentor.collector.github_client import GitHubResponse, parse_link_header


class RawStoreError(ValueError):
    """Raised when a raw response cannot be stored safely."""


@dataclass(frozen=True, slots=True)
class RawRecord:
    path: Path
    response_sha256: str | None
    request_fingerprint: str
    payload_bytes: int
    fetched_at: datetime


_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")
_SECRET_QUERY_NAMES = {
    "access_token",
    "client_secret",
    "token",
    "authorization",
}


def _safe_segment(value: str) -> str:
    cleaned = _SAFE_SEGMENT.sub("_", value).strip("._")
    return cleaned or "unknown"


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _parse_rate_reset(headers: dict[str, str]) -> str | None:
    raw = headers.get("x-ratelimit-reset")
    if not raw:
        return None
    try:
        return datetime.fromtimestamp(int(raw), tz=timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


class RawStore:
    def __init__(
        self,
        root: Path,
        *,
        api_version: str | None = None,
        schema_version: str = "github-rest-raw-v1",
    ) -> None:
        self.root = root.resolve()
        self.api_version = api_version
        self.schema_version = schema_version

    @staticmethod
    def _validate_url(url: str) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        forbidden = _SECRET_QUERY_NAMES.intersection(name.casefold() for name in query)
        if forbidden:
            raise RawStoreError(
                "refusing to persist URL containing secret query fields: "
                + ", ".join(sorted(forbidden))
            )

    def save(
        self,
        response: GitHubResponse,
        *,
        endpoint_name: str,
        repository_full_name: str,
        collection_run_id: UUID,
    ) -> RawRecord:
        self._validate_url(response.url)
        if "/" not in repository_full_name:
            raise RawStoreError("repository_full_name must be owner/repo")
        owner, repository = repository_full_name.split("/", maxsplit=1)

        request_fingerprint = hashlib.sha256(response.url.encode("utf-8")).hexdigest()
        payload_bytes_raw = (
            _canonical_json(response.payload) if response.payload is not None else b""
        )
        response_sha256 = (
            hashlib.sha256(payload_bytes_raw).hexdigest()
            if response.payload is not None
            else None
        )

        selected_headers = {
            key: response.headers[key]
            for key in (
                "etag",
                "last-modified",
                "link",
                "content-type",
                "x-ratelimit-limit",
                "x-ratelimit-remaining",
                "x-ratelimit-used",
                "x-ratelimit-reset",
                "x-ratelimit-resource",
            )
            if key in response.headers
        }
        envelope = {
            "metadata": {
                "collection_run_id": str(collection_run_id),
                "source_system": "github_rest",
                "source_endpoint": endpoint_name,
                "source_url": response.url,
                "request_fingerprint": request_fingerprint,
                "api_version": self.api_version,
                "fetched_at": response.fetched_at.isoformat(),
                "status_code": response.status_code,
                "headers": selected_headers,
                "pagination_links": parse_link_header(response.headers.get("link")),
                "rate_limit_reset_at": _parse_rate_reset(response.headers),
                "response_sha256": response_sha256,
                "schema_version": self.schema_version,
            },
            "payload": response.payload,
        }
        encoded = _canonical_json(envelope)

        day = response.fetched_at.astimezone(timezone.utc).date().isoformat()
        timestamp = response.fetched_at.astimezone(timezone.utc).strftime(
            "%Y%m%dT%H%M%S.%fZ"
        )
        directory = (
            self.root
            / _safe_segment(endpoint_name)
            / _safe_segment(owner)
            / _safe_segment(repository)
            / day
        )
        directory.mkdir(parents=True, exist_ok=True)
        filename = f"{timestamp}_{request_fingerprint[:16]}.json.gz"
        destination = directory / filename

        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{filename}.", suffix=".tmp", dir=directory
        )
        try:
            with os.fdopen(fd, "wb") as raw_handle:
                with gzip.GzipFile(
                    filename="", fileobj=raw_handle, mode="wb", mtime=0
                ) as zipped:
                    zipped.write(encoded)
            os.replace(temporary_name, destination)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

        return RawRecord(
            path=destination,
            response_sha256=response_sha256,
            request_fingerprint=request_fingerprint,
            payload_bytes=len(encoded),
            fetched_at=response.fetched_at,
        )
