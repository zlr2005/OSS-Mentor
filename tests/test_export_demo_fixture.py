from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scripts.export_demo_fixture import export_fixture


FIXTURE_DATABASE = Path("fixtures/oss_mentor_demo.sqlite3")


def _source_with_body(tmp_path: Path) -> Path:
    source = tmp_path / "source.sqlite3"
    shutil.copyfile(FIXTURE_DATABASE, source)
    with closing(sqlite3.connect(source)) as connection:
        with connection:
            connection.execute(
                """
                UPDATE task_candidate
                SET body_text = 'Public Issue reproduction steps'
                WHERE task_candidate_id = (
                    SELECT MIN(task_candidate_id) FROM task_candidate
                )
                """
            )
    return source


class ExportDemoFixtureTests(unittest.TestCase):
    def test_export_clears_issue_bodies_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _source_with_body(root)
            output = root / "without-bodies.sqlite3"

            export_fixture(source, output, per_language=8, all_candidates=True)

            with closing(sqlite3.connect(output)) as connection:
                self.assertEqual(
                    (0,),
                    connection.execute(
                        "SELECT COUNT(*) FROM task_candidate WHERE body_text IS NOT NULL"
                    ).fetchone(),
                )
                self.assertEqual(
                    ("public-task-metadata-no-bodies-no-feedback",),
                    connection.execute(
                        "SELECT value FROM schema_metadata WHERE key = 'fixture_policy'"
                    ).fetchone(),
                )

    def test_export_can_retain_public_issue_bodies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _source_with_body(root)
            output = root / "with-bodies.sqlite3"

            export_fixture(
                source,
                output,
                per_language=8,
                all_candidates=True,
                include_issue_bodies=True,
            )

            with closing(sqlite3.connect(output)) as connection:
                self.assertEqual(
                    (1,),
                    connection.execute(
                        "SELECT COUNT(*) FROM task_candidate WHERE body_text IS NOT NULL"
                    ).fetchone(),
                )
                self.assertEqual(
                    ("public-issue-content-no-feedback",),
                    connection.execute(
                        "SELECT value FROM schema_metadata WHERE key = 'fixture_policy'"
                    ).fetchone(),
                )
                self.assertEqual(
                    (0,),
                    connection.execute(
                        "SELECT COUNT(*) FROM recommendation_feedback"
                    ).fetchone(),
                )
                self.assertEqual(
                    (0,),
                    connection.execute(
                        "SELECT COUNT(*) FROM recommendation_feedback_event"
                    ).fetchone(),
                )


if __name__ == "__main__":
    unittest.main()
