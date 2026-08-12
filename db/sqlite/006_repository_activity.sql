ALTER TABLE repository ADD COLUMN maintenance_status TEXT NOT NULL DEFAULT 'active'
    CHECK (maintenance_status IN ('active', 'inactive', 'unknown'));
ALTER TABLE repository ADD COLUMN maintenance_reason TEXT;
ALTER TABLE repository ADD COLUMN activity_checked_at TEXT;

CREATE INDEX IF NOT EXISTS repository_maintenance_status_idx
    ON repository(maintenance_status, is_archived, is_disabled);
