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


    @staticmethod
    def _quality_record() -> dict[str, object]:
        return {
            "task_candidate_id": 1,
            "repository": "example/project",
            "primary_language": "Python",
            "is_archived": False,
            "is_disabled": False,
            "issue_number": 7,
            "html_url": "https://github.com/example/project/issues/7",
            "title": "Fix parser bug",
            "body_text": "Steps to reproduce the parser bug.",
            "labels_json": '["bug"]',
            "state": "open",
            "assignment_state": "unassigned",
            "has_linked_open_pr": False,
            "last_activity_at": "2026-01-02T00:00:00+00:00",
            "github_verified_at": "2026-01-03T00:00:00+00:00",
            "candidate_eligibility": "eligible",
            "newcomer_label_signal": True,
            "task_types_json": '["bug_fix"]',
            "text_clarity_score": 80.0,
            "estimated_code_difficulty": 1,
            "estimated_setup_difficulty": 1,
            "estimated_project_context_difficulty": 1,
            "estimated_collaboration_difficulty": 0,
            "estimated_effort_bucket": "half_day",
            "novice_fit_probability": 0.8,
            "newcomer_score": 80.0,
            "growth_value_score": 40.0,
            "feature_evidence_json": '{"source":"test"}',
            "feature_extracted_at": "2026-01-04T00:00:00+00:00",
            "task_feature_version": "task-features-v0.1",
            "requirements": [
                {
                    "skill_name": "Python",
                    "minimum_level": 1,
                    "importance": 1.0,
                    "requirement_source": "repository_primary_language",
                    "feature_version": "task-features-v0.1",
                }
            ],
            "skill_requirement_count": 1,
        }

    def test_data_quality_report_writes_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database_path = root / "quality.sqlite3"
            database_path.touch()
            json_path = root / "reports" / "quality.json"
            markdown_path = root / "docs" / "quality.md"
            store = SimpleNamespace(
                database_path=database_path,
                data_quality_records=lambda: [self._quality_record()],
            )
            output = io.StringIO()

            with patch(
                "oss_mentor.cli._sqlite_store", return_value=store
            ) as store_factory:
                with redirect_stdout(output):
                    result = main(
                        [
                            "report-data-quality",
                            "--database",
                            str(database_path),
                            "--output",
                            str(json_path),
                            "--markdown-output",
                            str(markdown_path),
                        ]
                    )

            self.assertEqual(0, result)
            store_factory.assert_called_once()
            self.assertEqual(
                str(database_path), store_factory.call_args.args[1]
            )

            report = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(
                "data_quality_report_v0.2", report["schema_version"]
            )
            self.assertEqual(
                1,
                report["scope_summary"]["eligible_candidates"][
                    "total_count"
                ],
            )
            self.assertTrue(
                report["acceptance_summary"]["overall_passed"]
            )

            markdown = markdown_path.read_text(encoding="utf-8")
            self.assertIn("# OSS-Mentor 数据质量报告 v0.2", markdown)
            self.assertIn("任务类型覆盖率", markdown)

            summary = json.loads(output.getvalue())
            self.assertEqual(
                "data_quality_report_generated", summary["event"]
            )
            self.assertEqual(str(json_path.resolve()), summary["json_output"])
            self.assertEqual(
                str(markdown_path.resolve()), summary["markdown_output"]
            )

    def test_data_quality_report_without_paths_prints_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database_path = Path(temporary) / "quality.sqlite3"
            database_path.touch()
            store = SimpleNamespace(
                database_path=database_path,
                data_quality_records=lambda: [self._quality_record()],
            )
            output = io.StringIO()

            with patch("oss_mentor.cli._sqlite_store", return_value=store):
                with redirect_stdout(output):
                    result = main(
                        [
                            "report-data-quality",
                            "--database",
                            str(database_path),
                        ]
                    )

            self.assertEqual(0, result)
            summary = json.loads(output.getvalue())
            self.assertEqual(
                "data_quality_report_generated", summary["event"]
            )
            self.assertIsNone(summary["json_output"])
            self.assertIsNone(summary["markdown_output"])
            self.assertEqual(
                1,
                summary["scope_summary"]["eligible_candidates"][
                    "total_count"
                ],
            )
            self.assertEqual(1.0, summary["task_type_coverage_rate"])
            self.assertEqual(
                1.0, summary["skill_requirement_coverage_rate"]
            )



if __name__ == "__main__":
    unittest.main()
