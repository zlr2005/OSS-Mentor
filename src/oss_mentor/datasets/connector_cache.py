"""Import actual read-only connector results without inventing HTTP response metadata."""

import gzip
import json
from urllib.parse import parse_qsl, urlsplit

from oss_mentor.datasets.github_history import CachedGitHubSource, digest
from oss_mentor.datasets.normalization import canonical_json


def cache_connector_reads(source: CachedGitHubSource, entries: list[dict]) -> int:
    saved = 0
    for entry in entries:
        url = urlsplit(entry["url"])
        if entry.get("tool_success") is not True or entry.get("source_system") != "github_connector":
            raise ValueError("Only successful, explicitly identified connector reads can be imported")
        if (url.scheme, url.netloc) != ("https", "api.github.com") or not url.path.startswith(f"/repos/{source.repository}/"):
            raise ValueError("Connector response belongs to another origin/repository")
        params = {}
        for name, value in parse_qsl(url.query):
            if name not in ("per_page", "page") or not value.isdigit():
                raise ValueError("Unsupported connector cache query")
            params[name] = int(value)
        payload = entry["payload"]
        if isinstance(payload, list) and len(payload) >= params.get("per_page", 30):
            raise ValueError("Potentially paginated connector result requires explicit continuation evidence")
        identity = digest(canonical_json([source.client.api_base, source.client.api_version, url.path, params or None]).encode())
        envelope = {"metadata": {"source_system": "github_connector", "source_url": entry["url"],
                                  "fetched_at": entry["fetched_at"], "status_code": None, "tool_success": True,
                                  "headers": {}, "headers_available": False, "api_version": None,
                                  "response_sha256": digest(canonical_json(payload).encode())}, "payload": payload}
        data = gzip.compress(canonical_json(envelope).encode(), mtime=0)
        path = source.root / "connector_raw" / f"{identity}.json.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        if identity in source.index:
            # Preserve already observed HTTP data rather than silently replace it.
            continue
        path.write_bytes(data)
        source.index[identity] = {"path": path.relative_to(source.root).as_posix(), "sha256": digest(data)}
        saved += 1
    source.root.mkdir(parents=True, exist_ok=True)
    temporary = source.index_path.with_suffix(".tmp")
    temporary.write_text(canonical_json(source.index), encoding="utf-8")
    temporary.replace(source.index_path)
    return saved
