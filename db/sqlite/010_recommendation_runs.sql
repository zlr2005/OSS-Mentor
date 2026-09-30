ALTER TABLE repository ADD COLUMN has_contributing_guide INTEGER NOT NULL DEFAULT 0
    CHECK (has_contributing_guide IN (0, 1));

CREATE TABLE recommendation_run (
    run_id TEXT PRIMARY KEY,
    feedback_context TEXT,
    service_track TEXT NOT NULL CHECK (service_track IN ('newcomer', 'growth')),
    match_version TEXT NOT NULL,
    profile_snapshot_hash TEXT NOT NULL,
    candidate_snapshot_hash TEXT NOT NULL,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE recommendation_run_item (
    run_id TEXT NOT NULL REFERENCES recommendation_run(run_id) ON DELETE CASCADE,
    task_candidate_id INTEGER NOT NULL,
    raw_score REAL NOT NULL CHECK (raw_score BETWEEN 0 AND 100),
    final_score REAL NOT NULL CHECK (final_score BETWEEN 0 AND 100),
    raw_rank INTEGER NOT NULL CHECK (raw_rank > 0),
    final_rank INTEGER NOT NULL CHECK (final_rank > 0),
    diversity_reranked INTEGER NOT NULL CHECK (diversity_reranked IN (0, 1)),
    recommendation_json TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    PRIMARY KEY (run_id, task_candidate_id),
    UNIQUE (run_id, final_rank)
);

CREATE INDEX recommendation_run_context_idx
    ON recommendation_run(feedback_context, created_at DESC);

CREATE INDEX recommendation_run_created_idx
    ON recommendation_run(created_at DESC);
