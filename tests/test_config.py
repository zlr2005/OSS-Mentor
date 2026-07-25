from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from oss_mentor.collector.config import ConfigError, Settings, load_repositories


class RepositoryConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings.from_env(Path.cwd())

    def test_wave_selection_and_labels(self) -> None:
        wave_one = load_repositories(self.settings.repository_config_path, wave=1)
        wave_two = load_repositories(self.settings.repository_config_path, wave=2)
        wave_three = load_repositories(self.settings.repository_config_path, wave=3)
        wave_four = load_repositories(self.settings.repository_config_path, wave=4)
        wave_five = load_repositories(self.settings.repository_config_path, wave=5)

        self.assertEqual(5, len(wave_one))
        self.assertEqual(5, len(wave_two))
        self.assertEqual(12, len(wave_three))
        self.assertEqual(5, len(wave_four))
        self.assertEqual(1, len(wave_five))
        self.assertEqual(
            28,
            len(
                {
                    repo.full_name
                    for repo in [
                        *wave_one,
                        *wave_two,
                        *wave_three,
                        *wave_four,
                        *wave_five,
                    ]
                }
            ),
        )

        matplotlib = next(
            repository
            for repository in wave_one
            if repository.full_name == "matplotlib/matplotlib"
        )
        self.assertIn("Good first issue", matplotlib.candidate_labels)
        self.assertEqual("NOASSERTION", matplotlib.license_spdx)

    def test_duplicate_repository_is_rejected(self) -> None:
        original = self.settings.repository_config_path.read_text(encoding="utf-8")
        lines = original.splitlines()
        duplicated = "\n".join([*lines, lines[1], ""])

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "duplicate.csv"
            path.write_text(duplicated, encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "duplicate repository"):
                load_repositories(path)


if __name__ == "__main__":
    unittest.main()
