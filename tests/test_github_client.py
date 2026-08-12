from __future__ import annotations

import json
import io
import unittest
import urllib.error
from email.message import Message

from oss_mentor.collector.github_client import (
    GitHubApiError,
    GitHubClient,
    RateLimitExceeded,
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


class RetryOnceOpener:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls = 0

    def __call__(self, request, *, timeout: int):
        self.calls += 1
        if self.calls == 1:
            raise urllib.error.URLError("temporary")
        return self.response


class ExceptionSequenceOpener:
    def __init__(self, items):
        self.items = list(items)
        self.calls = 0

    def __call__(self, request, *, timeout: int):
        self.calls += 1
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def http_error(code: int, *, headers: dict[str, str] | None = None):
    message = Message()
    for name, value in (headers or {}).items():
        message[name] = value
    return urllib.error.HTTPError(
        "https://api.github.com/repos/example/demo",
        code,
        "fixture",
        message,
        io.BytesIO(b'{"message":"fixture"}'),
    )


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
        self.assertEqual(2, client.request_count)
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

    def test_request_count_includes_retry_attempts(self) -> None:
        url = "https://api.github.com/repos/example/demo"
        opener = RetryOnceOpener(FakeResponse(url=url, payload={"id": 1}))
        client = GitHubClient(
            api_base="https://api.github.com",
            api_version="2026-03-10",
            user_agent="OSS-Mentor-test/0",
            token="test-token-not-persisted",
            max_retries=1,
            backoff_base_seconds=0,
            opener=opener,
            sleep=lambda _: None,
            random_source=lambda: 0,
        )
        client.get("/repos/example/demo")
        self.assertEqual(2, client.request_count)

    def test_403_primary_rate_limit_is_not_retried(self) -> None:
        opener = ExceptionSequenceOpener(
            [
                http_error(
                    403,
                    headers={
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": "1785326400",
                    },
                )
            ]
        )
        client = GitHubClient(
            api_base="https://api.github.com",
            api_version="2026-03-10",
            user_agent="OSS-Mentor-test/0",
            token=None,
            opener=opener,
            sleep=lambda _: None,
        )
        with self.assertRaises(RateLimitExceeded):
            client.get("/repos/example/demo")
        self.assertEqual(1, opener.calls)
        self.assertEqual(0, client.rate_limit_remaining)
        self.assertIsNotNone(client.rate_limit_reset_at)

    def test_404_is_not_retried(self) -> None:
        opener = ExceptionSequenceOpener([http_error(404)])
        client = GitHubClient(
            api_base="https://api.github.com",
            api_version="2026-03-10",
            user_agent="OSS-Mentor-test/0",
            token=None,
            opener=opener,
            sleep=lambda _: None,
        )
        with self.assertRaises(GitHubApiError) as raised:
            client.get("/repos/example/demo")
        self.assertEqual(404, raised.exception.status_code)
        self.assertEqual(1, opener.calls)

    def test_429_is_retried_with_a_bound(self) -> None:
        opener = ExceptionSequenceOpener([http_error(429), http_error(429)])
        client = GitHubClient(
            api_base="https://api.github.com",
            api_version="2026-03-10",
            user_agent="OSS-Mentor-test/0",
            token=None,
            max_retries=1,
            backoff_base_seconds=0,
            opener=opener,
            sleep=lambda _: None,
            random_source=lambda: 0,
        )
        with self.assertRaises(GitHubApiError) as raised:
            client.get("/repos/example/demo")
        self.assertEqual(429, raised.exception.status_code)
        self.assertEqual(2, opener.calls)
        self.assertEqual(1, client.retry_count)

    def test_5xx_and_timeout_recover_on_retry(self) -> None:
        for failure in (http_error(502), TimeoutError("fixture timeout")):
            with self.subTest(failure=type(failure).__name__):
                response = FakeResponse(
                    url="https://api.github.com/repos/example/demo",
                    payload={"id": 1},
                )
                opener = ExceptionSequenceOpener([failure, response])
                client = GitHubClient(
                    api_base="https://api.github.com",
                    api_version="2026-03-10",
                    user_agent="OSS-Mentor-test/0",
                    token=None,
                    max_retries=1,
                    backoff_base_seconds=0,
                    opener=opener,
                    sleep=lambda _: None,
                    random_source=lambda: 0,
                )
                result = client.get("/repos/example/demo")
                self.assertEqual(1, result.payload["id"])
                self.assertEqual(2, opener.calls)
                self.assertEqual(1, client.retry_count)


if __name__ == "__main__":
    unittest.main()
