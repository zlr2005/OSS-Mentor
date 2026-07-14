from __future__ import annotations

import json
import unittest
from email.message import Message

from oss_mentor.collector.ecosystems_client import (
    EcosystemsApiError,
    EcosystemsClient,
)


class FakeResponse:
    def __init__(self, url: str, payload: object) -> None:
        self._url = url
        self._payload = json.dumps(payload).encode("utf-8")
        self.status = 200
        self.headers = Message()

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def geturl(self) -> str:
        return self._url

    def read(self) -> bytes:
        return self._payload


class RecordingOpener:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.request = None

    def __call__(self, request, *, timeout: int):
        self.request = request
        return self.response


class EcosystemsClientTests(unittest.TestCase):
    def test_repository_name_is_path_encoded(self) -> None:
        expected = (
            "https://issues.ecosyste.ms/api/v1/hosts/GitHub/repositories/"
            "eslint%2Feslint/issues?state=open&pull_request=false&sort=updated_at&"
            "order=desc&per_page=3"
        )
        opener = RecordingOpener(FakeResponse(expected, []))
        client = EcosystemsClient(
            api_base="https://issues.ecosyste.ms/api/v1",
            user_agent="OSS-Mentor-test/0",
            opener=opener,
        )

        response = client.get_issues("eslint/eslint", per_page=3)

        self.assertEqual(expected, response.url)
        self.assertEqual(expected, opener.request.full_url)

    def test_rejects_cross_origin_url(self) -> None:
        client = EcosystemsClient(
            api_base="https://issues.ecosyste.ms/api/v1",
            user_agent="OSS-Mentor-test/0",
        )
        with self.assertRaisesRegex(EcosystemsApiError, "outside API origin"):
            client.get("https://example.com/items")


if __name__ == "__main__":
    unittest.main()
