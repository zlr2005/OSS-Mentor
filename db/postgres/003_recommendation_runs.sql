-- OSS-Mentor v0.5 recommendation snapshot migration for PostgreSQL.
-- Equivalent to SQLite migration 010_recommendation_runs.sql.
-- Apply after db/postgres/001_initial.sql and db/postgres/002_profile_identity.sql.

ALTER TABLE repository
    ADD COLUMN IF NOT EXISTS has_contributing_guide BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS recommendation_run (
    run_id TEXT PRIMARY KEY,
    feedback_context TEXT,
    service_track TEXT NOT NULL CHECK (service_track IN ('newcomer', 'growth')),
    match_version TEXT NOT NULL,
    profile_snapshot_hash TEXT NOT NULL,
    candidate_snapshot_hash TEXT NOT NULL,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recommendation_run_item (
    run_id TEXT NOT NULL REFERENCES recommendation_run(run_id) ON DELETE CASCADE,
    task_candidate_id BIGINT NOT NULL,
    raw_score REAL NOT NULL CHECK (raw_score BETWEEN 0 AND 100),
    final_score REAL NOT NULL CHECK (final_score BETWEEN 0 AND 100),
    raw_rank INTEGER NOT NULL CHECK (raw_rank > 0),
    final_rank INTEGER NOT NULL CHECK (final_rank > 0),
    diversity_reranked BOOLEAN NOT NULL,
    recommendation_json TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    PRIMARY KEY (run_id, task_candidate_id),
    UNIQUE (run_id, final_rank)
);

CREATE INDEX IF NOT EXISTS recommendation_run_context_idx
    ON recommendation_run(feedback_context, created_at DESC);

CREATE INDEX IF NOT EXISTS recommendation_run_created_idx
    ON recommendation_run(created_at DESC);
