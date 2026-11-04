CREATE TABLE IF NOT EXISTS recommendation_run_ui_metadata (
    run_id TEXT PRIMARY KEY REFERENCES recommendation_run(run_id) ON DELETE CASCADE,
    profile_json TEXT NOT NULL DEFAULT '{}',
    model_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
