"""SQLite persistence for reproducible recommendation batches."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from oss_mentor.contracts import RecommendationBatchV3
from oss_mentor.sqlite_store import SQLiteCandidateStore


class SQLiteRecommendationStorage:
    """Persist hashes, ranks and public recommendation results without raw user data."""

    def __init__(self, database_path: Path, migration_path: Path) -> None:
        self.store = SQLiteCandidateStore(database_path, migration_path)

    def initialize(self) -> None:
        self.store.initialize()

    def save_recommendation_batch(self, batch: RecommendationBatchV3) -> None:
        self.initialize()
        ranking_by_id = {
            int(item["task_candidate_id"]): item for item in batch.rankings
        }
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO recommendation_run (
                    run_id, feedback_context, service_track, match_version,
                    profile_snapshot_hash, candidate_snapshot_hash,
                    warnings_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch.run_id,
                    batch.feedback_context,
                    batch.service_track,
                    batch.match_version,
                    batch.profile_hash,
                    batch.candidate_hash,
                    json.dumps(batch.warnings, ensure_ascii=False),
                    batch.created_at,
                ),
            )
            for item in batch.items:
                ranking = ranking_by_id[item.task_candidate_id]
                payload = item.to_dict()
                connection.execute(
                    """
                    INSERT INTO recommendation_run_item (
                        run_id, task_candidate_id, raw_score, final_score,
                        raw_rank, final_rank, diversity_reranked,
                        recommendation_json, reasons_json, warnings_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch.run_id,
                        item.task_candidate_id,
                        ranking["raw_score"],
                        ranking["final_score"],
                        ranking["raw_rank"],
                        ranking["final_rank"],
                        int(bool(ranking["diversity_reranked"])),
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        json.dumps(payload["reasons"], ensure_ascii=False, sort_keys=True),
                        json.dumps(payload["warnings"], ensure_ascii=False),
                    ),
                )

    def find_recommendation_batch(self, run_id: str) -> dict[str, Any] | None:
        self.initialize()
        with self.store.connect() as connection:
            run = connection.execute(
                "SELECT * FROM recommendation_run WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                return None
            rows = connection.execute(
                """
                SELECT * FROM recommendation_run_item
                WHERE run_id = ? ORDER BY final_rank
                """,
                (run_id,),
            ).fetchall()
        return {
            **dict(run),
            "warnings": json.loads(run["warnings_json"]),
            "items": [
                {
                    **json.loads(row["recommendation_json"]),
                    "raw_score": row["raw_score"],
                    "final_score": row["final_score"],
                    "raw_rank": row["raw_rank"],
                    "final_rank": row["final_rank"],
                    "diversity_reranked": bool(row["diversity_reranked"]),
                }
                for row in rows
            ],
        }
