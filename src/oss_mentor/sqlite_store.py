"""SQLite persistence for the dependency-free MVP candidate pipeline."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from oss_mentor.candidate_rules import EligibilityResult
from oss_mentor.developer_profiles import DeveloperProfile
from oss_mentor.task_features import SkillRequirement, TaskFeatures


class _ClosingConnection(sqlite3.Connection):
    """Commit or roll back, then release the Windows file handle."""

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


class SQLiteCandidateStore:
    def __init__(self, database_path: Path, migration_path: Path) -> None:
        self.database_path = database_path.resolve()
        self.migration_path = migration_path.resolve()

    def connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.database_path, factory=_ClosingConnection
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migration (
                    migration_name TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            migration_files = sorted(self.migration_path.parent.glob("*.sql"))
            for migration_file in migration_files:
                applied = connection.execute(
                    "SELECT 1 FROM schema_migration WHERE migration_name = ?",
                    (migration_file.name,),
                ).fetchone()
                if applied:
                    continue
                connection.executescript(migration_file.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO schema_migration (migration_name, applied_at) VALUES (?, ?)",
                    (migration_file.name, self._now()),
                )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def upsert_repository(
        self,
        connection: sqlite3.Connection,
        *,
        full_name: str,
        github_repository_id: int | None,
        html_url: str,
        ecosystems_last_synced_at: str | None,
        ecosystem: str | None = None,
        primary_language: str | None = None,
        github_verified_at: str | None = None,
        is_archived: bool = False,
        is_disabled: bool = False,
        pushed_at: str | None = None,
        mark_synced: bool = False,
    ) -> int:
        now = self._now()
        connection.execute(
            """
            INSERT INTO repository (
                full_name, github_repository_id, html_url,
                ecosystems_last_synced_at, first_collected_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(full_name) DO UPDATE SET
                github_repository_id = COALESCE(
                    excluded.github_repository_id, repository.github_repository_id
                ),
                html_url = excluded.html_url,
                ecosystems_last_synced_at = COALESCE(
                    excluded.ecosystems_last_synced_at,
                    repository.ecosystems_last_synced_at
                ),
                updated_at = excluded.updated_at
            """,
            (
                full_name,
                github_repository_id,
                html_url,
                ecosystems_last_synced_at,
                now,
                now,
            ),
        )
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(repository)")
        }
        if "ecosystem" in columns:
            connection.execute(
                """
                UPDATE repository SET
                    ecosystem = COALESCE(?, ecosystem),
                    primary_language = COALESCE(?, primary_language)
                WHERE full_name = ?
                """,
                (ecosystem, primary_language, full_name),
            )
        if "github_verified_at" in columns:
            connection.execute(
                """
                UPDATE repository SET
                    github_verified_at = COALESCE(?, github_verified_at),
                    is_archived = ?,
                    is_disabled = ?,
                    pushed_at = COALESCE(?, pushed_at),
                    last_candidate_sync_at = CASE WHEN ? THEN ?
                        ELSE last_candidate_sync_at END,
                    updated_at = ?
                WHERE full_name = ?
                """,
                (
                    github_verified_at,
                    int(is_archived),
                    int(is_disabled),
                    pushed_at,
                    int(mark_synced),
                    now,
                    now,
                    full_name,
                ),
            )
        row = connection.execute(
            "SELECT repository_id FROM repository WHERE full_name = ?", (full_name,)
        ).fetchone()
        if row is None:
            raise RuntimeError(f"failed to upsert repository {full_name}")
        return int(row["repository_id"])

    def upsert_candidate(
        self,
        connection: sqlite3.Connection,
        *,
        repository_id: int,
        record: dict[str, Any],
        eligibility: EligibilityResult,
    ) -> None:
        normalized_at = self._now()
        values = (
            repository_id,
            record["issue_number"],
            record.get("github_issue_id"),
            record["html_url"],
            record["created_at"],
            record.get("author_association"),
            record["title"],
            record.get("body_text"),
            json.dumps(record.get("labels") or [], ensure_ascii=False, sort_keys=True),
            record["state"],
            record["assignment_state"],
            int(bool(record.get("is_locked"))),
            (
                None
                if record.get("has_linked_open_pr") is None
                else int(bool(record["has_linked_open_pr"]))
            ),
            int(record.get("comment_count") or 0),
            record.get("last_activity_at"),
            record["source_system"],
            record["source_fetched_at"],
            record.get("github_verified_at"),
            eligibility.eligibility,
            json.dumps(eligibility.reasons),
            json.dumps(eligibility.warnings),
            int(eligibility.newcomer_label_signal),
            eligibility.feature_definition_version,
            normalized_at,
        )
        connection.execute(
            """
            INSERT INTO task_candidate (
                repository_id, issue_number, github_issue_id, html_url, created_at,
                author_association, title, body_text, labels_json, state,
                assignment_state, is_locked, has_linked_open_pr, comment_count,
                last_activity_at, source_system, source_fetched_at,
                github_verified_at, candidate_eligibility,
                ineligibility_reasons_json, warnings_json, newcomer_label_signal,
                feature_definition_version, normalized_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(repository_id, issue_number) DO UPDATE SET
                github_issue_id = COALESCE(
                    excluded.github_issue_id, task_candidate.github_issue_id
                ),
                html_url = excluded.html_url,
                author_association = excluded.author_association,
                title = excluded.title,
                body_text = COALESCE(excluded.body_text, task_candidate.body_text),
                labels_json = excluded.labels_json,
                state = excluded.state,
                assignment_state = excluded.assignment_state,
                is_locked = excluded.is_locked,
                has_linked_open_pr = excluded.has_linked_open_pr,
                comment_count = excluded.comment_count,
                last_activity_at = excluded.last_activity_at,
                source_system = excluded.source_system,
                source_fetched_at = excluded.source_fetched_at,
                github_verified_at = COALESCE(
                    excluded.github_verified_at, task_candidate.github_verified_at
                ),
                candidate_eligibility = excluded.candidate_eligibility,
                ineligibility_reasons_json = excluded.ineligibility_reasons_json,
                warnings_json = excluded.warnings_json,
                newcomer_label_signal = excluded.newcomer_label_signal,
                feature_definition_version = excluded.feature_definition_version,
                normalized_at = excluded.normalized_at
            """,
            values,
        )

    def update_repository_metadata(
        self,
        *,
        full_name: str,
        github_repository_id: int,
        ecosystem: str,
        primary_language: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE repository SET
                    github_repository_id = ?,
                    ecosystem = ?,
                    primary_language = ?,
                    updated_at = ?
                WHERE full_name = ?
                """,
                (
                    github_repository_id,
                    ecosystem,
                    primary_language,
                    self._now(),
                    full_name,
                ),
            )

    def summary(self) -> dict[str, Any]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT candidate_eligibility, COUNT(*) AS count
                FROM task_candidate
                GROUP BY candidate_eligibility
                ORDER BY candidate_eligibility
                """
            ).fetchall()
            newcomer_count = connection.execute(
                "SELECT COUNT(*) FROM task_candidate WHERE newcomer_label_signal = 1"
            ).fetchone()[0]
            eligible_newcomer_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM task_candidate AS tc
                JOIN repository AS r USING (repository_id)
                WHERE tc.newcomer_label_signal = 1
                  AND tc.candidate_eligibility = 'eligible'
                  AND COALESCE(r.is_archived, 0) = 0
                  AND COALESCE(r.is_disabled, 0) = 0
                """
            ).fetchone()[0]
            total = connection.execute("SELECT COUNT(*) FROM task_candidate").fetchone()[0]
        return {
            "database_path": str(self.database_path),
            "candidate_count": int(total),
            "newcomer_signal_count": int(newcomer_count),
            "eligible_newcomer_signal_count": int(eligible_newcomer_count),
            "eligibility_counts": {
                str(row["candidate_eligibility"]): int(row["count"]) for row in rows
            },
        }

    def stale_candidates(
        self,
        *,
        repositories: list[str],
        older_than_hours: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return candidates whose current GitHub verification is stale."""

        self.initialize()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=older_than_hours)).isoformat()
        clauses = [
            "(tc.github_verified_at IS NULL OR tc.github_verified_at < ?)",
        ]
        parameters: list[Any] = [cutoff]
        if repositories:
            placeholders = ", ".join("?" for _ in repositories)
            clauses.append(f"r.full_name IN ({placeholders})")
            parameters.extend(repositories)
        parameters.append(limit)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    tc.task_candidate_id, tc.repository_id, tc.issue_number,
                    tc.github_verified_at, tc.candidate_eligibility,
                    r.full_name AS repository
                FROM task_candidate AS tc
                JOIN repository AS r USING (repository_id)
                WHERE {' AND '.join(clauses)}
                ORDER BY
                    CASE WHEN tc.github_verified_at IS NULL THEN 0 ELSE 1 END,
                    tc.github_verified_at,
                    tc.task_candidate_id
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_candidate_unavailable(
        self,
        *,
        task_candidate_id: int,
        reason: str = "github_unavailable",
        verified_at: str | None = None,
    ) -> None:
        now = verified_at or self._now()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE task_candidate SET
                    candidate_eligibility = 'excluded',
                    ineligibility_reasons_json = ?,
                    warnings_json = '[]',
                    github_verified_at = ?,
                    normalized_at = ?
                WHERE task_candidate_id = ?
                """,
                (json.dumps([reason]), now, now, task_candidate_id),
            )

    def mark_repositories_refreshed(self, full_names: list[str]) -> None:
        if not full_names:
            return
        now = self._now()
        placeholders = ", ".join("?" for _ in full_names)
        with self.connect() as connection:
            connection.execute(
                f"""
                UPDATE repository
                SET last_candidate_refresh_at = ?, updated_at = ?
                WHERE full_name IN ({placeholders})
                """,
                [now, now, *full_names],
            )

    def candidate_report_rows(self) -> dict[str, list[dict[str, Any]]]:
        """Return aggregate-safe source rows for the local candidate report."""

        self.initialize()
        with self.connect() as connection:
            repositories = connection.execute(
                """
                SELECT repository_id, full_name, primary_language, is_archived,
                       is_disabled, github_verified_at, last_candidate_sync_at,
                       last_candidate_refresh_at
                FROM repository ORDER BY full_name
                """
            ).fetchall()
            candidates = connection.execute(
                """
                SELECT tc.task_candidate_id, tc.repository_id, tc.state,
                       tc.assignment_state, tc.is_locked, tc.has_linked_open_pr,
                       tc.body_text,
                       tc.candidate_eligibility, tc.ineligibility_reasons_json,
                       tc.warnings_json, tc.newcomer_label_signal,
                       tc.github_verified_at, tc.task_types_json,
                       r.full_name AS repository, r.primary_language,
                       r.is_archived, r.is_disabled
                FROM task_candidate AS tc
                JOIN repository AS r USING (repository_id)
                ORDER BY tc.task_candidate_id
                """
            ).fetchall()
        return {
            "repositories": [dict(row) for row in repositories],
            "candidates": [dict(row) for row in candidates],
        }

    def list_candidates(
        self,
        *,
        eligibility: str | None = None,
        newcomer_only: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if eligibility:
            clauses.append("tc.candidate_eligibility = ?")
            parameters.append(eligibility)
        if newcomer_only:
            clauses.append("tc.newcomer_label_signal = 1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    r.full_name AS repository,
                    tc.issue_number,
                    tc.github_issue_id,
                    tc.title,
                    tc.html_url,
                    tc.labels_json,
                    tc.assignment_state,
                    tc.comment_count,
                    tc.last_activity_at,
                    tc.candidate_eligibility,
                    tc.ineligibility_reasons_json,
                    tc.warnings_json,
                    tc.newcomer_label_signal,
                    tc.github_verified_at,
                    tc.task_types_json,
                    tc.text_clarity_score,
                    tc.estimated_code_difficulty,
                    tc.estimated_setup_difficulty,
                    tc.estimated_project_context_difficulty,
                    tc.estimated_collaboration_difficulty,
                    tc.estimated_effort_bucket,
                    tc.novice_fit_probability,
                    tc.newcomer_score,
                    tc.growth_value_score,
                    tc.task_feature_version
                FROM task_candidate AS tc
                JOIN repository AS r USING (repository_id)
                {where}
                ORDER BY tc.last_activity_at DESC, tc.issue_number DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [
            {
                **dict(row),
                "labels": json.loads(row["labels_json"]),
                "ineligibility_reasons": json.loads(
                    row["ineligibility_reasons_json"]
                ),
                "warnings": json.loads(row["warnings_json"]),
                "newcomer_label_signal": bool(row["newcomer_label_signal"]),
                "task_types": json.loads(row["task_types_json"]),
            }
            for row in rows
        ]

    def feature_records(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    tc.task_candidate_id, tc.title, tc.body_text, tc.labels_json,
                    tc.comment_count, tc.candidate_eligibility,
                    r.primary_language
                FROM task_candidate AS tc
                JOIN repository AS r USING (repository_id)
                ORDER BY task_candidate_id
                """
            ).fetchall()
        return [
            {
                **dict(row),
                "labels": json.loads(row["labels_json"]),
            }
            for row in rows
        ]

    def replace_skill_requirements(
        self,
        connection: sqlite3.Connection,
        *,
        task_candidate_id: int,
        requirements: tuple[SkillRequirement, ...],
        feature_version: str,
    ) -> None:
        connection.execute(
            "DELETE FROM task_skill_requirement WHERE task_candidate_id = ?",
            (task_candidate_id,),
        )
        connection.executemany(
            """
            INSERT INTO task_skill_requirement (
                task_candidate_id, skill_name, minimum_level, importance,
                requirement_source, feature_version
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    task_candidate_id,
                    item.skill_name,
                    item.minimum_level,
                    item.importance,
                    item.requirement_source,
                    feature_version,
                )
                for item in requirements
            ],
        )

    def upsert_profile(self, profile: DeveloperProfile) -> int:
        self.initialize()
        now = self._now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO developer_profile (
                    profile_key, display_name, service_track,
                    preferred_languages_json, operating_systems_json,
                    preferred_task_types_json, max_code_difficulty,
                    max_setup_difficulty, desired_skill_stretch, profile_source,
                    consent_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_key) DO UPDATE SET
                    display_name = excluded.display_name,
                    service_track = excluded.service_track,
                    preferred_languages_json = excluded.preferred_languages_json,
                    operating_systems_json = excluded.operating_systems_json,
                    preferred_task_types_json = excluded.preferred_task_types_json,
                    max_code_difficulty = excluded.max_code_difficulty,
                    max_setup_difficulty = excluded.max_setup_difficulty,
                    desired_skill_stretch = excluded.desired_skill_stretch,
                    profile_source = excluded.profile_source,
                    consent_version = excluded.consent_version,
                    updated_at = excluded.updated_at
                """,
                (
                    profile.profile_key,
                    profile.display_name,
                    profile.service_track,
                    json.dumps(profile.preferred_languages),
                    json.dumps(profile.operating_systems),
                    json.dumps(profile.preferred_task_types),
                    profile.max_code_difficulty,
                    profile.max_setup_difficulty,
                    profile.desired_skill_stretch,
                    profile.profile_source,
                    profile.consent_version,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT developer_profile_id FROM developer_profile WHERE profile_key = ?",
                (profile.profile_key,),
            ).fetchone()
            profile_id = int(row["developer_profile_id"])
            connection.execute(
                "DELETE FROM developer_skill WHERE developer_profile_id = ?",
                (profile_id,),
            )
            connection.executemany(
                """
                INSERT INTO developer_skill (
                    developer_profile_id, skill_name, skill_level,
                    evidence_source, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (profile_id, name, level, profile.profile_source, now)
                    for name, level in profile.skills.items()
                ],
            )
        return profile_id

    def profile_for_matching(self, profile_key: str) -> dict[str, Any]:
        self.initialize()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM developer_profile WHERE profile_key = ?", (profile_key,)
            ).fetchone()
            if row is None:
                raise ValueError(f"developer profile does not exist: {profile_key}")
            skills = connection.execute(
                """
                SELECT skill_name, skill_level FROM developer_skill
                WHERE developer_profile_id = ?
                """,
                (row["developer_profile_id"],),
            ).fetchall()
        return {
            **dict(row),
            "preferred_languages": json.loads(row["preferred_languages_json"]),
            "operating_systems": json.loads(row["operating_systems_json"]),
            "preferred_task_types": json.loads(row["preferred_task_types_json"]),
            "skills": {item["skill_name"].casefold(): item["skill_level"] for item in skills},
        }

    def list_profiles_public(self) -> list[dict[str, Any]]:
        self.initialize()
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    dp.developer_profile_id, dp.profile_key, dp.display_name,
                    dp.service_track, dp.preferred_languages_json,
                    dp.operating_systems_json, dp.preferred_task_types_json,
                    dp.max_code_difficulty, dp.max_setup_difficulty,
                    dp.desired_skill_stretch, dp.profile_source,
                    COUNT(ds.skill_name) AS skill_count
                FROM developer_profile AS dp
                LEFT JOIN developer_skill AS ds USING (developer_profile_id)
                GROUP BY dp.developer_profile_id
                ORDER BY dp.profile_key
                """
            ).fetchall()
        return [
            {
                "profile_key": row["profile_key"],
                "display_name": row["display_name"],
                "service_track": row["service_track"],
                "preferred_languages": json.loads(row["preferred_languages_json"]),
                "operating_systems": json.loads(row["operating_systems_json"]),
                "preferred_task_types": json.loads(row["preferred_task_types_json"]),
                "max_code_difficulty": row["max_code_difficulty"],
                "max_setup_difficulty": row["max_setup_difficulty"],
                "desired_skill_stretch": row["desired_skill_stretch"],
                "profile_source": row["profile_source"],
                "skill_count": row["skill_count"],
            }
            for row in rows
        ]

    def matchable_candidates(self) -> list[dict[str, Any]]:
        self.initialize()
        with self.connect() as connection:
            candidates = connection.execute(
                """
                SELECT tc.*, r.full_name AS repository, r.primary_language
                FROM task_candidate AS tc
                JOIN repository AS r USING (repository_id)
                WHERE tc.candidate_eligibility = 'eligible'
                  AND tc.task_feature_version IS NOT NULL
                  AND COALESCE(r.is_archived, 0) = 0
                  AND COALESCE(r.is_disabled, 0) = 0
                """
            ).fetchall()
            requirements = connection.execute(
                "SELECT * FROM task_skill_requirement"
            ).fetchall()
        by_task: dict[int, list[dict[str, Any]]] = {}
        for row in requirements:
            by_task.setdefault(int(row["task_candidate_id"]), []).append(dict(row))
        return [
            {
                **dict(row),
                "labels": json.loads(row["labels_json"]),
                "task_types": json.loads(row["task_types_json"]),
                "requirements": by_task.get(int(row["task_candidate_id"]), []),
            }
            for row in candidates
        ]

    def feedback_states(
        self, feedback_context: str, task_candidate_ids: list[int]
    ) -> dict[int, str]:
        """Return the current feedback state for the displayed candidate IDs."""

        if not task_candidate_ids:
            return {}
        self.initialize()
        unique_ids = sorted({int(value) for value in task_candidate_ids})
        placeholders = ", ".join("?" for _ in unique_ids)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT task_candidate_id, feedback_state
                FROM recommendation_feedback
                WHERE feedback_context = ?
                  AND task_candidate_id IN ({placeholders})
                """,
                [feedback_context, *unique_ids],
            ).fetchall()
        return {
            int(row["task_candidate_id"]): str(row["feedback_state"])
            for row in rows
        }

    def record_feedback(
        self,
        *,
        task_candidate_id: int,
        feedback_context: str,
        service_track: str,
        feedback_state: str,
    ) -> dict[str, Any]:
        """Upsert current feedback and append an event only when the state changes."""

        self.initialize()
        now = self._now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            candidate = connection.execute(
                "SELECT 1 FROM task_candidate WHERE task_candidate_id = ?",
                (task_candidate_id,),
            ).fetchone()
            if candidate is None:
                raise ValueError("task candidate does not exist")
            current = connection.execute(
                """
                SELECT recommendation_feedback_id, feedback_state, created_at, updated_at
                FROM recommendation_feedback
                WHERE task_candidate_id = ? AND feedback_context = ?
                """,
                (task_candidate_id, feedback_context),
            ).fetchone()
            previous_state = str(current["feedback_state"]) if current else None
            if current is not None and previous_state == feedback_state:
                return {
                    "task_candidate_id": task_candidate_id,
                    "feedback_context": feedback_context,
                    "service_track": service_track,
                    "feedback_state": feedback_state,
                    "changed": False,
                    "created_at": current["created_at"],
                    "updated_at": current["updated_at"],
                }
            if current is None:
                cursor = connection.execute(
                    """
                    INSERT INTO recommendation_feedback (
                        task_candidate_id, feedback_context, service_track,
                        feedback_state, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_candidate_id,
                        feedback_context,
                        service_track,
                        feedback_state,
                        now,
                        now,
                    ),
                )
                feedback_id = int(cursor.lastrowid)
                created_at = now
            else:
                feedback_id = int(current["recommendation_feedback_id"])
                created_at = str(current["created_at"])
                connection.execute(
                    """
                    UPDATE recommendation_feedback
                    SET service_track = ?, feedback_state = ?, updated_at = ?
                    WHERE recommendation_feedback_id = ?
                    """,
                    (service_track, feedback_state, now, feedback_id),
                )
            connection.execute(
                """
                INSERT INTO recommendation_feedback_event (
                    recommendation_feedback_id, previous_state,
                    feedback_state, occurred_at
                ) VALUES (?, ?, ?, ?)
                """,
                (feedback_id, previous_state, feedback_state, now),
            )
        return {
            "task_candidate_id": task_candidate_id,
            "feedback_context": feedback_context,
            "service_track": service_track,
            "feedback_state": feedback_state,
            "changed": True,
            "created_at": created_at,
            "updated_at": now,
        }

    def update_features(
        self,
        connection: sqlite3.Connection,
        *,
        task_candidate_id: int,
        features: TaskFeatures,
    ) -> None:
        connection.execute(
            """
            UPDATE task_candidate SET
                has_reproduction_steps = ?,
                has_acceptance_criteria = ?,
                has_expected_behavior = ?,
                has_affected_module_hint = ?,
                task_types_json = ?,
                text_clarity_score = ?,
                estimated_code_difficulty = ?,
                estimated_setup_difficulty = ?,
                estimated_project_context_difficulty = ?,
                estimated_collaboration_difficulty = ?,
                estimated_effort_bucket = ?,
                novice_fit_probability = ?,
                newcomer_score = ?,
                growth_value_score = ?,
                feature_evidence_json = ?,
                feature_extracted_at = ?,
                task_feature_version = ?
            WHERE task_candidate_id = ?
            """,
            (
                int(features.has_reproduction_steps),
                int(features.has_acceptance_criteria),
                int(features.has_expected_behavior),
                int(features.has_affected_module_hint),
                json.dumps(features.task_types),
                features.text_clarity_score,
                features.estimated_code_difficulty,
                features.estimated_setup_difficulty,
                features.estimated_project_context_difficulty,
                features.estimated_collaboration_difficulty,
                features.estimated_effort_bucket,
                features.novice_fit_probability,
                features.newcomer_score,
                features.growth_value_score,
                json.dumps(features.feature_evidence, sort_keys=True),
                self._now(),
                features.task_feature_version,
                task_candidate_id,
            ),
        )

    def rank_candidates(self, *, track: str, limit: int = 20) -> list[dict[str, Any]]:
        if track not in {"newcomer", "growth"}:
            raise ValueError(f"unsupported track: {track}")
        score_column = "newcomer_score" if track == "newcomer" else "growth_value_score"
        newcomer_clause = "AND tc.newcomer_label_signal = 1" if track == "newcomer" else ""
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    r.full_name AS repository,
                    tc.issue_number,
                    tc.title,
                    tc.html_url,
                    tc.labels_json,
                    tc.task_types_json,
                    tc.text_clarity_score,
                    tc.estimated_code_difficulty,
                    tc.estimated_setup_difficulty,
                    tc.estimated_project_context_difficulty,
                    tc.estimated_collaboration_difficulty,
                    tc.estimated_effort_bucket,
                    tc.novice_fit_probability,
                    tc.newcomer_score,
                    tc.growth_value_score,
                    tc.feature_evidence_json,
                    tc.task_feature_version
                FROM task_candidate AS tc
                JOIN repository AS r USING (repository_id)
                WHERE tc.candidate_eligibility = 'eligible'
                  AND tc.task_feature_version IS NOT NULL
                  {newcomer_clause}
                ORDER BY {score_column} DESC, tc.last_activity_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                **dict(row),
                "track": track,
                "score": row[score_column],
                "labels": json.loads(row["labels_json"]),
                "task_types": json.loads(row["task_types_json"]),
                "feature_evidence": json.loads(row["feature_evidence_json"]),
            }
            for row in rows
        ]
