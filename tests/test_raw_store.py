from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from oss_mentor.collector.github_client import GitHubResponse
from oss_mentor.collector.raw_store import RawStore, RawStoreError


class RawStoreTests(unittest.TestCase):
    def test_writes_traceable_gzip_envelope(self) -> None:
        response = GitHubResponse(
            url="https://api.github.com/repos/example/demo?per_page=100",
            status_code=200,
            headers={
                "etag": '"abc"',
                "x-ratelimit-limit": "5000",
                "x-ratelimit-remaining": "4999",
                "x-ratelimit-reset": "1783728000",
            },
            payload={"id": 123, "full_name": "example/demo"},
            fetched_at=datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc),
        )

        with tempfile.TemporaryDirectory() as temporary:
            store = RawStore(Path(temporary), api_version="2026-03-10")
            record = store.save(
                response,
                endpoint_name="repository",
                repository_full_name="example/demo",
                collection_run_id=UUID("00000000-0000-0000-0000-000000000001"),
            )

            self.assertTrue(record.path.is_file())
            self.assertEqual(".gz", record.path.suffix)
            with gzip.open(record.path, "rt", encoding="utf-8") as handle:
                envelope = json.load(handle)

            self.assertEqual(response.payload, envelope["payload"])
            self.assertEqual("repository", envelope["metadata"]["source_endpoint"])
            self.assertEqual("2026-03-10", envelope["metadata"]["api_version"])
            self.assertEqual('"abc"', envelope["metadata"]["headers"]["etag"])
            serialized = json.dumps(envelope, ensure_ascii=False)
            self.assertNotIn("Authorization", serialized)
            self.assertNotIn("Bearer", serialized)

    def test_rejects_secret_query_parameters(self) -> None:
        response = GitHubResponse(
            url="https://api.github.com/repos/example/demo?access_token=secret",
            status_code=200,
            headers={},
            payload={},
            fetched_at=datetime.now(timezone.utc),
        )
        with tempfile.TemporaryDirectory() as temporary:
            store = RawStore(Path(temporary))
            with self.assertRaisesRegex(RawStoreError, "secret query"):
                store.save(
                    response,
                    endpoint_name="repository",
                    repository_full_name="example/demo",
                    collection_run_id=UUID(
                        "00000000-0000-0000-0000-000000000001"
                    ),
                )


if __name__ == "__main__":
    unittest.main()
