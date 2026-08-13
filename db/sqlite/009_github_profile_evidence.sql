PRAGMA foreign_keys = ON;

-- B v0.5 profile ownership association.
--
-- user_key is intentionally an application-level stable identifier.
-- The authentication/user table is owned by the platform layer and is
-- therefore not introduced by this migration.
CREATE TABLE IF NOT EXISTS profile_user_binding (
    profile_user_binding_id INTEGER PRIMARY KEY,
    user_key TEXT NOT NULL UNIQUE,
    developer_profile_id INTEGER NOT NULL UNIQUE
        REFERENCES developer_profile(developer_profile_id)
        ON DELETE CASCADE,
    linked_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Persist per-field provenance and edit protection.
--
-- Effective profile values remain in developer_profile / developer_skill.
-- This table records where a field came from, whether it is locked,
-- and the evidence retained after a user accepts a GitHub suggestion.
CREATE TABLE IF NOT EXISTS profile_field_state (
    developer_profile_id INTEGER NOT NULL
        REFERENCES developer_profile(developer_profile_id)
        ON DELETE CASCADE,
    field_name TEXT NOT NULL,
    source TEXT NOT NULL CHECK (
        source IN (
            'default',
            'github_weak_inference',
            'github_explicit_evidence',
            'user_input',
            'user_confirmed'
        )
    ),
    locked INTEGER NOT NULL DEFAULT 0 CHECK (
        locked IN (0, 1)
    ),
    observed_at TEXT,
    accepted_source TEXT CHECK (
        accepted_source IS NULL
        OR accepted_source IN (
            'github_weak_inference',
            'github_explicit_evidence'
        )
    ),
    confidence REAL CHECK (
        confidence IS NULL
        OR (
            confidence >= 0.0
            AND confidence <= 1.0
        )
    ),
    evidence_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (
        developer_profile_id,
        field_name
    )
);

-- One sanitized public-GitHub import operation.
--
-- No OAuth token, cookie, authorization code, state parameter, private
-- repository payload, or other authentication secret belongs in this table.
CREATE TABLE IF NOT EXISTS github_profile_import (
    github_profile_import_id INTEGER PRIMARY KEY,
    developer_profile_id INTEGER NOT NULL
        REFERENCES developer_profile(developer_profile_id)
        ON DELETE CASCADE,
    import_key TEXT NOT NULL,
    github_login TEXT NOT NULL COLLATE NOCASE,
    import_version TEXT NOT NULL,
    consent_version TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    public_repository_count INTEGER NOT NULL DEFAULT 0
        CHECK (
            public_repository_count >= 0
        ),
    recent_repository_count INTEGER NOT NULL DEFAULT 0
        CHECK (
            recent_repository_count >= 0
        ),
    summary_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (
        developer_profile_id,
        import_key
    )
);

-- GitHub-derived changes are suggestions only.
-- They never silently overwrite a manually maintained profile field.
CREATE TABLE IF NOT EXISTS profile_field_suggestion (
    profile_field_suggestion_id INTEGER PRIMARY KEY,
    github_profile_import_id INTEGER NOT NULL
        REFERENCES github_profile_import(github_profile_import_id)
        ON DELETE CASCADE,
    developer_profile_id INTEGER NOT NULL
        REFERENCES developer_profile(developer_profile_id)
        ON DELETE CASCADE,
    field_name TEXT NOT NULL,
    current_value_json TEXT,
    proposed_value_json TEXT NOT NULL,
    suggestion_source TEXT NOT NULL CHECK (
        suggestion_source IN (
            'github_explicit_evidence',
            'github_weak_inference'
        )
    ),
    confidence REAL NOT NULL CHECK (
        confidence >= 0.0
        AND confidence <= 1.0
    ),
    evidence_json TEXT NOT NULL DEFAULT '[]',
    observed_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN (
            'pending',
            'accepted',
            'rejected'
        )
    ),
    blocked_reason TEXT,
    resolved_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (
        github_profile_import_id,
        field_name
    )
);

-- Explainable GitHub evidence supporting an inferred developer skill.
--
-- This is separate from developer_skill:
-- developer_skill stores the effective profile value;
-- developer_skill_evidence records why GitHub suggested that value.
CREATE TABLE IF NOT EXISTS developer_skill_evidence (
    developer_skill_evidence_id INTEGER PRIMARY KEY,
    developer_profile_id INTEGER NOT NULL
        REFERENCES developer_profile(developer_profile_id)
        ON DELETE CASCADE,
    github_profile_import_id INTEGER NOT NULL
        REFERENCES github_profile_import(github_profile_import_id)
        ON DELETE CASCADE,
    skill_name TEXT NOT NULL COLLATE NOCASE,
    evidence_source TEXT NOT NULL CHECK (
        evidence_source IN (
            'github_explicit_evidence',
            'github_weak_inference'
        )
    ),
    confidence REAL NOT NULL CHECK (
        confidence >= 0.0
        AND confidence <= 1.0
    ),
    evidence_json TEXT NOT NULL DEFAULT '[]',
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (
        github_profile_import_id,
        skill_name,
        evidence_source
    )
);

CREATE INDEX IF NOT EXISTS profile_user_binding_profile_idx
    ON profile_user_binding(
        developer_profile_id
    );

CREATE INDEX IF NOT EXISTS profile_field_state_profile_source_idx
    ON profile_field_state(
        developer_profile_id,
        source,
        field_name
    );

CREATE INDEX IF NOT EXISTS github_profile_import_profile_observed_idx
    ON github_profile_import(
        developer_profile_id,
        observed_at DESC
    );

CREATE INDEX IF NOT EXISTS github_profile_import_login_idx
    ON github_profile_import(
        github_login,
        observed_at DESC
    );

CREATE INDEX IF NOT EXISTS profile_field_suggestion_profile_status_idx
    ON profile_field_suggestion(
        developer_profile_id,
        status,
        created_at DESC
    );

CREATE INDEX IF NOT EXISTS profile_field_suggestion_import_idx
    ON profile_field_suggestion(
        github_profile_import_id,
        field_name
    );

CREATE INDEX IF NOT EXISTS developer_skill_evidence_profile_skill_idx
    ON developer_skill_evidence(
        developer_profile_id,
        skill_name
    );