from __future__ import annotations

import json
import unittest
from email.message import Message

from oss_mentor.collector.github_client import (
    GitHubApiError,
    GitHubClient,
    parse_link_header,
)


class FakeResponse:
    def __init__(
        self,
        *,
        url: str,
        payload: object,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._url = url
        self._payload = json.dumps(payload).encode("utf-8")
        self.status = status
        self.headers = Message()
        for name, value in (headers or {}).items():
            self.headers[name] = value

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def geturl(self) -> str:
        return self._url

    def read(self) -> bytes:
        return self._payload


class SequenceOpener:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, *, timeout: int):
        self.requests.append((request, timeout))
        if not self.responses:
            raise AssertionError("unexpected extra request")
        return self.responses.pop(0)


class GitHubClientTests(unittest.TestCase):
    def test_parse_link_header(self) -> None:
        value = (
            '<https://api.github.com/items?page=2>; rel="next", '
            '<https://api.github.com/items?page=5>; rel="last"'
        )
        self.assertEqual(
            {
                "next": "https://api.github.com/items?page=2",
                "last": "https://api.github.com/items?page=5",
            },
            parse_link_header(value),
        )

    def test_iter_pages_follows_only_github_origin(self) -> None:
        first_url = "https://api.github.com/repos/example/demo/labels?per_page=100"
        second_url = "https://api.github.com/repos/example/demo/labels?page=2"
        opener = SequenceOpener(
            [
                FakeResponse(
                    url=first_url,
                    payload=[{"name": "help wanted"}],
                    headers={"Link": f'<{second_url}>; rel="next"'},
                ),
                FakeResponse(url=second_url, payload=[{"name": "bug"}]),
            ]
        )
        client = GitHubClient(
            api_base="https://api.github.com",
            api_version="2026-03-10",
            user_agent="OSS-Mentor-test/0",
            token="test-token-not-persisted",
            opener=opener,
        )

        pages = list(
            client.iter_pages(
                "/repos/example/demo/labels", params={"per_page": 100}
            )
        )

        self.assertEqual(2, len(pages))
        self.assertEqual("help wanted", pages[0].payload[0]["name"])
        self.assertEqual("bug", pages[1].payload[0]["name"])
        self.assertEqual(2, len(opener.requests))
        authorization = opener.requests[0][0].get_header("Authorization")
        self.assertEqual("Bearer test-token-not-persisted", authorization)

    def test_rejects_cross_origin_pagination_url(self) -> None:
        client = GitHubClient(
            api_base="https://api.github.com",
            api_version="2026-03-10",
            user_agent="OSS-Mentor-test/0",
            token=None,
        )
        with self.assertRaisesRegex(GitHubApiError, "outside API origin"):
            client.get("https://example.com/steal")


if __name__ == "__main__":
    unittest.main()

