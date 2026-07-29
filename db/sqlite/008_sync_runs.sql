ALTER TABLE repository ADD COLUMN candidate_sync_cursor TEXT;
ALTER TABLE repository ADD COLUMN candidate_sync_etag TEXT;
ALTER TABLE repository ADD COLUMN candidate_sync_last_modified TEXT;
ALTER TABLE repository ADD COLUMN github_updated_at TEXT;

ALTER TABLE task_candidate ADD COLUMN candidate_availability TEXT NOT NULL
    DEFAULT 'temporarily_unverified'
    CHECK (
        candidate_availability IN (
            'available',
            'closed',
            'assigned',
            'linked_open_pr',
            'locked',
            'repository_inactive',
            'temporarily_unverified'
        )
    );
ALTER TABLE task_candidate ADD COLUMN availability_reasons_json TEXT NOT NULL
    DEFAULT '["not_yet_verified_for_v0.5"]';

UPDATE task_candidate
SET candidate_availability = CASE
        WHEN state != 'open' THEN 'closed'
        WHEN assignment_state = 'assigned' THEN 'assigned'
        WHEN is_locked = 1 THEN 'locked'
        WHEN has_linked_open_pr = 1 THEN 'linked_open_pr'
        WHEN source_system = 'github_rest'
             AND github_issue_id IS NOT NULL
             AND github_verified_at IS NOT NULL THEN 'available'
        ELSE 'temporarily_unverified'
    END,
    availability_reasons_json = CASE
        WHEN state != 'open' THEN '["issue_closed"]'
        WHEN assignment_state = 'assigned' THEN '["issue_assigned"]'
        WHEN is_locked = 1 THEN '["issue_locked"]'
        WHEN has_linked_open_pr = 1 THEN '["linked_open_pr"]'
        WHEN source_system = 'github_rest'
             AND github_issue_id IS NOT NULL
             AND github_verified_at IS NOT NULL THEN '[]'
        ELSE ineligibility_reasons_json
    END;

UPDATE task_candidate
SET candidate_availability = 'repository_inactive',
    availability_reasons_json = '["repository_inactive"]'
WHERE repository_id IN (
    SELECT repository_id
    FROM repository
    WHERE is_archived = 1
       OR is_disabled = 1
       OR maintenance_status = 'inactive'
);

CREATE TABLE sync_run (
    sync_run_id INTEGER PRIMARY KEY,
    run_type TEXT NOT NULL DEFAULT 'repository_sync' CHECK (
        run_type IN ('repository_sync', 'candidate_refresh')
    ),
    status TEXT NOT NULL CHECK (
        status IN (
            'pending',
            'running',
            'succeeded',
            'partially_succeeded',
            'failed'
        )
    ),
    requested_by TEXT NOT NULL,
    limit_per_repository INTEGER NOT NULL
        CHECK (limit_per_repository BETWEEN 1 AND 100),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    repository_count INTEGER NOT NULL DEFAULT 0 CHECK (repository_count >= 0),
    request_count INTEGER NOT NULL DEFAULT 0 CHECK (request_count >= 0),
    success_count INTEGER NOT NULL DEFAULT 0 CHECK (success_count >= 0),
    failure_count INTEGER NOT NULL DEFAULT 0 CHECK (failure_count >= 0),
    skipped_count INTEGER NOT NULL DEFAULT 0 CHECK (skipped_count >= 0),
    rate_limit_remaining INTEGER,
    rate_limit_reset_at TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    error_code TEXT,
    error_summary TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE sync_repository_result (
    sync_repository_result_id INTEGER PRIMARY KEY,
    sync_run_id INTEGER NOT NULL
        REFERENCES sync_run(sync_run_id) ON DELETE CASCADE,
    repository_id INTEGER
        REFERENCES repository(repository_id) ON DELETE SET NULL,
    repository_full_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'pending',
            'running',
            'succeeded',
            'failed',
            'skipped',
            'not_modified'
        )
    ),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    request_count INTEGER NOT NULL DEFAULT 0 CHECK (request_count >= 0),
    discovered_count INTEGER NOT NULL DEFAULT 0 CHECK (discovered_count >= 0),
    success_count INTEGER NOT NULL DEFAULT 0 CHECK (success_count >= 0),
    failure_count INTEGER NOT NULL DEFAULT 0 CHECK (failure_count >= 0),
    skipped_count INTEGER NOT NULL DEFAULT 0 CHECK (skipped_count >= 0),
    rate_limit_remaining INTEGER,
    rate_limit_reset_at TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    sync_cursor TEXT,
    etag TEXT,
    last_modified TEXT,
    error_code TEXT,
    error_summary TEXT,
    UNIQUE (sync_run_id, repository_full_name)
);

CREATE INDEX sync_run_started_at_idx
    ON sync_run(started_at DESC);

CREATE UNIQUE INDEX sync_run_one_running_idx
    ON sync_run(status)
    WHERE status = 'running';

CREATE INDEX sync_repository_result_run_status_idx
    ON sync_repository_result(sync_run_id, status, repository_full_name);

CREATE INDEX task_candidate_availability_idx
    ON task_candidate(candidate_availability, github_verified_at, repository_id);
