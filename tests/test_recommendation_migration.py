from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from oss_mentor.sqlite_store import SQLiteCandidateStore

ROOT = Path(__file__).resolve().parents[1]


class RecommendationMigrationTests(unittest.TestCase):
    def test_010_creates_snapshot_tables_and_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = SQLiteCandidateStore(
                Path(temporary) / "migration.sqlite3",
                ROOT / "db" / "sqlite" / "001_mvp.sql",
            )
            store.initialize()
            with store.connect() as connection:
                tables = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )}
                indexes = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                )}
                columns = {row[1] for row in connection.execute(
                    "PRAGMA table_info(recommendation_run_item)"
                )}
        self.assertTrue({"recommendation_run", "recommendation_run_item"}.issubset(tables))
        self.assertIn("recommendation_run_context_idx", indexes)
        self.assertTrue({"raw_score", "final_score", "raw_rank", "final_rank"}.issubset(columns))


if __name__ == "__main__":
    unittest.main()
