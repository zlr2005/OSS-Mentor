ALTER TABLE repository ADD COLUMN github_verified_at TEXT;
ALTER TABLE repository ADD COLUMN is_archived INTEGER NOT NULL DEFAULT 0
    CHECK (is_archived IN (0, 1));
ALTER TABLE repository ADD COLUMN is_disabled INTEGER NOT NULL DEFAULT 0
    CHECK (is_disabled IN (0, 1));
ALTER TABLE repository ADD COLUMN pushed_at TEXT;
ALTER TABLE repository ADD COLUMN last_candidate_sync_at TEXT;
ALTER TABLE repository ADD COLUMN last_candidate_refresh_at TEXT;

CREATE INDEX IF NOT EXISTS repository_recommendable_idx
    ON repository(is_archived, is_disabled, full_name);

CREATE INDEX IF NOT EXISTS task_candidate_verification_idx
    ON task_candidate(github_verified_at, repository_id);
