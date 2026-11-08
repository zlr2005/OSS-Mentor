ALTER TABLE task_candidate ADD COLUMN verification_expires_at TEXT;
ALTER TABLE task_candidate ADD COLUMN inventory_review_status TEXT;
ALTER TABLE task_candidate ADD COLUMN inventory_source TEXT;
ALTER TABLE task_candidate ADD COLUMN inventory_record_json TEXT;

CREATE INDEX task_candidate_expiry_idx ON task_candidate(verification_expires_at);

CREATE TABLE candidate_inventory_import (
    import_id INTEGER PRIMARY KEY,
    snapshot_path TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    summary_json TEXT NOT NULL
);
