-- A-owned additive draft migration. D's existing sorted migration discovery can
-- apply this without facade edits. All snapshots are isolated by dataset_version;
-- these historical/fixture tables never update live task_candidate rows.
-- Deletion is RESTRICT throughout: no implicit loss of evidence or user records.

CREATE TABLE IF NOT EXISTS midterm_dataset_snapshot (
    dataset_version TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    fixture_only INTEGER NOT NULL CHECK (fixture_only IN (0, 1)),
    registered_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS midterm_dataset_import (
    batch_id TEXT PRIMARY KEY,
    dataset_version TEXT NOT NULL UNIQUE REFERENCES midterm_dataset_snapshot(dataset_version) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN ('running', 'failed', 'completed')),
    checkpoint INTEGER NOT NULL DEFAULT 0 CHECK (checkpoint >= 0),
    total_items INTEGER NOT NULL CHECK (total_items >= 0),
    duplicates_json TEXT NOT NULL,
    error_type TEXT,
    updated_at TEXT NOT NULL,
    CHECK (checkpoint <= total_items),
    CHECK (status != 'completed' OR checkpoint = total_items)
);

CREATE TABLE IF NOT EXISTS midterm_dataset_asset (
    dataset_version TEXT NOT NULL REFERENCES midterm_dataset_snapshot(dataset_version) ON DELETE RESTRICT,
    relative_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    content BLOB NOT NULL,
    PRIMARY KEY (dataset_version, relative_path)
);

CREATE TABLE IF NOT EXISTS midterm_dataset_repository (
    dataset_version TEXT NOT NULL REFERENCES midterm_dataset_snapshot(dataset_version) ON DELETE RESTRICT,
    repository_key TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (dataset_version, repository_key)
);

CREATE TABLE IF NOT EXISTS midterm_dataset_developer (
    dataset_version TEXT NOT NULL REFERENCES midterm_dataset_snapshot(dataset_version) ON DELETE RESTRICT,
    developer_key TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (dataset_version, developer_key)
);

CREATE TABLE IF NOT EXISTS midterm_dataset_file (
    dataset_version TEXT NOT NULL,
    file_key TEXT NOT NULL,
    repository_key TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (dataset_version, file_key),
    FOREIGN KEY (dataset_version, repository_key)
        REFERENCES midterm_dataset_repository(dataset_version, repository_key) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS midterm_dataset_code (
    dataset_version TEXT NOT NULL,
    file_version_key TEXT NOT NULL,
    file_key TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (dataset_version, file_version_key),
    FOREIGN KEY (dataset_version, file_key)
        REFERENCES midterm_dataset_file(dataset_version, file_key) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS midterm_dataset_contribution (
    dataset_version TEXT NOT NULL,
    record_key TEXT NOT NULL,
    developer_key TEXT NOT NULL,
    file_version_key TEXT NOT NULL,
    event_at TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (dataset_version, record_key),
    FOREIGN KEY (dataset_version, developer_key)
        REFERENCES midterm_dataset_developer(dataset_version, developer_key) ON DELETE RESTRICT,
    FOREIGN KEY (dataset_version, file_version_key)
        REFERENCES midterm_dataset_code(dataset_version, file_version_key) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS midterm_dataset_contribution_author_time_idx
    ON midterm_dataset_contribution(dataset_version, developer_key, event_at);

CREATE TABLE IF NOT EXISTS midterm_dataset_behavior (
    dataset_version TEXT NOT NULL,
    record_key TEXT NOT NULL,
    developer_key TEXT NOT NULL,
    repository_key TEXT NOT NULL,
    event_at TEXT,
    ordinal INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (dataset_version, record_key),
    FOREIGN KEY (dataset_version, developer_key)
        REFERENCES midterm_dataset_developer(dataset_version, developer_key) ON DELETE RESTRICT,
    FOREIGN KEY (dataset_version, repository_key)
        REFERENCES midterm_dataset_repository(dataset_version, repository_key) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS midterm_dataset_issue (
    dataset_version TEXT NOT NULL,
    issue_key TEXT NOT NULL,
    repository_key TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (dataset_version, issue_key),
    FOREIGN KEY (dataset_version, repository_key)
        REFERENCES midterm_dataset_repository(dataset_version, repository_key) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS midterm_dataset_issue_file (
    dataset_version TEXT NOT NULL,
    record_key TEXT NOT NULL,
    issue_key TEXT NOT NULL,
    file_key TEXT NOT NULL,
    available_at TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (dataset_version, record_key),
    FOREIGN KEY (dataset_version, issue_key)
        REFERENCES midterm_dataset_issue(dataset_version, issue_key) ON DELETE RESTRICT,
    FOREIGN KEY (dataset_version, file_key)
        REFERENCES midterm_dataset_file(dataset_version, file_key) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS midterm_dataset_issue_file_time_idx
    ON midterm_dataset_issue_file(dataset_version, issue_key, available_at);

CREATE TABLE IF NOT EXISTS midterm_dataset_file_alias (
    dataset_version TEXT NOT NULL,
    record_key TEXT NOT NULL,
    file_key TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (dataset_version, record_key),
    FOREIGN KEY (dataset_version, file_key)
        REFERENCES midterm_dataset_file(dataset_version, file_key) ON DELETE RESTRICT
);
