PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT INTO schema_metadata (key, value)
VALUES ('schema_version', 'sqlite-mvp-v1')
ON CONFLICT(key) DO UPDATE SET value = excluded.value;

CREATE TABLE IF NOT EXISTS repository (
    repository_id INTEGER PRIMARY KEY,
    full_name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    github_repository_id INTEGER,
    html_url TEXT NOT NULL,
    ecosystems_last_synced_at TEXT,
    first_collected_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_candidate (
    task_candidate_id INTEGER PRIMARY KEY,
    repository_id INTEGER NOT NULL REFERENCES repository(repository_id),
    issue_number INTEGER NOT NULL CHECK (issue_number > 0),
    github_issue_id INTEGER,
    html_url TEXT NOT NULL,
    created_at TEXT NOT NULL,
    author_association TEXT,
    title TEXT NOT NULL,
    body_text TEXT,
    labels_json TEXT NOT NULL DEFAULT '[]',
    state TEXT NOT NULL CHECK (state IN ('open', 'closed')),
    assignment_state TEXT NOT NULL CHECK (
        assignment_state IN ('unassigned', 'assigned', 'claimed_in_comments', 'unknown')
    ),
    is_locked INTEGER NOT NULL DEFAULT 0 CHECK (is_locked IN (0, 1)),
    has_linked_open_pr INTEGER CHECK (has_linked_open_pr IN (0, 1)),
    comment_count INTEGER NOT NULL DEFAULT 0 CHECK (comment_count >= 0),
    last_activity_at TEXT,
    source_system TEXT NOT NULL CHECK (source_system IN ('ecosystems', 'github_rest')),
    source_fetched_at TEXT NOT NULL,
    github_verified_at TEXT,
    candidate_eligibility TEXT NOT NULL CHECK (
        candidate_eligibility IN ('eligible', 'temporarily_ineligible', 'excluded', 'unknown')
    ),
    ineligibility_reasons_json TEXT NOT NULL DEFAULT '[]',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    newcomer_label_signal INTEGER NOT NULL DEFAULT 0 CHECK (newcomer_label_signal IN (0, 1)),
    feature_definition_version TEXT NOT NULL,
    normalized_at TEXT NOT NULL,
    UNIQUE (repository_id, issue_number)
);

CREATE INDEX IF NOT EXISTS task_candidate_eligibility_idx
    ON task_candidate(candidate_eligibility, newcomer_label_signal, last_activity_at DESC);

CREATE INDEX IF NOT EXISTS task_candidate_repository_idx
    ON task_candidate(repository_id, issue_number);
