PRAGMA foreign_keys = ON;

-- B-owned persistence for the midterm questionnaire, preview, confirmation,
-- and immutable enhanced-profile feature history.  Authentication ownership
-- always enters through oss_user.user_id and profile_user_binding.

CREATE TABLE IF NOT EXISTS profile_questionnaire (
    questionnaire_id TEXT NOT NULL,
    questionnaire_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    feature_schema_version TEXT NOT NULL,
    definition_json TEXT NOT NULL,
    definition_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (questionnaire_id, questionnaire_version)
);

CREATE TABLE IF NOT EXISTS profile_assessment_submission (
    submission_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL
        REFERENCES oss_user(user_id) ON DELETE CASCADE,
    developer_profile_id INTEGER NOT NULL
        REFERENCES developer_profile(developer_profile_id) ON DELETE CASCADE,
    questionnaire_id TEXT NOT NULL,
    questionnaire_version TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    answers_json TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (user_id, idempotency_key),
    FOREIGN KEY (questionnaire_id, questionnaire_version)
        REFERENCES profile_questionnaire(questionnaire_id, questionnaire_version)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS profile_assessment_preview (
    preview_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL
        REFERENCES oss_user(user_id) ON DELETE CASCADE,
    developer_profile_id INTEGER NOT NULL
        REFERENCES developer_profile(developer_profile_id) ON DELETE CASCADE,
    submission_id TEXT NOT NULL
        REFERENCES profile_assessment_submission(submission_id) ON DELETE CASCADE,
    base_profile_version TEXT NOT NULL,
    questionnaire_id TEXT NOT NULL,
    questionnaire_version TEXT NOT NULL,
    feature_schema_version TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    preview_json TEXT NOT NULL,
    preview_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (questionnaire_id, questionnaire_version)
        REFERENCES profile_questionnaire(questionnaire_id, questionnaire_version)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS profile_assessment_preview_feature (
    preview_id TEXT NOT NULL
        REFERENCES profile_assessment_preview(preview_id) ON DELETE CASCADE,
    feature_name TEXT NOT NULL,
    value REAL NOT NULL CHECK (value >= 0.0 AND value <= 1.0),
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    observed INTEGER NOT NULL CHECK (observed IN (0, 1)),
    locked INTEGER NOT NULL CHECK (locked IN (0, 1)),
    conflict INTEGER NOT NULL CHECK (conflict IN (0, 1)),
    source_kind TEXT CHECK (
        source_kind IS NULL OR source_kind IN (
            'github_weak',
            'assessment_self_report',
            'github_direct',
            'user_confirmed',
            'user_locked'
        )
    ),
    blocked_reason TEXT CHECK (
        blocked_reason IS NULL OR blocked_reason = 'field_locked'
    ),
    evidence_json TEXT NOT NULL,
    PRIMARY KEY (preview_id, feature_name),
    CHECK (observed = 1 OR value = 0.0),
    CHECK (locked = 0 OR observed = 1)
);

CREATE TABLE IF NOT EXISTS profile_assessment_plan (
    plan_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL
        REFERENCES oss_user(user_id) ON DELETE CASCADE,
    developer_profile_id INTEGER NOT NULL
        REFERENCES developer_profile(developer_profile_id) ON DELETE CASCADE,
    preview_id TEXT NOT NULL
        REFERENCES profile_assessment_preview(preview_id) ON DELETE CASCADE,
    profile_key TEXT NOT NULL,
    base_profile_version TEXT NOT NULL,
    questionnaire_version TEXT NOT NULL,
    feature_schema_version TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    plan_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'completed', 'stale')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_assessment_suggestion (
    plan_id TEXT NOT NULL
        REFERENCES profile_assessment_plan(plan_id) ON DELETE CASCADE,
    suggestion_id TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    current_json TEXT NOT NULL,
    proposed_json TEXT NOT NULL,
    conflict INTEGER NOT NULL CHECK (conflict IN (0, 1)),
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'accepted', 'rejected', 'blocked')
    ),
    blocked_reason TEXT CHECK (
        blocked_reason IS NULL OR blocked_reason = 'field_locked'
    ),
    created_at TEXT NOT NULL,
    PRIMARY KEY (plan_id, suggestion_id)
);

CREATE TABLE IF NOT EXISTS profile_assessment_confirmation (
    confirmation_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL UNIQUE
        REFERENCES profile_assessment_plan(plan_id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL
        REFERENCES oss_user(user_id) ON DELETE CASCADE,
    developer_profile_id INTEGER NOT NULL
        REFERENCES developer_profile(developer_profile_id) ON DELETE CASCADE,
    profile_key TEXT NOT NULL,
    base_profile_version TEXT NOT NULL,
    new_profile_version TEXT NOT NULL,
    confirmed_at TEXT NOT NULL,
    result_json TEXT NOT NULL,
    result_sha256 TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (user_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS profile_assessment_decision (
    confirmation_id TEXT NOT NULL
        REFERENCES profile_assessment_confirmation(confirmation_id) ON DELETE CASCADE,
    plan_id TEXT NOT NULL,
    suggestion_id TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('accept', 'reject')),
    conflict_was_visible INTEGER NOT NULL CHECK (conflict_was_visible IN (0, 1)),
    decided_at TEXT NOT NULL,
    PRIMARY KEY (confirmation_id, suggestion_id),
    FOREIGN KEY (plan_id, suggestion_id)
        REFERENCES profile_assessment_suggestion(plan_id, suggestion_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS developer_profile_feature_state (
    feature_state_id TEXT PRIMARY KEY,
    developer_profile_id INTEGER NOT NULL
        REFERENCES developer_profile(developer_profile_id) ON DELETE CASCADE,
    profile_key TEXT NOT NULL,
    profile_version TEXT NOT NULL,
    feature_schema_version TEXT NOT NULL,
    confirmation_id TEXT NOT NULL UNIQUE
        REFERENCES profile_assessment_confirmation(confirmation_id) ON DELETE CASCADE,
    state_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (developer_profile_id, profile_version)
);

CREATE TABLE IF NOT EXISTS developer_profile_feature (
    feature_state_id TEXT NOT NULL
        REFERENCES developer_profile_feature_state(feature_state_id) ON DELETE CASCADE,
    feature_name TEXT NOT NULL,
    observed INTEGER NOT NULL CHECK (observed IN (0, 1)),
    value REAL NOT NULL CHECK (value >= 0.0 AND value <= 1.0),
    locked INTEGER NOT NULL CHECK (locked IN (0, 1)),
    evidence_json TEXT NOT NULL,
    PRIMARY KEY (feature_state_id, feature_name),
    CHECK (observed = 1 OR value = 0.0),
    CHECK (locked = 0 OR observed = 1)
);

CREATE TABLE IF NOT EXISTS developer_profile_current_feature (
    developer_profile_id INTEGER PRIMARY KEY
        REFERENCES developer_profile(developer_profile_id) ON DELETE CASCADE,
    feature_state_id TEXT NOT NULL UNIQUE
        REFERENCES developer_profile_feature_state(feature_state_id) ON DELETE CASCADE,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS profile_assessment_submission_user_idx
    ON profile_assessment_submission(user_id, submitted_at DESC);

CREATE INDEX IF NOT EXISTS profile_assessment_preview_user_idx
    ON profile_assessment_preview(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS profile_assessment_plan_user_status_idx
    ON profile_assessment_plan(user_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS profile_assessment_confirmation_user_idx
    ON profile_assessment_confirmation(user_id, confirmed_at DESC);

CREATE INDEX IF NOT EXISTS developer_profile_feature_state_profile_idx
    ON developer_profile_feature_state(developer_profile_id, created_at DESC);

-- A current pointer may only reference a feature state owned by the same
-- developer profile.  The individual foreign keys prove that both rows exist;
-- these triggers additionally protect the cross-row ownership invariant.
CREATE TRIGGER IF NOT EXISTS developer_profile_current_feature_owner_insert
BEFORE INSERT ON developer_profile_current_feature
WHEN NOT EXISTS (
    SELECT 1
    FROM developer_profile_feature_state AS state
    WHERE state.feature_state_id = NEW.feature_state_id
      AND state.developer_profile_id = NEW.developer_profile_id
)
BEGIN
    SELECT RAISE(ABORT, 'current feature-state owner mismatch');
END;

CREATE TRIGGER IF NOT EXISTS developer_profile_current_feature_owner_update
BEFORE UPDATE OF developer_profile_id, feature_state_id
ON developer_profile_current_feature
WHEN NOT EXISTS (
    SELECT 1
    FROM developer_profile_feature_state AS state
    WHERE state.feature_state_id = NEW.feature_state_id
      AND state.developer_profile_id = NEW.developer_profile_id
)
BEGIN
    SELECT RAISE(ABORT, 'current feature-state owner mismatch');
END;

-- History is append-only.  Deletes remain available for the explicit
-- user/profile cascade, while in-place mutation of audit records is rejected.
CREATE TRIGGER IF NOT EXISTS profile_questionnaire_no_update
BEFORE UPDATE ON profile_questionnaire
BEGIN SELECT RAISE(ABORT, 'profile questionnaire history is immutable'); END;

CREATE TRIGGER IF NOT EXISTS profile_assessment_submission_no_update
BEFORE UPDATE ON profile_assessment_submission
BEGIN SELECT RAISE(ABORT, 'profile assessment submissions are immutable'); END;

CREATE TRIGGER IF NOT EXISTS profile_assessment_preview_no_update
BEFORE UPDATE ON profile_assessment_preview
BEGIN SELECT RAISE(ABORT, 'profile assessment previews are immutable'); END;

CREATE TRIGGER IF NOT EXISTS profile_assessment_preview_feature_no_update
BEFORE UPDATE ON profile_assessment_preview_feature
BEGIN SELECT RAISE(ABORT, 'profile preview features are immutable'); END;

CREATE TRIGGER IF NOT EXISTS profile_assessment_confirmation_no_update
BEFORE UPDATE ON profile_assessment_confirmation
BEGIN SELECT RAISE(ABORT, 'profile assessment confirmations are immutable'); END;

CREATE TRIGGER IF NOT EXISTS profile_assessment_decision_no_update
BEFORE UPDATE ON profile_assessment_decision
BEGIN SELECT RAISE(ABORT, 'profile assessment decisions are immutable'); END;

CREATE TRIGGER IF NOT EXISTS developer_profile_feature_state_no_update
BEFORE UPDATE ON developer_profile_feature_state
BEGIN SELECT RAISE(ABORT, 'profile feature-state history is immutable'); END;

CREATE TRIGGER IF NOT EXISTS developer_profile_feature_no_update
BEFORE UPDATE ON developer_profile_feature
BEGIN SELECT RAISE(ABORT, 'profile feature history is immutable'); END;
