CREATE TABLE IF NOT EXISTS oss_user (
    user_id INTEGER PRIMARY KEY,
    github_user_id INTEGER NOT NULL UNIQUE,
    github_login TEXT NOT NULL,
    display_name TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS oauth_identity (
    oauth_identity_id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES oss_user(user_id) ON DELETE CASCADE,
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
    oauth_state_id INTEGER PRIMARY KEY,
    state_hash TEXT NOT NULL UNIQUE,
    return_to TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_session (
    user_session_id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES oss_user(user_id) ON DELETE CASCADE,
    session_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT
);

CREATE INDEX IF NOT EXISTS user_session_user_idx ON user_session(user_id);
CREATE INDEX IF NOT EXISTS user_session_expiry_idx ON user_session(expires_at);
CREATE INDEX IF NOT EXISTS oauth_state_expiry_idx ON oauth_state(expires_at);
