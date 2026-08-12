#!/usr/bin/env python3
"""Build a reproducible difficulty-rules-v0.2.1 SQLite snapshot locally.

This wrapper intentionally reuses the repository-native CLI pipeline:

    python -m oss_mentor extract-features --database <temporary copy>

It does NOT reimplement task-feature extraction, difficulty rules, skill
inference, or SQLite persistence.

Default source:
    data/oss_mentor_task_features_v0.2_round3.sqlite3

Default output:
    data/oss_mentor_task_features_v0.2.1.sqlite3

Default report:
    data/reports/difficulty_snapshot_v0.2.1_build.json

Safety:
- source database is opened read-only for validation;
- output is refused if it already exists unless --overwrite is supplied;
- work is performed on a temporary copy in the output directory;
- task identity, non-feature candidate fields, repository metadata (except
  updated_at), task types, migration set, and skill identity set are checked;
- the source SHA-256 is checked again after the run;
- temporary database is atomically renamed only after validation succeeds;
- no network APIs are called by this wrapper.
"""

from __future__ import annotations

import argparse
import hashlib
from contextlib import closing
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from oss_mentor.task_features import (
    DIFFICULTY_FORMULA_VERSION,
    TASK_FEATURE_VERSION,
)

EXPECTED_TASK_FEATURE_VERSION = "task-features-v0.3"
EXPECTED_DIFFICULTY_FORMULA_VERSION = "difficulty-rules-v0.2.1"

DEFAULT_SOURCE = Path("data/oss_mentor_task_features_v0.2_round3.sqlite3")
DEFAULT_OUTPUT = Path("data/oss_mentor_task_features_v0.2.1.sqlite3")
DEFAULT_REPORT = Path("data/reports/difficulty_snapshot_v0.2.1_build.json")

EXPECTED_ACTIVE_COUNT = 1464
EXPECTED_ELIGIBLE_COUNT = 608
EXPECTED_NEWCOMER_ELIGIBLE_COUNT = 264

# Exactly the columns mutated by SQLiteCandidateStore.update_features().
FEATURE_MUTABLE_COLUMNS = {
    "has_reproduction_steps",
    "has_acceptance_criteria",
    "has_expected_behavior",
    "has_affected_module_hint",
    "task_types_json",
    "text_clarity_score",
    "estimated_code_difficulty",
    "estimated_setup_difficulty",
    "estimated_project_context_difficulty",
    "estimated_collaboration_difficulty",
    "estimated_effort_bucket",
    "novice_fit_probability",
    "newcomer_score",
    "growth_value_score",
    "feature_evidence_json",
    "feature_extracted_at",
    "task_feature_version",
}


class SnapshotBuildError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fatal(message: str) -> "NoReturn":
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def readonly_connection(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise SnapshotBuildError(f"SQLite database does not exist: {resolved}")
    connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")]


def applied_migrations(connection: sqlite3.Connection) -> set[str]:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migration'"
    ).fetchone()
    if not exists:
        raise SnapshotBuildError("source database has no schema_migration table")
    return {
        str(row["migration_name"])
        for row in connection.execute(
            "SELECT migration_name FROM schema_migration ORDER BY migration_name"
        )
    }


def current_migration_files(repo_root: Path) -> set[str]:
    migration_dir = repo_root / "db" / "sqlite"
    if not migration_dir.is_dir():
        raise SnapshotBuildError(f"migration directory does not exist: {migration_dir}")
    return {path.name for path in migration_dir.glob("*.sql")}


def candidate_summary(connection: sqlite3.Connection) -> dict[str, int]:
    total = int(connection.execute("SELECT COUNT(*) FROM task_candidate").fetchone()[0])
    active = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM task_candidate tc
            JOIN repository r USING(repository_id)
            WHERE COALESCE(r.is_archived, 0) = 0
              AND COALESCE(r.is_disabled, 0) = 0
            """
        ).fetchone()[0]
    )
    eligible = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM task_candidate tc
            JOIN repository r USING(repository_id)
            WHERE COALESCE(r.is_archived, 0) = 0
              AND COALESCE(r.is_disabled, 0) = 0
              AND tc.candidate_eligibility = 'eligible'
            """
        ).fetchone()[0]
    )
    newcomer = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM task_candidate tc
            JOIN repository r USING(repository_id)
            WHERE COALESCE(r.is_archived, 0) = 0
              AND COALESCE(r.is_disabled, 0) = 0
              AND tc.candidate_eligibility = 'eligible'
              AND COALESCE(tc.newcomer_label_signal, 0) = 1
            """
        ).fetchone()[0]
    )
    return {
        "total_candidates": total,
        "active_candidates": active,
        "eligible_candidates": eligible,
        "newcomer_eligible_candidates": newcomer,
    }


def candidate_ids(connection: sqlite3.Connection) -> list[int]:
    return [
        int(row["task_candidate_id"])
        for row in connection.execute(
            "SELECT task_candidate_id FROM task_candidate ORDER BY task_candidate_id"
        )
    ]


def rows_by_id(
    connection: sqlite3.Connection,
    table: str,
    id_column: str,
) -> dict[Any, dict[str, Any]]:
    columns = table_columns(connection, table)
    rows = connection.execute(
        f"SELECT {', '.join(columns)} FROM {table} ORDER BY {id_column}"
    ).fetchall()
    return {row[id_column]: dict(row) for row in rows}


def compare_non_feature_candidate_fields(
    source: sqlite3.Connection,
    after: sqlite3.Connection,
) -> list[dict[str, Any]]:
    source_rows = rows_by_id(source, "task_candidate", "task_candidate_id")
    after_rows = rows_by_id(after, "task_candidate", "task_candidate_id")
    if set(source_rows) != set(after_rows):
        raise SnapshotBuildError("task_candidate identity set changed")

    columns = table_columns(source, "task_candidate")
    immutable_columns = [
        column for column in columns if column not in FEATURE_MUTABLE_COLUMNS
    ]
    changes: list[dict[str, Any]] = []
    for task_id in sorted(source_rows):
        before = source_rows[task_id]
        current = after_rows[task_id]
        changed = {
            column: {"before": before[column], "after": current[column]}
            for column in immutable_columns
            if before[column] != current[column]
        }
        if changed:
            changes.append({"task_candidate_id": int(task_id), "changes": changed})
    return changes


def compare_repository_metadata(
    source: sqlite3.Connection,
    after: sqlite3.Connection,
) -> list[dict[str, Any]]:
    source_rows = rows_by_id(source, "repository", "repository_id")
    after_rows = rows_by_id(after, "repository", "repository_id")
    if set(source_rows) != set(after_rows):
        raise SnapshotBuildError("repository identity set changed")
    columns = [
        column
        for column in table_columns(source, "repository")
        if column != "updated_at"
    ]
    changes: list[dict[str, Any]] = []
    for repository_id in sorted(source_rows):
        before = source_rows[repository_id]
        current = after_rows[repository_id]
        changed = {
            column: {"before": before[column], "after": current[column]}
            for column in columns
            if before[column] != current[column]
        }
        if changed:
            changes.append(
                {"repository_id": int(repository_id), "changes": changed}
            )
    return changes


def parse_string_list(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise SnapshotBuildError(f"invalid JSON list: {value!r}") from exc
    if not isinstance(value, list):
        raise SnapshotBuildError(f"expected JSON list, got {type(value).__name__}")
    return tuple(str(item) for item in value)


def task_type_changes(
    source: sqlite3.Connection,
    after: sqlite3.Connection,
) -> list[dict[str, Any]]:
    before_rows = {
        int(row["task_candidate_id"]): parse_string_list(row["task_types_json"])
        for row in source.execute(
            "SELECT task_candidate_id, task_types_json FROM task_candidate "
            "ORDER BY task_candidate_id"
        )
    }
    after_rows = {
        int(row["task_candidate_id"]): parse_string_list(row["task_types_json"])
        for row in after.execute(
            "SELECT task_candidate_id, task_types_json FROM task_candidate "
            "ORDER BY task_candidate_id"
        )
    }
    output: list[dict[str, Any]] = []
    for task_id in sorted(before_rows):
        if before_rows[task_id] != after_rows[task_id]:
            output.append(
                {
                    "task_candidate_id": task_id,
                    "before": list(before_rows[task_id]),
                    "after": list(after_rows[task_id]),
                }
            )
    return output


def skill_maps(
    connection: sqlite3.Connection,
) -> dict[tuple[int, str], dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT task_candidate_id, skill_name, minimum_level,
               importance, requirement_source, feature_version
        FROM task_skill_requirement
        ORDER BY task_candidate_id, LOWER(skill_name)
        """
    ).fetchall()
    return {
        (int(row["task_candidate_id"]), str(row["skill_name"])): dict(row)
        for row in rows
    }


def compare_skills(
    source: sqlite3.Connection,
    after: sqlite3.Connection,
) -> dict[str, Any]:
    before = skill_maps(source)
    current = skill_maps(after)
    before_ids = set(before)
    after_ids = set(current)
    minimum_level_changes: list[dict[str, Any]] = []
    metadata_changes: list[dict[str, Any]] = []
    for key in sorted(before_ids & after_ids):
        left = before[key]
        right = current[key]
        if left["minimum_level"] != right["minimum_level"]:
            minimum_level_changes.append(
                {
                    "task_candidate_id": key[0],
                    "skill_name": key[1],
                    "before": int(left["minimum_level"]),
                    "after": int(right["minimum_level"]),
                }
            )
        static_keys = ("importance", "requirement_source")
        changed = {
            field: {"before": left[field], "after": right[field]}
            for field in static_keys
            if left[field] != right[field]
        }
        if changed:
            metadata_changes.append(
                {
                    "task_candidate_id": key[0],
                    "skill_name": key[1],
                    "changes": changed,
                }
            )
    return {
        "identity_only_source": [
            {"task_candidate_id": task_id, "skill_name": skill}
            for task_id, skill in sorted(before_ids - after_ids)
        ],
        "identity_only_after": [
            {"task_candidate_id": task_id, "skill_name": skill}
            for task_id, skill in sorted(after_ids - before_ids)
        ],
        "minimum_level_change_count": len(minimum_level_changes),
        "minimum_level_changes": minimum_level_changes,
        "static_metadata_change_count": len(metadata_changes),
        "static_metadata_changes": metadata_changes,
    }


def feature_version_validation(
    connection: sqlite3.Connection,
) -> dict[str, Any]:
    task_version_counts: Counter[str] = Counter()
    formula_counts: Counter[str] = Counter()
    malformed: list[int] = []

    for row in connection.execute(
        """
        SELECT task_candidate_id, task_feature_version, feature_evidence_json
        FROM task_candidate
        ORDER BY task_candidate_id
        """
    ):
        task_version_counts[str(row["task_feature_version"] or "")] += 1
        try:
            evidence = json.loads(row["feature_evidence_json"] or "{}")
            assessment = evidence.get("difficulty_assessment") or {}
            formula = str(assessment.get("formula_version") or "")
        except (json.JSONDecodeError, AttributeError, TypeError):
            malformed.append(int(row["task_candidate_id"]))
            formula = "<malformed>"
        formula_counts[formula] += 1

    return {
        "task_feature_version_counts": dict(sorted(task_version_counts.items())),
        "difficulty_formula_version_counts": dict(sorted(formula_counts.items())),
        "malformed_feature_evidence_task_ids": malformed,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a local v0.2.1 task-feature snapshot by copying the fixed "
            "v0.2 round3 snapshot and invoking the repository-native "
            "extract-features CLI."
        )
    )
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--expected-active", type=int, default=EXPECTED_ACTIVE_COUNT)
    parser.add_argument("--expected-eligible", type=int, default=EXPECTED_ELIGIBLE_COUNT)
    parser.add_argument(
        "--expected-newcomer-eligible",
        type=int,
        default=EXPECTED_NEWCOMER_ELIGIBLE_COUNT,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    source_path = (repo_root / args.source).resolve() if not Path(args.source).is_absolute() else Path(args.source).resolve()
    output_path = (repo_root / args.output).resolve() if not Path(args.output).is_absolute() else Path(args.output).resolve()
    report_path = (repo_root / args.report).resolve() if not Path(args.report).is_absolute() else Path(args.report).resolve()

    if TASK_FEATURE_VERSION != EXPECTED_TASK_FEATURE_VERSION:
        fatal(
            "TASK_FEATURE_VERSION mismatch: "
            f"expected {EXPECTED_TASK_FEATURE_VERSION}, got {TASK_FEATURE_VERSION}"
        )
    if DIFFICULTY_FORMULA_VERSION != EXPECTED_DIFFICULTY_FORMULA_VERSION:
        fatal(
            "DIFFICULTY_FORMULA_VERSION mismatch: "
            f"expected {EXPECTED_DIFFICULTY_FORMULA_VERSION}, "
            f"got {DIFFICULTY_FORMULA_VERSION}"
        )
    if not source_path.is_file():
        fatal(f"source database does not exist: {source_path}")
    if source_path == output_path:
        fatal("source and output database paths must be different")
    if output_path.exists() and not args.overwrite:
        fatal(
            f"output database already exists: {output_path}. "
            "Refusing to overwrite; use --overwrite only intentionally."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(
        f".{output_path.name}.tmp-{uuid4().hex}"
    )

    source_hash_before = sha256_file(source_path)
    cli_stdout = ""
    cli_stderr = ""

    try:
        with closing(readonly_connection(source_path)) as source:
            source_counts = candidate_summary(source)
            source_ids = candidate_ids(source)
            source_migrations = applied_migrations(source)

        repo_migrations = current_migration_files(repo_root)
        if source_migrations != repo_migrations:
            raise SnapshotBuildError(
                "migration preflight failed. The source snapshot migration set "
                "does not exactly match current db/sqlite/*.sql. "
                f"source={sorted(source_migrations)}, current={sorted(repo_migrations)}. "
                "Stopping so extract-features cannot apply an unplanned migration."
            )

        if source_counts["eligible_candidates"] != args.expected_eligible:
            raise SnapshotBuildError(
                f"source eligible count must be {args.expected_eligible}, "
                f"got {source_counts['eligible_candidates']}"
            )

        if source_counts["active_candidates"] != args.expected_active:
            print(
                "WARNING: source active candidate count differs from expected "
                f"{args.expected_active}: {source_counts['active_candidates']}",
                file=sys.stderr,
            )
        if (
            source_counts["newcomer_eligible_candidates"]
            != args.expected_newcomer_eligible
        ):
            print(
                "WARNING: source newcomer eligible count differs from expected "
                f"{args.expected_newcomer_eligible}: "
                f"{source_counts['newcomer_eligible_candidates']}",
                file=sys.stderr,
            )

        shutil.copy2(source_path, temporary_path)

        command = [
            sys.executable,
            "-m",
            "oss_mentor",
            "extract-features",
            "--database",
            str(temporary_path),
        ]
        completed = subprocess.run(
            command,
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
        )
        cli_stdout = completed.stdout
        cli_stderr = completed.stderr
        if completed.returncode != 0:
            raise SnapshotBuildError(
                "repository-native extract-features command failed with exit "
                f"code {completed.returncode}.\nSTDOUT:\n{cli_stdout}\n"
                f"STDERR:\n{cli_stderr}"
            )

        with closing(readonly_connection(source_path)) as source, closing(
            readonly_connection(temporary_path)
        ) as after:
            after_counts = candidate_summary(after)
            after_ids = candidate_ids(after)
            after_migrations = applied_migrations(after)

            if source_ids != after_ids:
                raise SnapshotBuildError(
                    "task_candidate identity set/order changed after extraction"
                )
            if source_counts != after_counts:
                raise SnapshotBuildError(
                    f"candidate counts changed: source={source_counts}, "
                    f"after={after_counts}"
                )
            if after_migrations != source_migrations:
                raise SnapshotBuildError(
                    "schema migration set changed during extraction"
                )

            non_feature_changes = compare_non_feature_candidate_fields(source, after)
            if non_feature_changes:
                raise SnapshotBuildError(
                    "non-feature task_candidate fields changed; first changes: "
                    + json.dumps(non_feature_changes[:3], ensure_ascii=False)
                )

            repository_changes = compare_repository_metadata(source, after)
            if repository_changes:
                raise SnapshotBuildError(
                    "repository metadata other than updated_at changed; "
                    "this would make the snapshot input drift from v0.2. "
                    "First changes: "
                    + json.dumps(repository_changes[:3], ensure_ascii=False)
                )

            types_changed = task_type_changes(source, after)
            if types_changed:
                raise SnapshotBuildError(
                    "task_types changed even though B2 rules are out of scope; "
                    f"count={len(types_changed)}, first="
                    + json.dumps(types_changed[:5], ensure_ascii=False)
                )

            skill_comparison = compare_skills(source, after)
            if skill_comparison["identity_only_source"] or skill_comparison[
                "identity_only_after"
            ]:
                raise SnapshotBuildError(
                    "skill requirement identity set changed. "
                    "Only minimum levels are expected to propagate from B3."
                )
            if skill_comparison["static_metadata_change_count"]:
                raise SnapshotBuildError(
                    "skill requirement importance/source changed unexpectedly"
                )

            version_validation = feature_version_validation(after)
            malformed = version_validation[
                "malformed_feature_evidence_task_ids"
            ]
            if malformed:
                raise SnapshotBuildError(
                    f"malformed feature evidence after extraction: {malformed[:10]}"
                )
            if version_validation["task_feature_version_counts"] != {
                EXPECTED_TASK_FEATURE_VERSION: source_counts["total_candidates"]
            }:
                raise SnapshotBuildError(
                    "unexpected task_feature_version distribution: "
                    + json.dumps(
                        version_validation["task_feature_version_counts"],
                        ensure_ascii=False,
                    )
                )
            if version_validation["difficulty_formula_version_counts"] != {
                EXPECTED_DIFFICULTY_FORMULA_VERSION: source_counts[
                    "total_candidates"
                ]
            }:
                raise SnapshotBuildError(
                    "unexpected difficulty formula version distribution: "
                    + json.dumps(
                        version_validation[
                            "difficulty_formula_version_counts"
                        ],
                        ensure_ascii=False,
                    )
                )

        source_hash_after = sha256_file(source_path)
        if source_hash_after != source_hash_before:
            raise SnapshotBuildError(
                "source database SHA-256 changed during the build; aborting"
            )

        if output_path.exists() and args.overwrite:
            # os.replace below atomically replaces the explicit output path.
            pass
        os.replace(temporary_path, output_path)
        output_hash = sha256_file(output_path)

        report = {
            "schema_version": "difficulty_snapshot_build_v0.2.1",
            "generated_at": utc_now(),
            "status": "completed",
            "network_access_used": False,
            "pipeline": {
                "source_database": str(source_path),
                "output_database": str(output_path),
                "repository_native_command": [
                    sys.executable,
                    "-m",
                    "oss_mentor",
                    "extract-features",
                    "--database",
                    "<temporary_copy>",
                ],
                "task_feature_version": TASK_FEATURE_VERSION,
                "difficulty_formula_version": DIFFICULTY_FORMULA_VERSION,
            },
            "integrity": {
                "source_sha256_before": source_hash_before,
                "source_sha256_after": source_hash_after,
                "source_unchanged": source_hash_before == source_hash_after,
                "output_sha256": output_hash,
                "candidate_counts": source_counts,
                "candidate_identity_count": len(source_ids),
                "task_type_change_count": 0,
                "migration_set": sorted(source_migrations),
                "skill_identity_changed": False,
                "skill_minimum_level_change_count": skill_comparison[
                    "minimum_level_change_count"
                ],
                "version_validation": version_validation,
            },
            "cli": {
                "stdout": cli_stdout.strip(),
                "stderr": cli_stderr.strip(),
            },
        }

        temp_report = report_path.with_name(
            f".{report_path.name}.tmp-{uuid4().hex}"
        )
        temp_report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_report, report_path)

    except SnapshotBuildError as exc:
        if temporary_path.exists():
            temporary_path.unlink()
        fatal(str(exc))
    except Exception as exc:
        if temporary_path.exists():
            temporary_path.unlink()
        fatal(f"{type(exc).__name__}: {exc}")

    print()
    print("B3 difficulty snapshot v0.2.1")
    print()
    print(f"Task feature version: {TASK_FEATURE_VERSION}")
    print(f"Difficulty formula version: {DIFFICULTY_FORMULA_VERSION}")
    print(f"Source: {source_path}")
    print(f"Output: {output_path}")
    print(f"Report: {report_path}")
    print(f"Candidates: {source_counts['total_candidates']}")
    print(f"Active: {source_counts['active_candidates']}")
    print(f"Eligible: {source_counts['eligible_candidates']}")
    print(
        "Newcomer eligible: "
        f"{source_counts['newcomer_eligible_candidates']}"
    )
    print("Task type changes: 0")
    print(
        "Skill minimum-level changes: "
        f"{skill_comparison['minimum_level_change_count']}"
    )
    print("Source unchanged: true")
    print("Network access used: false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())