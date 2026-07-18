from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from oss_mentor.cli import main
from oss_mentor.collector.github_client import RateLimitExceeded


class CliTests(unittest.TestCase):
    def test_wave_one_dry_run_has_no_network_requirement(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(["collect-repositories", "--wave", "1", "--dry-run"])

        self.assertEqual(0, result)
        plan = json.loads(output.getvalue())
        self.assertEqual("dry-run", plan["mode"])
        self.assertEqual(5, plan["repository_count"])
        self.assertEqual(
            {
                "community_profile",
                "labels",
                "languages",
                "repository",
            },
            {
                request["endpoint"]
                for repository in plan["repositories"]
                for request in repository["requests"]
            },
        )

    def test_real_collection_requires_explicit_network_flag(self) -> None:
        error_output = io.StringIO()
        with redirect_stderr(error_output):
            result = main(
                [
                    "collect-repositories",
                    "--wave",
                    "1",
                    "--repo",
                    "eslint/eslint",
                ]
            )

        self.assertEqual(2, result)
        self.assertIn("--allow-network", error_output.getvalue())

    def test_source_comparison_requires_network_flag(self) -> None:
        error_output = io.StringIO()
        with redirect_stderr(error_output):
            result = main(
                [
                    "compare-issue-sources",
                    "--wave",
                    "1",
                    "--repo",
                    "eslint/eslint",
                ]
            )

        self.assertEqual(2, result)
        self.assertIn("--allow-network", error_output.getvalue())

    def test_candidate_sync_requires_network_flag(self) -> None:
        error_output = io.StringIO()
        with redirect_stderr(error_output):
            result = main(
                [
                    "sync-candidates",
                    "--wave",
                    "1",
                    "--repo",
                    "eslint/eslint",
                ]
            )

        self.assertEqual(2, result)
        self.assertIn("--allow-network", error_output.getvalue())

    def test_all_enabled_candidate_sync_dry_run_does_not_build_clients(self) -> None:
        output = io.StringIO()
        with patch("oss_mentor.cli.GitHubClient") as github_client:
            with redirect_stdout(output):
                result = main(["sync-candidates", "--all-enabled", "--dry-run"])
        self.assertEqual(0, result)
        self.assertEqual(28, json.loads(output.getvalue())["repository_count"])
        github_client.assert_not_called()

    def test_batch_sync_without_token_is_rejected(self) -> None:
        error_output = io.StringIO()
        with patch.dict(os.environ, {"GITHUB_TOKEN": ""}, clear=False):
            with redirect_stderr(error_output):
                result = main(
                    ["sync-candidates", "--all-enabled", "--allow-network"]
                )
        self.assertEqual(2, result)
        self.assertIn("GITHUB_TOKEN", error_output.getvalue())

    def test_batch_sync_continues_after_one_repository_failure(self) -> None:
        class FakeClient:
            def __init__(self, **kwargs):
                self.request_count = 0

        class FakeSynchronizer:
            calls: list[str] = []

            def __init__(self, ecosystems, github, store):
                self.github = github
                self.ecosystems = ecosystems

            def sync(self, repository, **kwargs):
                self.calls.append(repository)
                self.github.request_count += 1
                self.ecosystems.request_count += 1
                if repository == "scikit-learn/scikit-learn":
                    raise OSError("temporary failure")
                return SimpleNamespace(
                    discovered_count=1,
                    hydrated_count=1,
                    timeline_checked_count=1,
                )

        with tempfile.TemporaryDirectory() as temporary:
            output = io.StringIO()
            arguments = [
                "sync-candidates",
                "--wave", "1",
                "--repo", "matplotlib/matplotlib",
                "--repo", "scikit-learn/scikit-learn",
                "--repo", "pytest-dev/pytest",
                "--database", str(Path(temporary) / "batch.sqlite3"),
                "--allow-network",
            ]
            with patch.dict(os.environ, {"GITHUB_TOKEN": "read-only-test-token"}):
                with patch("oss_mentor.cli.GitHubClient", FakeClient), patch(
                    "oss_mentor.cli.EcosystemsClient", FakeClient
                ), patch("oss_mentor.cli.CandidateSynchronizer", FakeSynchronizer):
                    with redirect_stdout(output):
                        result = main(arguments)
        report = json.loads(output.getvalue())
        self.assertEqual(1, result)
        self.assertEqual(2, report["successful_repository_count"])
        self.assertEqual(3, len(FakeSynchronizer.calls))

    def test_rate_limit_stops_remaining_repositories(self) -> None:
        class FakeClient:
            def __init__(self, **kwargs):
                self.request_count = 0

        class LimitedSynchronizer:
            calls: list[str] = []

            def __init__(self, ecosystems, github, store):
                pass

            def sync(self, repository, **kwargs):
                self.calls.append(repository)
                raise RateLimitExceeded("exhausted", reset_at=None, url="https://api.github.test")

        with tempfile.TemporaryDirectory() as temporary:
            output = io.StringIO()
            with patch.dict(os.environ, {"GITHUB_TOKEN": "read-only-test-token"}):
                with patch("oss_mentor.cli.GitHubClient", FakeClient), patch(
                    "oss_mentor.cli.EcosystemsClient", FakeClient
                ), patch("oss_mentor.cli.CandidateSynchronizer", LimitedSynchronizer):
                    with redirect_stdout(output):
                        result = main([
                            "sync-candidates", "--wave", "1",
                            "--database", str(Path(temporary) / "limited.sqlite3"),
                            "--allow-network",
                        ])
        report = json.loads(output.getvalue())
        self.assertEqual(1, result)
        self.assertTrue(report["rate_limited"])
        self.assertEqual(1, len(LimitedSynchronizer.calls))
        self.assertEqual(4, sum(row["status"] == "skipped_rate_limit" for row in report["repositories"]))


if __name__ == "__main__":
    unittest.main()
