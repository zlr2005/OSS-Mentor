CREATE TABLE IF NOT EXISTS recommendation_advanced_adapter_shadow (
    run_id TEXT PRIMARY KEY REFERENCES recommendation_run(run_id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES oss_user(user_id) ON DELETE CASCADE,
    mode TEXT NOT NULL CHECK (mode = 'shadow'),
    model_version TEXT NOT NULL,
    profile_adapter_version TEXT NOT NULL,
    profile_key TEXT NOT NULL,
    profile_version TEXT NOT NULL,
    profile_mode TEXT NOT NULL,
    candidate_snapshot_hash TEXT NOT NULL,
    paired_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_advanced_adapter_shadow_user
    ON recommendation_advanced_adapter_shadow(user_id, created_at);
