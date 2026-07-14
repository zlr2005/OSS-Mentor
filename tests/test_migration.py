from __future__ import annotations

import unittest
from pathlib import Path
import re


class InitialMigrationTests(unittest.TestCase):
    def test_required_tables_and_dependency_order(self) -> None:
        path = Path("db/migrations/001_initial_p0.sql")
        sql = path.read_text(encoding="utf-8")
        required_tables = {
            "data_collection_run",
            "raw_object",
            "github_actor",
            "repository",
            "repository_snapshot",
            "task",
            "task_snapshot",
            "developer",
            "developer_profile_snapshot",
            "contribution_attempt",
            "pull_request_fact",
            "recommendation_session",
            "recommendation_impression",
            "interaction_event",
        }
        for table in required_tables:
            self.assertIn(f"CREATE TABLE {table} (", sql)

        create_impression = sql.index("CREATE TABLE recommendation_impression (")
        add_impression_fk = sql.index(
            "ADD CONSTRAINT contribution_attempt_origin_impression_fk"
        )
        self.assertLess(create_impression, add_impression_fk)
        self.assertTrue(sql.lstrip().startswith("BEGIN;"))
        self.assertTrue(sql.rstrip().endswith("COMMIT;"))

    def test_referenced_tables_are_created_before_foreign_keys(self) -> None:
        sql = Path("db/migrations/001_initial_p0.sql").read_text(encoding="utf-8")
        create_positions = {
            match.group(1): match.start()
            for match in re.finditer(
                r"CREATE TABLE (?:oss_mentor_private\.)?([a-z_]+) \(", sql
            )
        }
        references = re.finditer(
            r"REFERENCES (?:oss_mentor\.)?([a-z_]+)(?:\([^)]*\))?", sql
        )
        for reference in references:
            table = reference.group(1)
            self.assertIn(table, create_positions, f"missing CREATE TABLE for {table}")
            self.assertLess(
                create_positions[table],
                reference.start(),
                f"{table} is referenced before it is created",
            )


if __name__ == "__main__":
    unittest.main()
