BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS oss_mentor;
CREATE SCHEMA IF NOT EXISTS oss_mentor_private;

COMMENT ON SCHEMA oss_mentor IS
    'OSS-Mentor normalized, snapshot, recommendation, and research data.';
COMMENT ON SCHEMA oss_mentor_private IS
    'Restricted identity and secret-reference data. Exclude from analytics roles.';

SET search_path TO oss_mentor, public;

CREATE TABLE data_collection_run (
    collection_run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_system text NOT NULL CHECK (
        source_system IN (
            'github_rest', 'github_graphql', 'gharchive', 'git_clone',
            'product', 'survey'
        )
    ),
    collector_version text NOT NULL,
    api_version text,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status text NOT NULL CHECK (status IN ('running', 'success', 'partial', 'failed')),
    request_count integer NOT NULL DEFAULT 0 CHECK (request_count >= 0),
    object_count integer NOT NULL DEFAULT 0 CHECK (object_count >= 0),
    error_count integer NOT NULL DEFAULT 0 CHECK (error_count >= 0),
    rate_limit_remaining integer,
    cursor_or_checkpoint jsonb NOT NULL DEFAULT '{}'::jsonb,
    raw_snapshot_uri text,
    schema_version text NOT NULL,
    error_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);

CREATE TABLE raw_object (
    raw_object_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_run_id uuid NOT NULL REFERENCES data_collection_run(collection_run_id),
    source_system text NOT NULL,
    source_endpoint text NOT NULL,
    source_url text NOT NULL,
    request_params jsonb NOT NULL DEFAULT '{}'::jsonb,
    request_fingerprint text NOT NULL,
    api_version text,
    fetched_at timestamptz NOT NULL,
    status_code integer NOT NULL CHECK (status_code BETWEEN 100 AND 599),
    etag text,
    last_modified text,
    pagination_links jsonb NOT NULL DEFAULT '{}'::jsonb,
    rate_limit_limit integer,
    rate_limit_remaining integer,
    rate_limit_used integer,
    rate_limit_reset timestamptz,
    rate_limit_resource text,
    response_sha256 text,
    payload_path text,
    payload_bytes bigint CHECK (payload_bytes IS NULL OR payload_bytes >= 0),
    schema_version text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (request_fingerprint, fetched_at)
);

COMMENT ON TABLE raw_object IS
    'Lineage metadata for immutable gzip JSON envelopes stored outside PostgreSQL.';

CREATE INDEX raw_object_run_idx ON raw_object(collection_run_id);
CREATE INDEX raw_object_endpoint_fetched_idx ON raw_object(source_endpoint, fetched_at DESC);
CREATE INDEX raw_object_response_sha_idx ON raw_object(response_sha256)
    WHERE response_sha256 IS NOT NULL;

CREATE TABLE github_actor (
    actor_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    github_user_id bigint NOT NULL UNIQUE CHECK (github_user_id > 0),
    actor_type text NOT NULL DEFAULT 'User' CHECK (actor_type IN ('User', 'Bot', 'Organization', 'Mannequin', 'Unknown')),
    is_bot boolean NOT NULL DEFAULT false,
    first_seen_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    pseudonym_version text NOT NULL DEFAULT 'v1',
    source_raw_object_id uuid REFERENCES raw_object(raw_object_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (last_seen_at >= first_seen_at)
);

COMMENT ON TABLE github_actor IS
    'Pseudonymous public GitHub actors. Do not store login or email in this table.';

CREATE TABLE repository (
    repository_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    github_repository_id bigint NOT NULL UNIQUE CHECK (github_repository_id > 0),
    full_name text NOT NULL,
    html_url text NOT NULL,
    is_fork boolean NOT NULL,
    is_archived boolean NOT NULL,
    is_mirror boolean,
    license_spdx_id text,
    default_branch text NOT NULL,
    visibility text NOT NULL DEFAULT 'public',
    first_collected_at timestamptz NOT NULL,
    collection_status text NOT NULL DEFAULT 'active' CHECK (
        collection_status IN ('active', 'paused', 'excluded', 'deleted', 'private')
    ),
    exclusion_reason text,
    source_raw_object_id uuid REFERENCES raw_object(raw_object_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX repository_full_name_lower_uq ON repository(lower(full_name));
CREATE INDEX repository_collection_status_idx ON repository(collection_status);

CREATE TABLE repository_snapshot (
    repository_snapshot_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    repository_id uuid NOT NULL REFERENCES repository(repository_id),
    snapshot_at timestamptz NOT NULL,
    primary_language text,
    language_distribution jsonb NOT NULL DEFAULT '{}'::jsonb,
    topics text[] NOT NULL DEFAULT ARRAY[]::text[],
    star_count integer NOT NULL DEFAULT 0 CHECK (star_count >= 0),
    fork_count integer NOT NULL DEFAULT 0 CHECK (fork_count >= 0),
    open_item_count_api integer NOT NULL DEFAULT 0 CHECK (open_item_count_api >= 0),
    open_issue_count integer CHECK (open_issue_count IS NULL OR open_issue_count >= 0),
    active_contributor_count_90d integer CHECK (active_contributor_count_90d IS NULL OR active_contributor_count_90d >= 0),
    commit_count_90d integer CHECK (commit_count_90d IS NULL OR commit_count_90d >= 0),
    median_first_response_hours_180d numeric(10,2) CHECK (median_first_response_hours_180d IS NULL OR median_first_response_hours_180d >= 0),
    median_pr_merge_hours_180d numeric(10,2) CHECK (median_pr_merge_hours_180d IS NULL OR median_pr_merge_hours_180d >= 0),
    first_project_pr_merge_rate_180d numeric(6,5) CHECK (
        first_project_pr_merge_rate_180d IS NULL OR
        first_project_pr_merge_rate_180d BETWEEN 0 AND 1
    ),
    maintainer_response_coverage_180d numeric(6,5) CHECK (
        maintainer_response_coverage_180d IS NULL OR
        maintainer_response_coverage_180d BETWEEN 0 AND 1
    ),
    community_health_percentage integer CHECK (
        community_health_percentage IS NULL OR
        community_health_percentage BETWEEN 0 AND 100
    ),
    has_readme boolean NOT NULL DEFAULT false,
    has_contributing_guide boolean NOT NULL DEFAULT false,
    has_code_of_conduct boolean NOT NULL DEFAULT false,
    has_issue_template boolean NOT NULL DEFAULT false,
    has_pr_template boolean NOT NULL DEFAULT false,
    has_ci_config boolean NOT NULL DEFAULT false,
    has_setup_documentation boolean,
    community_support_score numeric(5,2) CHECK (
        community_support_score IS NULL OR community_support_score BETWEEN 0 AND 100
    ),
    feature_definition_version text NOT NULL,
    source_raw_object_id uuid REFERENCES raw_object(raw_object_id),
    normalized_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (repository_id, snapshot_at)
);

CREATE INDEX repository_snapshot_latest_idx
    ON repository_snapshot(repository_id, snapshot_at DESC);

CREATE TABLE repository_label (
    repository_label_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    repository_id uuid NOT NULL REFERENCES repository(repository_id),
    github_label_id bigint NOT NULL CHECK (github_label_id > 0),
    name text NOT NULL,
    description text,
    color text,
    collected_at timestamptz NOT NULL,
    source_raw_object_id uuid REFERENCES raw_object(raw_object_id),
    UNIQUE (repository_id, github_label_id, collected_at)
);

CREATE INDEX repository_label_name_idx ON repository_label(repository_id, lower(name));

CREATE TABLE guidance_resource (
    resource_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    repository_id uuid NOT NULL REFERENCES repository(repository_id),
    resource_type text NOT NULL CHECK (
        resource_type IN (
            'readme', 'setup', 'contributing', 'testing', 'code_style',
            'architecture', 'communication', 'security', 'issue_template',
            'pr_template'
        )
    ),
    source_url text NOT NULL,
    source_commit_sha text,
    title text,
    extracted_summary text,
    command_snippets jsonb NOT NULL DEFAULT '[]'::jsonb,
    last_verified_at timestamptz NOT NULL,
    quality_score numeric(5,2) CHECK (quality_score IS NULL OR quality_score BETWEEN 0 AND 100),
    source_raw_object_id uuid REFERENCES raw_object(raw_object_id),
    UNIQUE (repository_id, resource_type, source_url, source_commit_sha)
);

CREATE TABLE task (
    task_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    repository_id uuid NOT NULL REFERENCES repository(repository_id),
    github_issue_id bigint NOT NULL CHECK (github_issue_id > 0),
    issue_number integer NOT NULL CHECK (issue_number > 0),
    html_url text NOT NULL,
    created_at timestamptz NOT NULL,
    author_actor_id uuid REFERENCES github_actor(actor_id),
    author_association text,
    first_collected_at timestamptz NOT NULL,
    source_raw_object_id uuid REFERENCES raw_object(raw_object_id),
    UNIQUE (repository_id, github_issue_id),
    UNIQUE (repository_id, issue_number)
);

CREATE INDEX task_repository_created_idx ON task(repository_id, created_at);

CREATE TABLE task_snapshot (
    task_snapshot_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id uuid NOT NULL REFERENCES task(task_id),
    repository_snapshot_id uuid NOT NULL REFERENCES repository_snapshot(repository_snapshot_id),
    snapshot_at timestamptz NOT NULL,
    title text NOT NULL,
    body_text text,
    labels text[] NOT NULL DEFAULT ARRAY[]::text[],
    standardized_label_signals jsonb NOT NULL DEFAULT '{}'::jsonb,
    state text NOT NULL CHECK (state IN ('open', 'closed')),
    state_reason text,
    assignment_state text NOT NULL CHECK (
        assignment_state IN ('unassigned', 'assigned', 'claimed_in_comments', 'unknown')
    ),
    has_linked_open_pr boolean NOT NULL DEFAULT false,
    comment_count integer NOT NULL DEFAULT 0 CHECK (comment_count >= 0),
    participant_count integer CHECK (participant_count IS NULL OR participant_count >= 0),
    age_days integer NOT NULL CHECK (age_days >= 0),
    last_activity_at timestamptz,
    is_locked boolean NOT NULL DEFAULT false,
    is_stale_candidate boolean NOT NULL DEFAULT false,
    has_reproduction_steps boolean,
    has_acceptance_criteria boolean,
    has_expected_behavior boolean,
    has_affected_module_hint boolean,
    task_types text[] NOT NULL DEFAULT ARRAY[]::text[],
    text_clarity_score numeric(5,2) CHECK (text_clarity_score IS NULL OR text_clarity_score BETWEEN 0 AND 100),
    estimated_code_difficulty smallint CHECK (estimated_code_difficulty IS NULL OR estimated_code_difficulty BETWEEN 0 AND 3),
    estimated_setup_difficulty smallint CHECK (estimated_setup_difficulty IS NULL OR estimated_setup_difficulty BETWEEN 0 AND 3),
    estimated_project_context_difficulty smallint CHECK (estimated_project_context_difficulty IS NULL OR estimated_project_context_difficulty BETWEEN 0 AND 3),
    estimated_collaboration_difficulty smallint CHECK (estimated_collaboration_difficulty IS NULL OR estimated_collaboration_difficulty BETWEEN 0 AND 3),
    estimated_effort_bucket text CHECK (
        estimated_effort_bucket IS NULL OR
        estimated_effort_bucket IN ('under_2h', 'half_day', 'one_day', 'multi_day', 'unknown')
    ),
    novice_fit_probability numeric(4,3) CHECK (
        novice_fit_probability IS NULL OR novice_fit_probability BETWEEN 0 AND 1
    ),
    growth_value_score numeric(5,2) CHECK (
        growth_value_score IS NULL OR growth_value_score BETWEEN 0 AND 100
    ),
    candidate_eligibility text NOT NULL CHECK (
        candidate_eligibility IN ('eligible', 'temporarily_ineligible', 'excluded', 'unknown')
    ),
    ineligibility_reasons text[] NOT NULL DEFAULT ARRAY[]::text[],
    feature_definition_version text NOT NULL,
    source_cutoff_at timestamptz NOT NULL,
    source_raw_object_id uuid REFERENCES raw_object(raw_object_id),
    normalized_at timestamptz NOT NULL DEFAULT now(),
    CHECK (source_cutoff_at <= snapshot_at),
    UNIQUE (task_id, snapshot_at)
);

CREATE INDEX task_snapshot_candidate_idx
    ON task_snapshot(candidate_eligibility, state, snapshot_at DESC);
CREATE INDEX task_snapshot_latest_idx ON task_snapshot(task_id, snapshot_at DESC);

CREATE TABLE developer (
    developer_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    github_actor_id uuid UNIQUE REFERENCES github_actor(actor_id),
    account_status text NOT NULL DEFAULT 'active' CHECK (
        account_status IN ('active', 'revoked', 'deleted', 'suspended')
    ),
    service_track text NOT NULL DEFAULT 'unknown' CHECK (
        service_track IN ('newcomer', 'growth', 'hybrid', 'unknown')
    ),
    service_track_source text NOT NULL DEFAULT 'rule' CHECK (
        service_track_source IN ('rule', 'model', 'user_selected', 'admin')
    ),
    service_track_confidence numeric(4,3) CHECK (
        service_track_confidence IS NULL OR service_track_confidence BETWEEN 0 AND 1
    ),
    preferred_locale text,
    timezone text,
    consent_version text NOT NULL,
    consented_at timestamptz NOT NULL,
    authorization_revoked_at timestamptz,
    deleted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE oss_mentor_private.github_identity_map (
    developer_id uuid PRIMARY KEY REFERENCES oss_mentor.developer(developer_id),
    github_user_id bigint NOT NULL UNIQUE CHECK (github_user_id > 0),
    github_login_encrypted bytea NOT NULL,
    scope_set text[] NOT NULL DEFAULT ARRAY[]::text[],
    token_secret_ref text,
    last_verified_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE oss_mentor_private.github_identity_map IS
    'Restricted identity bridge. token_secret_ref points to secret storage; never store a token value.';

CREATE TABLE onboarding_response (
    response_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    developer_id uuid NOT NULL REFERENCES developer(developer_id),
    submitted_at timestamptz NOT NULL,
    questionnaire_version text NOT NULL,
    git_skill_self smallint CHECK (git_skill_self IS NULL OR git_skill_self BETWEEN 0 AND 4),
    github_workflow_skill_self smallint CHECK (github_workflow_skill_self IS NULL OR github_workflow_skill_self BETWEEN 0 AND 4),
    testing_skill_self smallint CHECK (testing_skill_self IS NULL OR testing_skill_self BETWEEN 0 AND 4),
    build_ci_skill_self smallint CHECK (build_ci_skill_self IS NULL OR build_ci_skill_self BETWEEN 0 AND 4),
    weekly_hours_available numeric(5,1) CHECK (
        weekly_hours_available IS NULL OR weekly_hours_available BETWEEN 0 AND 168
    ),
    preferred_task_types text[] NOT NULL DEFAULT ARRAY[]::text[],
    interest_topics text[] NOT NULL DEFAULT ARRAY[]::text[],
    growth_goal text CHECK (
        growth_goal IS NULL OR
        growth_goal IN ('first_pr', 'practice_skill', 'learn_new_skill', 'join_community', 'deep_contribution')
    ),
    challenge_preference text CHECK (
        challenge_preference IS NULL OR challenge_preference IN ('safe', 'balanced', 'challenging')
    ),
    preferred_task_duration text CHECK (
        preferred_task_duration IS NULL OR
        preferred_task_duration IN ('under_2h', 'half_day', 'one_day', 'multi_day', 'unsure')
    ),
    language_skill_self jsonb NOT NULL DEFAULT '{}'::jsonb,
    framework_skill_self jsonb NOT NULL DEFAULT '{}'::jsonb,
    accessibility_needs text[] NOT NULL DEFAULT ARRAY[]::text[]
);

CREATE INDEX onboarding_response_developer_idx
    ON onboarding_response(developer_id, submitted_at DESC);

CREATE TABLE developer_profile_snapshot (
    profile_snapshot_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    developer_id uuid NOT NULL REFERENCES developer(developer_id),
    snapshot_at timestamptz NOT NULL,
    feature_window_days integer NOT NULL CHECK (feature_window_days > 0),
    service_track text NOT NULL CHECK (service_track IN ('newcomer', 'growth', 'hybrid', 'unknown')),
    observed_external_pr_count integer NOT NULL DEFAULT 0 CHECK (observed_external_pr_count >= 0),
    observed_merged_pr_count integer NOT NULL DEFAULT 0 CHECK (observed_merged_pr_count >= 0),
    observed_review_count integer NOT NULL DEFAULT 0 CHECK (observed_review_count >= 0),
    active_contribution_days integer NOT NULL DEFAULT 0 CHECK (active_contribution_days >= 0),
    active_contribution_months integer NOT NULL DEFAULT 0 CHECK (active_contribution_months >= 0),
    contributed_repository_count integer NOT NULL DEFAULT 0 CHECK (contributed_repository_count >= 0),
    contribution_type_distribution jsonb NOT NULL DEFAULT '{}'::jsonb,
    language_evidence_scores jsonb NOT NULL DEFAULT '{}'::jsonb,
    framework_evidence_scores jsonb NOT NULL DEFAULT '{}'::jsonb,
    experience_score numeric(5,2) CHECK (experience_score IS NULL OR experience_score BETWEEN 0 AND 100),
    technical_breadth_score numeric(5,2) CHECK (technical_breadth_score IS NULL OR technical_breadth_score BETWEEN 0 AND 100),
    collaboration_score numeric(5,2) CHECK (collaboration_score IS NULL OR collaboration_score BETWEEN 0 AND 100),
    quality_stability_score numeric(5,2) CHECK (quality_stability_score IS NULL OR quality_stability_score BETWEEN 0 AND 100),
    growth_velocity_score numeric(5,2) CHECK (growth_velocity_score IS NULL OR growth_velocity_score BETWEEN 0 AND 100),
    profile_confidence numeric(4,3) NOT NULL CHECK (profile_confidence BETWEEN 0 AND 1),
    feature_definition_version text NOT NULL,
    source_cutoff_at timestamptz NOT NULL,
    source_raw_object_id uuid REFERENCES raw_object(raw_object_id),
    CHECK (source_cutoff_at <= snapshot_at),
    UNIQUE (developer_id, snapshot_at)
);

CREATE INDEX developer_profile_latest_idx
    ON developer_profile_snapshot(developer_id, snapshot_at DESC);

CREATE TABLE contribution_attempt (
    attempt_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    developer_id uuid REFERENCES developer(developer_id),
    actor_id uuid REFERENCES github_actor(actor_id),
    task_id uuid NOT NULL REFERENCES task(task_id),
    origin_impression_id uuid,
    attempt_source text NOT NULL CHECK (
        attempt_source IN ('recommended', 'organic', 'maintainer_assigned', 'historical', 'unknown')
    ),
    started_at timestamptz,
    claimed_at timestamptz,
    pr_created_at timestamptz,
    ended_at timestamptz,
    attempt_outcome text CHECK (
        attempt_outcome IS NULL OR
        attempt_outcome IN (
            'started_no_pr', 'pr_open', 'merged', 'closed_unmerged',
            'abandoned', 'task_invalidated', 'maintainer_unresponsive', 'unknown'
        )
    ),
    outcome_observed_at timestamptz,
    is_first_observed_project_pr boolean,
    is_first_observed_public_pr boolean,
    first_pr_confidence numeric(4,3) CHECK (
        first_pr_confidence IS NULL OR first_pr_confidence BETWEEN 0 AND 1
    ),
    history_left_censored boolean NOT NULL DEFAULT false,
    self_reported_minutes_spent integer CHECK (
        self_reported_minutes_spent IS NULL OR self_reported_minutes_spent >= 0
    ),
    outcome_reason_codes text[] NOT NULL DEFAULT ARRAY[]::text[],
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (developer_id IS NOT NULL OR actor_id IS NOT NULL),
    CHECK (ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at)
);

CREATE INDEX contribution_attempt_actor_task_idx ON contribution_attempt(actor_id, task_id);
CREATE INDEX contribution_attempt_developer_started_idx ON contribution_attempt(developer_id, started_at DESC);

CREATE TABLE pull_request_fact (
    pull_request_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    repository_id uuid NOT NULL REFERENCES repository(repository_id),
    attempt_id uuid REFERENCES contribution_attempt(attempt_id),
    author_actor_id uuid REFERENCES github_actor(actor_id),
    github_pull_request_id bigint NOT NULL CHECK (github_pull_request_id > 0),
    pr_number integer NOT NULL CHECK (pr_number > 0),
    html_url text NOT NULL,
    opened_at timestamptz NOT NULL,
    closed_at timestamptz,
    merged_at timestamptz,
    state text NOT NULL CHECK (state IN ('open', 'closed', 'merged')),
    draft boolean NOT NULL DEFAULT false,
    head_sha text,
    base_sha text,
    author_association text,
    maintainer_can_modify boolean,
    commit_count_final integer CHECK (commit_count_final IS NULL OR commit_count_final >= 0),
    additions_final integer CHECK (additions_final IS NULL OR additions_final >= 0),
    deletions_final integer CHECK (deletions_final IS NULL OR deletions_final >= 0),
    changed_files_final integer CHECK (changed_files_final IS NULL OR changed_files_final >= 0),
    review_round_count integer CHECK (review_round_count IS NULL OR review_round_count >= 0),
    change_request_count integer CHECK (change_request_count IS NULL OR change_request_count >= 0),
    first_response_hours numeric(10,2) CHECK (first_response_hours IS NULL OR first_response_hours >= 0),
    merge_hours numeric(10,2) CHECK (merge_hours IS NULL OR merge_hours >= 0),
    ci_final_state text CHECK (
        ci_final_state IS NULL OR ci_final_state IN ('success', 'failure', 'cancelled', 'missing', 'unknown')
    ),
    ci_state_confidence numeric(4,3) CHECK (
        ci_state_confidence IS NULL OR ci_state_confidence BETWEEN 0 AND 1
    ),
    is_bot_authored boolean NOT NULL DEFAULT false,
    file_list_truncated boolean NOT NULL DEFAULT false,
    commit_list_truncated boolean NOT NULL DEFAULT false,
    snapshot_finalized_at timestamptz,
    source_raw_object_id uuid REFERENCES raw_object(raw_object_id),
    normalized_at timestamptz NOT NULL DEFAULT now(),
    CHECK (closed_at IS NULL OR closed_at >= opened_at),
    CHECK (merged_at IS NULL OR merged_at >= opened_at),
    UNIQUE (repository_id, github_pull_request_id),
    UNIQUE (repository_id, pr_number)
);

CREATE INDEX pull_request_author_opened_idx ON pull_request_fact(author_actor_id, opened_at);
CREATE INDEX pull_request_repository_state_idx ON pull_request_fact(repository_id, state, opened_at);

CREATE TABLE issue_pull_request_link (
    task_id uuid NOT NULL REFERENCES task(task_id),
    pull_request_id uuid NOT NULL REFERENCES pull_request_fact(pull_request_id),
    link_method text NOT NULL CHECK (
        link_method IN ('closing_reference', 'timeline', 'cross_reference', 'manual', 'text_heuristic')
    ),
    link_confidence numeric(4,3) NOT NULL CHECK (link_confidence BETWEEN 0 AND 1),
    observed_at timestamptz NOT NULL,
    source_raw_object_id uuid REFERENCES raw_object(raw_object_id),
    PRIMARY KEY (task_id, pull_request_id, link_method)
);

CREATE TABLE review_fact (
    review_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    github_review_id bigint NOT NULL UNIQUE CHECK (github_review_id > 0),
    pull_request_id uuid NOT NULL REFERENCES pull_request_fact(pull_request_id),
    reviewer_actor_id uuid REFERENCES github_actor(actor_id),
    review_state text NOT NULL CHECK (
        review_state IN ('approved', 'changes_requested', 'commented', 'dismissed', 'pending', 'unknown')
    ),
    submitted_at timestamptz,
    commit_id text,
    author_association text,
    is_maintainer_review boolean,
    comment_count integer CHECK (comment_count IS NULL OR comment_count >= 0),
    source_raw_object_id uuid REFERENCES raw_object(raw_object_id)
);

CREATE INDEX review_fact_pr_submitted_idx ON review_fact(pull_request_id, submitted_at);

CREATE TABLE file_change_fact (
    file_change_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pull_request_id uuid NOT NULL REFERENCES pull_request_fact(pull_request_id),
    file_path text NOT NULL,
    previous_file_path text,
    file_sha text,
    file_extension text,
    detected_language text,
    change_status text NOT NULL CHECK (
        change_status IN ('added', 'modified', 'removed', 'renamed', 'copied', 'changed', 'unchanged')
    ),
    additions integer CHECK (additions IS NULL OR additions >= 0),
    deletions integer CHECK (deletions IS NULL OR deletions >= 0),
    is_test_file boolean,
    is_documentation_file boolean,
    module_id text,
    source_raw_object_id uuid REFERENCES raw_object(raw_object_id),
    UNIQUE (pull_request_id, file_path)
);

CREATE INDEX file_change_pr_idx ON file_change_fact(pull_request_id);

CREATE TABLE check_run_fact (
    check_run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    github_check_run_id bigint NOT NULL UNIQUE CHECK (github_check_run_id > 0),
    pull_request_id uuid REFERENCES pull_request_fact(pull_request_id),
    repository_id uuid NOT NULL REFERENCES repository(repository_id),
    head_sha text NOT NULL,
    name text NOT NULL,
    app_github_id bigint,
    status text NOT NULL,
    conclusion text,
    started_at timestamptz,
    completed_at timestamptz,
    source_raw_object_id uuid REFERENCES raw_object(raw_object_id),
    CHECK (completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at)
);

CREATE INDEX check_run_pr_idx ON check_run_fact(pull_request_id);
CREATE INDEX check_run_repo_sha_idx ON check_run_fact(repository_id, head_sha);

CREATE TABLE recommendation_session (
    recommendation_session_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    developer_id uuid NOT NULL REFERENCES developer(developer_id),
    profile_snapshot_id uuid NOT NULL REFERENCES developer_profile_snapshot(profile_snapshot_id),
    service_track text NOT NULL CHECK (service_track IN ('newcomer', 'growth', 'hybrid')),
    requested_at timestamptz NOT NULL,
    model_name text NOT NULL,
    model_version text NOT NULL,
    experiment_id text,
    candidate_set_definition text NOT NULL,
    candidate_count integer NOT NULL CHECK (candidate_count >= 0),
    result_count integer NOT NULL CHECK (result_count >= 0),
    challenge_mix_ratio numeric(4,3) CHECK (
        challenge_mix_ratio IS NULL OR challenge_mix_ratio BETWEEN 0 AND 1
    )
);

CREATE INDEX recommendation_session_developer_idx
    ON recommendation_session(developer_id, requested_at DESC);

CREATE TABLE recommendation_impression (
    impression_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    recommendation_session_id uuid NOT NULL REFERENCES recommendation_session(recommendation_session_id),
    task_snapshot_id uuid NOT NULL REFERENCES task_snapshot(task_snapshot_id),
    rank_position integer NOT NULL CHECK (rank_position > 0),
    recommendation_score numeric(14,8) NOT NULL,
    completion_probability numeric(4,3) CHECK (
        completion_probability IS NULL OR completion_probability BETWEEN 0 AND 1
    ),
    growth_value_score numeric(5,2) CHECK (
        growth_value_score IS NULL OR growth_value_score BETWEEN 0 AND 100
    ),
    difficulty_gap numeric(8,3),
    recommendation_reason_codes text[] NOT NULL DEFAULT ARRAY[]::text[],
    displayed_at timestamptz,
    was_viewable boolean NOT NULL DEFAULT false,
    UNIQUE (recommendation_session_id, rank_position),
    UNIQUE (recommendation_session_id, task_snapshot_id)
);

CREATE INDEX recommendation_impression_task_idx ON recommendation_impression(task_snapshot_id);

ALTER TABLE contribution_attempt
    ADD CONSTRAINT contribution_attempt_origin_impression_fk
    FOREIGN KEY (origin_impression_id)
    REFERENCES recommendation_impression(impression_id);

CREATE TABLE interaction_event (
    event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_event_id text,
    developer_id uuid NOT NULL REFERENCES developer(developer_id),
    impression_id uuid REFERENCES recommendation_impression(impression_id),
    task_id uuid REFERENCES task(task_id),
    event_type text NOT NULL CHECK (
        event_type IN (
            'impression', 'open_detail', 'save', 'dismiss', 'start', 'claim',
            'open_guidance', 'copy_command', 'report_blocker', 'create_pr',
            'abandon', 'complete_feedback'
        )
    ),
    occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    session_id uuid,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (received_at >= occurred_at - interval '7 days')
);

CREATE UNIQUE INDEX interaction_event_client_id_uq
    ON interaction_event(client_event_id) WHERE client_event_id IS NOT NULL;
CREATE INDEX interaction_event_developer_time_idx ON interaction_event(developer_id, occurred_at);
CREATE INDEX interaction_event_impression_idx ON interaction_event(impression_id);

CREATE TABLE barrier_feedback (
    barrier_feedback_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id uuid NOT NULL REFERENCES contribution_attempt(attempt_id),
    reported_at timestamptz NOT NULL,
    barrier_types text[] NOT NULL,
    severity smallint CHECK (severity IS NULL OR severity BETWEEN 1 AND 5),
    resolved boolean,
    helpful_resource_ids uuid[] NOT NULL DEFAULT ARRAY[]::uuid[],
    free_text_feedback text
);

CREATE TABLE learning_assessment (
    assessment_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    developer_id uuid NOT NULL REFERENCES developer(developer_id),
    attempt_id uuid REFERENCES contribution_attempt(attempt_id),
    assessment_type text NOT NULL CHECK (
        assessment_type IN ('pre_task', 'post_task', 'periodic', 'self_efficacy')
    ),
    assessment_version text NOT NULL,
    skill_scores jsonb NOT NULL DEFAULT '{}'::jsonb,
    self_efficacy_score numeric(6,2),
    perceived_difficulty smallint CHECK (
        perceived_difficulty IS NULL OR perceived_difficulty BETWEEN 1 AND 5
    ),
    perceived_learning_gain smallint CHECK (
        perceived_learning_gain IS NULL OR perceived_learning_gain BETWEEN 1 AND 5
    ),
    would_attempt_similar_task boolean,
    completed_at timestamptz NOT NULL
);

CREATE INDEX learning_assessment_developer_idx
    ON learning_assessment(developer_id, completed_at DESC);

COMMIT;
