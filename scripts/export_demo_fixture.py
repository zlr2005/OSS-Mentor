"""Export a small, sanitized SQLite fixture from a local OSS-Mentor database."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


DEFAULT_SOURCE = Path("data/oss_mentor.sqlite3")
DEFAULT_OUTPUT = Path("fixtures/oss_mentor_demo.sqlite3")


def _selected_task_ids(connection: sqlite3.Connection, per_language: int) -> set[int]:
    languages = connection.execute(
        """
        SELECT DISTINCT repository.primary_language
        FROM task_candidate
        JOIN repository USING (repository_id)
        WHERE task_candidate.candidate_eligibility = 'eligible'
          AND repository.primary_language IS NOT NULL
        ORDER BY repository.primary_language COLLATE NOCASE
        """
    ).fetchall()

    selected: set[int] = set()
    first_pass = (per_language + 1) // 2
    rankings = (
        ("newcomer_score", first_pass),
        ("growth_value_score", per_language - first_pass),
    )
    for (language,) in languages:
        language_ids: list[int] = []
        for score_column, limit in rankings:
            if limit <= 0:
                continue
            rows = connection.execute(
                f"""
                SELECT task_candidate.task_candidate_id
                FROM task_candidate
                JOIN repository USING (repository_id)
                WHERE task_candidate.candidate_eligibility = 'eligible'
                  AND repository.primary_language = ?
                  AND task_candidate.{score_column} IS NOT NULL
                ORDER BY task_candidate.{score_column} DESC,
                         task_candidate.task_candidate_id
                LIMIT ?
                """,
                (language, limit),
            ).fetchall()
            language_ids.extend(row[0] for row in rows)

        # Ranking lists can overlap. Fill the remaining slots deterministically.
        language_ids = list(dict.fromkeys(language_ids))
        if len(language_ids) < per_language:
            rows = connection.execute(
                """
                SELECT task_candidate.task_candidate_id
                FROM task_candidate
                JOIN repository USING (repository_id)
                WHERE task_candidate.candidate_eligibility = 'eligible'
                  AND repository.primary_language = ?
                ORDER BY COALESCE(task_candidate.newcomer_score, 0) DESC,
                         COALESCE(task_candidate.growth_value_score, 0) DESC,
                         task_candidate.task_candidate_id
                """,
                (language,),
            ).fetchall()
            for (task_id,) in rows:
                if task_id not in language_ids:
                    language_ids.append(task_id)
                if len(language_ids) == per_language:
                    break
        selected.update(language_ids[:per_language])
    return selected


def export_fixture(source: Path, output: Path, per_language: int) -> None:
    source = source.resolve()
    output = output.resolve()
    if source == output:
        raise ValueError("source and output databases must be different")
    if not source.is_file():
        raise FileNotFoundError(f"source database does not exist: {source}")

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    source_connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    output_connection = sqlite3.connect(output)
    try:
        source_connection.backup(output_connection)
        output_connection.execute("PRAGMA foreign_keys = OFF")
        selected = _selected_task_ids(output_connection, per_language)
        if not selected:
            raise RuntimeError("source database has no eligible scored candidates")

        placeholders = ",".join("?" for _ in selected)
        parameters = tuple(sorted(selected))

        # Feedback may represent local user behavior and is never part of the fixture.
        output_connection.execute("DELETE FROM recommendation_feedback_event")
        output_connection.execute("DELETE FROM recommendation_feedback")
        output_connection.execute(
            f"DELETE FROM task_skill_requirement WHERE task_candidate_id NOT IN ({placeholders})",
            parameters,
        )
        output_connection.execute(
            f"DELETE FROM task_candidate WHERE task_candidate_id NOT IN ({placeholders})",
            parameters,
        )
        output_connection.execute(
            """
            DELETE FROM repository
            WHERE repository_id NOT IN (
                SELECT DISTINCT repository_id FROM task_candidate
            )
            """
        )
        output_connection.execute(
            """
            DELETE FROM developer_skill
            WHERE developer_profile_id IN (
                SELECT developer_profile_id
                FROM developer_profile
                WHERE profile_source != 'demo'
            )
            """
        )
        output_connection.execute("DELETE FROM developer_profile WHERE profile_source != 'demo'")

        # Keep only public fields required by the demo UI and precomputed ranking data.
        output_connection.execute(
            """
            UPDATE task_candidate
            SET github_issue_id = NULL,
                author_association = NULL,
                body_text = NULL,
                warnings_json = '[]'
            """
        )
        output_connection.execute(
            """
            INSERT INTO schema_metadata(key, value)
            VALUES ('fixture_kind', 'sanitized-demo')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """
        )
        output_connection.execute(
            """
            INSERT INTO schema_metadata(key, value)
            VALUES ('fixture_policy', 'public-task-metadata-no-bodies-no-feedback')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """
        )
        output_connection.commit()
        output_connection.execute("VACUUM")
    finally:
        output_connection.close()
        source_connection.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--per-language",
        type=int,
        default=8,
        help="maximum eligible candidates retained per language (default: 8)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.per_language < 1:
        raise SystemExit("--per-language must be at least 1")
    export_fixture(args.source, args.output, args.per_language)
    print(f"Wrote sanitized demo fixture: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
