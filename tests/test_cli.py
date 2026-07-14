from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout

from oss_mentor.cli import main


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


if __name__ == "__main__":
    unittest.main()
