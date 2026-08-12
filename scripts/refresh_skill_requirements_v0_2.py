"""Controlled offline refresh for B4 SkillRequirement v0.2 validation.

This script intentionally does NOT use the public ``extract-features`` CLI path,
because that command refreshes repository metadata from the current repository
configuration before feature extraction.  B4 formal validation needs a fixed
candidate/repository snapshot, so this script recomputes only TaskFeatures and
SkillRequirement rows already present in a copied SQLite snapshot.

No migrations, repository-config writes, candidate sync/refresh, GitHub access,
matching, or ranking are performed here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from oss_mentor.sqlite_store import SQLiteCandidateStore
from oss_mentor.task_features import (
    DIFFICULTY_FORMULA_VERSION,
    SKILL_REQUIREMENT_RULES_VERSION,
    TASK_FEATURE_VERSION,
    extract_task_features,
    infer_skill_requirements,
)

REFRESH_SCHEMA_VERSION = "skill_requirement_refresh_v0.2"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_sha256(path: str | Path) -> str:
    resolved = Path(path).expanduser().resolve()
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _connect_existing(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {path}")
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def input_snapshot_payload(connection: sqlite3.Connection) -> dict[str, Any]:
    """Return only fields that B4 refresh is forbidden to change.

    TaskFeatures/SkillRequirement output columns are deliberately excluded.
    """

    repositories = [
        dict(row)
        for row in connection.execute(
            """
            SELECT
                repository_id,
                full_name,
                github_repository_id,
                html_url,
                ecosystem,
                primary_language,
                COALESCE(is_archived, 0) AS is_archived,
                COALESCE(is_disabled, 0) AS is_disabled
            FROM repository
            ORDER BY repository_id
            """
        ).fetchall()
    ]
    candidates = [
        dict(row)
        for row in connection.execute(
            """
            SELECT
                task_candidate_id,
                repository_id,
                issue_number,
                github_issue_id,
                html_url,
                created_at,
                author_association,
                title,
                body_text,
                labels_json,
                state,
                assignment_state,
                is_locked,
                has_linked_open_pr,
                comment_count,
                last_activity_at,
                source_system,
                source_fetched_at,
                github_verified_at,
                candidate_eligibility,
                ineligibility_reasons_json,
                warnings_json,
                newcomer_label_signal,
                feature_definition_version
            FROM task_candidate
            ORDER BY task_candidate_id
            """
        ).fetchall()
    ]
    return {"repositories": repositories, "candidates": candidates}


def input_snapshot_sha256(connection: sqlite3.Connection) -> str:
    payload = _canonical_json(input_snapshot_payload(connection)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest().upper()


def _version_summary(connection: sqlite3.Connection) -> dict[str, Any]:
    task_versions = Counter(
        str(row[0] or "")
        for row in connection.execute(
            "SELECT task_feature_version FROM task_candidate"
        ).fetchall()
    )
    requirement_versions = Counter(
        str(row[0] or "")
        for row in connection.execute(
            "SELECT feature_version FROM task_skill_requirement"
        ).fetchall()
    )
    return {
        "task_feature_version_distribution": dict(sorted(task_versions.items())),
        "skill_requirement_feature_version_distribution": dict(
            sorted(requirement_versions.items())
        ),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def refresh_database(
    database_path: str | Path,
    *,
    baseline_path: str | Path,
    expected_baseline_sha256: str,
) -> dict[str, Any]:
    database = Path(database_path).expanduser().resolve()
    baseline = Path(baseline_path).expanduser().resolve()
    expected = expected_baseline_sha256.strip().upper()

    if database == baseline:
        raise RuntimeError("STOP: B4 working database must not be the baseline database")
    if not baseline.is_file():
        raise FileNotFoundError(f"baseline database does not exist: {baseline}")
    if not database.is_file():
        raise FileNotFoundError(f"B4 working database does not exist: {database}")

    baseline_hash_before = file_sha256(baseline)
    if baseline_hash_before != expected:
        raise RuntimeError(
            "STOP: baseline SHA256 mismatch before B4 refresh: "
            f"expected {expected}, got {baseline_hash_before}"
        )

    database_hash_before = file_sha256(database)
    if database_hash_before != expected:
        raise RuntimeError(
            "STOP: B4 working database is not an untouched baseline copy before refresh: "
            f"expected {expected}, got {database_hash_before}"
        )

    connection = _connect_existing(database)
    try:
        snapshot_hash_before = input_snapshot_sha256(connection)
    finally:
        connection.close()

    # Deliberately do not call store.initialize() and do not load repository config.
    store = SQLiteCandidateStore(
        database,
        Path(__file__).resolve().parents[1] / "db" / "sqlite" / "001_mvp.sql",
    )
    records = store.feature_records()
    with store.connect() as connection:
        for record in records:
            features = extract_task_features(record)
            store.update_features(
                connection,
                task_candidate_id=int(record["task_candidate_id"]),
                features=features,
            )
            store.replace_skill_requirements(
                connection,
                task_candidate_id=int(record["task_candidate_id"]),
                requirements=infer_skill_requirements(record, features),
                feature_version=features.task_feature_version,
            )

    connection = _connect_existing(database)
    try:
        snapshot_hash_after = input_snapshot_sha256(connection)
        versions = _version_summary(connection)
        requirement_count = int(
            connection.execute("SELECT COUNT(*) FROM task_skill_requirement").fetchone()[0]
        )
    finally:
        connection.close()

    if snapshot_hash_before != snapshot_hash_after:
        raise RuntimeError(
            "STOP: invariant repository/candidate input snapshot changed during B4 refresh"
        )

    baseline_hash_after = file_sha256(baseline)
    if baseline_hash_after != expected:
        raise RuntimeError(
            "STOP: baseline SHA256 changed during B4 refresh: "
            f"expected {expected}, got {baseline_hash_after}"
        )

    database_hash_after = file_sha256(database)
    return {
        "schema_version": REFRESH_SCHEMA_VERSION,
        "generated_at": utc_now(),
        "method": "offline_fixed_snapshot_b_side_feature_refresh",
        "network_access_performed": False,
        "repository_config_applied": False,
        "migrations_run": False,
        "candidate_sync_or_refresh_performed": False,
        "baseline": {
            "path": str(baseline),
            "expected_sha256": expected,
            "sha256_before": baseline_hash_before,
            "sha256_after": baseline_hash_after,
            "unchanged": baseline_hash_before == baseline_hash_after == expected,
        },
        "database": {
            "path": str(database),
            "sha256_before": database_hash_before,
            "sha256_after": database_hash_after,
            "changed_by_feature_refresh": database_hash_before != database_hash_after,
        },
        "input_snapshot": {
            "sha256_before": snapshot_hash_before,
            "sha256_after": snapshot_hash_after,
            "unchanged": snapshot_hash_before == snapshot_hash_after,
        },
        "versions": {
            "expected_task_feature_version": TASK_FEATURE_VERSION,
            "expected_difficulty_formula_version": DIFFICULTY_FORMULA_VERSION,
            "expected_skill_requirement_rules_version": SKILL_REQUIREMENT_RULES_VERSION,
            **versions,
        },
        "candidate_count_refreshed": len(records),
        "skill_requirement_count_after": requirement_count,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="B4 copied working SQLite database")
    parser.add_argument("--baseline", required=True, help="immutable B3 baseline SQLite database")
    parser.add_argument(
        "--expected-baseline-sha256",
        required=True,
        help="formal SHA256 of the immutable baseline database",
    )
    parser.add_argument("--output", required=True, help="JSON refresh report path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = refresh_database(
        args.database,
        baseline_path=args.baseline,
        expected_baseline_sha256=args.expected_baseline_sha256,
    )
    output = Path(args.output).expanduser().resolve()
    _write_json(output, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
