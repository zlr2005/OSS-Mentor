-- OSS-Mentor v0.5 PostgreSQL baseline.
-- Equivalent to the final structure of SQLite migrations 001-008.
-- Idempotent: safe to run on an empty database.

CREATE TABLE IF NOT EXISTS repository (
    repository_id BIGSERIAL PRIMARY KEY,
    full_name TEXT NOT NULL UNIQUE,
    github_repository_id BIGINT,
    html_url TEXT NOT NULL,
    ecosystems_last_synced_at TEXT,
    first_collected_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    ecosystem TEXT,
    primary_language TEXT,
    github_verified_at TEXT,
    is_archived BOOLEAN NOT NULL DEFAULT FALSE,
    is_disabled BOOLEAN NOT NULL DEFAULT FALSE,
    pushed_at TEXT,
    last_candidate_sync_at TEXT,
    maintenance_status TEXT,
    maintenance_reason TEXT,
    activity_checked_at TEXT,
    candidate_sync_cursor TEXT,
    candidate_sync_etag TEXT,
    candidate_sync_last_modified TEXT,
    github_updated_at TEXT
);

CREATE TABLE IF NOT EXISTS task_candidate (
    task_candidate_id BIGSERIAL PRIMARY KEY,
    repository_id BIGINT NOT NULL REFERENCES repository(repository_id) ON DELETE CASCADE,
    issue_number INTEGER NOT NULL CHECK (issue_number > 0),
    github_issue_id BIGINT,
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
    is_locked BOOLEAN NOT NULL DEFAULT FALSE,
    has_linked_open_pr BOOLEAN,
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
    newcomer_label_signal BOOLEAN NOT NULL DEFAULT FALSE,
    feature_definition_version TEXT NOT NULL,
    normalized_at TEXT NOT NULL,
    task_types_json TEXT,
    text_clarity_score REAL,
    estimated_code_difficulty INTEGER,
    estimated_setup_difficulty INTEGER,
    estimated_project_context_difficulty INTEGER,
    estimated_collaboration_difficulty INTEGER,
    estimated_effort_bucket TEXT,
    novice_fit_probability REAL,
    newcomer_score REAL,
    growth_value_score REAL,
    feature_evidence_json TEXT,
    feature_extracted_at TEXT,
    task_feature_version TEXT,
    has_reproduction_steps BOOLEAN,
    has_acceptance_criteria BOOLEAN,
    has_expected_behavior BOOLEAN,
    has_affected_module_hint BOOLEAN,
    candidate_availability TEXT NOT NULL DEFAULT 'temporarily_unverified' CHECK (
        candidate_availability IN (
            'available', 'closed', 'assigned', 'linked_open_pr',
            'locked', 'repository_inactive', 'temporarily_unverified'
        )
    ),
    availability_reasons_json TEXT NOT NULL DEFAULT '[]',
    last_verified_at TEXT,
    UNIQUE (repository_id, issue_number)
);

CREATE INDEX IF NOT EXISTS task_candidate_eligibility_idx
    ON task_candidate(candidate_eligibility, newcomer_label_signal, last_activity_at DESC);
CREATE INDEX IF NOT EXISTS task_candidate_repository_idx
    ON task_candidate(repository_id, issue_number);

CREATE TABLE IF NOT EXISTS developer_profile (
    developer_profile_id BIGSERIAL PRIMARY KEY,
    profile_key TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    service_track TEXT NOT NULL CHECK (service_track IN ('newcomer', 'growth', 'hybrid')),
    preferred_languages_json TEXT NOT NULL DEFAULT '[]',
    operating_systems_json TEXT NOT NULL DEFAULT '[]',
    preferred_task_types_json TEXT NOT NULL DEFAULT '[]',
    max_code_difficulty INTEGER NOT NULL CHECK (max_code_difficulty BETWEEN 0 AND 3),
    max_setup_difficulty INTEGER NOT NULL CHECK (max_setup_difficulty BETWEEN 0 AND 3),
    desired_skill_stretch INTEGER NOT NULL CHECK (desired_skill_stretch BETWEEN 0 AND 2),
    profile_source TEXT NOT NULL CHECK (profile_source IN ('demo', 'user_input', 'import')),
    consent_version TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS developer_skill (
    developer_profile_id BIGINT NOT NULL REFERENCES developer_profile(developer_profile_id)
        ON DELETE CASCADE,
    skill_name TEXT NOT NULL,
    skill_level INTEGER NOT NULL CHECK (skill_level BETWEEN 0 AND 4),
    evidence_source TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (developer_profile_id, skill_name)
);

CREATE TABLE IF NOT EXISTS task_skill_requirement (
    task_candidate_id BIGINT NOT NULL REFERENCES task_candidate(task_candidate_id)
        ON DELETE CASCADE,
    skill_name TEXT NOT NULL,
    minimum_level INTEGER NOT NULL CHECK (minimum_level BETWEEN 0 AND 4),
    importance REAL NOT NULL CHECK (importance > 0 AND importance <= 1),
    requirement_source TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    PRIMARY KEY (task_candidate_id, skill_name)
);

CREATE TABLE IF NOT EXISTS recommendation_feedback (
    recommendation_feedback_id BIGSERIAL PRIMARY KEY,
    task_candidate_id BIGINT NOT NULL REFERENCES task_candidate(task_candidate_id)
        ON DELETE CASCADE,
    feedback_context TEXT NOT NULL,
    service_track TEXT NOT NULL CHECK (service_track IN ('newcomer', 'growth')),
    feedback_state TEXT NOT NULL CHECK (
        feedback_state IN ('interested', 'not_suitable', 'started', 'completed')
    ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (task_candidate_id, feedback_context)
);

CREATE TABLE IF NOT EXISTS recommendation_feedback_event (
    recommendation_feedback_event_id BIGSERIAL PRIMARY KEY,
    recommendation_feedback_id BIGINT NOT NULL
        REFERENCES recommendation_feedback(recommendation_feedback_id)
        ON DELETE CASCADE,
    previous_state TEXT CHECK (
        previous_state IS NULL OR
        previous_state IN ('interested', 'not_suitable', 'started', 'completed')
    ),
    feedback_state TEXT NOT NULL CHECK (
        feedback_state IN ('interested', 'not_suitable', 'started', 'completed')
    ),
    occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_run (
    sync_run_id BIGSERIAL PRIMARY KEY,
    run_type TEXT NOT NULL DEFAULT 'repository_sync' CHECK (
        run_type IN ('repository_sync', 'candidate_refresh')
    ),
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'running', 'succeeded', 'partially_succeeded', 'failed')
    ),
    started_at TEXT,
    finished_at TEXT,
    requested_by TEXT NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    rate_limit_remaining INTEGER,
    rate_limit_reset_at TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_summary TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_repository_result (
    sync_repository_result_id BIGSERIAL PRIMARY KEY,
    sync_run_id BIGINT NOT NULL REFERENCES sync_run(sync_run_id) ON DELETE CASCADE,
    repository_id BIGINT NOT NULL REFERENCES repository(repository_id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'running', 'succeeded', 'partially_succeeded', 'failed')
    ),
    started_at TEXT,
    finished_at TEXT,
    request_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    retry_count INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_summary TEXT,
    UNIQUE (sync_run_id, repository_id)
);

-- Identity and sessions (SQLite 007 equivalent)
CREATE TABLE IF NOT EXISTS oss_user (
    user_id BIGSERIAL PRIMARY KEY,
    github_user_id BIGINT NOT NULL UNIQUE,
    github_login TEXT NOT NULL,
    display_name TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS oauth_identity (
    oauth_identity_id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES oss_user(user_id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    provider_user_id TEXT NOT NULL,
    access_token_ref TEXT NOT NULL,
    scope TEXT NOT NULL,
    token_expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (provider, provider_user_id)
);

CREATE TABLE IF NOT EXISTS oauth_state (
    oauth_state_id BIGSERIAL PRIMARY KEY,
    state_hash TEXT NOT NULL UNIQUE,
    return_to TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_session (
    user_session_id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES oss_user(user_id) ON DELETE CASCADE,
    session_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT
);

CREATE INDEX IF NOT EXISTS user_session_user_idx ON user_session(user_id);
CREATE INDEX IF NOT EXISTS user_session_expiry_idx ON user_session(expires_at);
CREATE INDEX IF NOT EXISTS oauth_state_expiry_idx ON oauth_state(expires_at);
