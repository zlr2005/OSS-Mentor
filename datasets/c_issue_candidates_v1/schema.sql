CREATE TABLE repository (
    repository_id INTEGER PRIMARY KEY,
    full_name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    github_repository_id INTEGER,
    html_url TEXT NOT NULL,
    ecosystems_last_synced_at TEXT,
    first_collected_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
, ecosystem TEXT, primary_language TEXT, github_verified_at TEXT, is_archived INTEGER NOT NULL DEFAULT 0
    CHECK (is_archived IN (0, 1)), is_disabled INTEGER NOT NULL DEFAULT 0
    CHECK (is_disabled IN (0, 1)), pushed_at TEXT, last_candidate_sync_at TEXT, last_candidate_refresh_at TEXT, maintenance_status TEXT NOT NULL DEFAULT 'active'
    CHECK (maintenance_status IN ('active', 'inactive', 'unknown')), maintenance_reason TEXT, activity_checked_at TEXT, candidate_sync_cursor TEXT, candidate_sync_etag TEXT, candidate_sync_last_modified TEXT, github_updated_at TEXT, has_contributing_guide INTEGER NOT NULL DEFAULT 0
    CHECK (has_contributing_guide IN (0, 1)));

CREATE INDEX repository_recommendable_idx
    ON repository(is_archived, is_disabled, full_name);

CREATE INDEX repository_maintenance_status_idx
    ON repository(maintenance_status, is_archived, is_disabled);

CREATE TABLE task_candidate (
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
    normalized_at TEXT NOT NULL, has_reproduction_steps INTEGER, has_acceptance_criteria INTEGER, has_expected_behavior INTEGER, has_affected_module_hint INTEGER, task_types_json TEXT NOT NULL DEFAULT '[]', text_clarity_score REAL, estimated_code_difficulty INTEGER, estimated_setup_difficulty INTEGER, estimated_project_context_difficulty INTEGER, estimated_collaboration_difficulty INTEGER, estimated_effort_bucket TEXT, novice_fit_probability REAL, newcomer_score REAL, growth_value_score REAL, feature_evidence_json TEXT NOT NULL DEFAULT '{}', feature_extracted_at TEXT, task_feature_version TEXT, candidate_availability TEXT NOT NULL
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
    ), availability_reasons_json TEXT NOT NULL
    DEFAULT '["not_yet_verified_for_v0.5"]', verification_expires_at TEXT, inventory_review_status TEXT, inventory_source TEXT, inventory_record_json TEXT,
    UNIQUE (repository_id, issue_number)
);

CREATE INDEX task_candidate_eligibility_idx
    ON task_candidate(candidate_eligibility, newcomer_label_signal, last_activity_at DESC);

CREATE INDEX task_candidate_repository_idx
    ON task_candidate(repository_id, issue_number);

CREATE INDEX task_candidate_newcomer_rank_idx
    ON task_candidate(candidate_eligibility, newcomer_score DESC);

CREATE INDEX task_candidate_growth_rank_idx
    ON task_candidate(candidate_eligibility, growth_value_score DESC);

CREATE INDEX task_candidate_verification_idx
    ON task_candidate(github_verified_at, repository_id);

CREATE INDEX task_candidate_availability_idx
    ON task_candidate(candidate_availability, github_verified_at, repository_id);

CREATE INDEX task_candidate_expiry_idx ON task_candidate(verification_expires_at);

CREATE INDEX task_candidate_github_identity_idx ON task_candidate(github_issue_id);

CREATE TABLE task_skill_requirement (
    task_candidate_id INTEGER NOT NULL REFERENCES task_candidate(task_candidate_id)
        ON DELETE CASCADE,
    skill_name TEXT NOT NULL COLLATE NOCASE,
    minimum_level INTEGER NOT NULL CHECK (minimum_level BETWEEN 0 AND 4),
    importance REAL NOT NULL CHECK (importance > 0 AND importance <= 1),
    requirement_source TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    PRIMARY KEY (task_candidate_id, skill_name)
);

CREATE INDEX task_skill_requirement_skill_idx
    ON task_skill_requirement(skill_name, minimum_level);
