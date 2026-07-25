ALTER TABLE repository ADD COLUMN ecosystem TEXT;
ALTER TABLE repository ADD COLUMN primary_language TEXT;

CREATE TABLE IF NOT EXISTS developer_profile (
    developer_profile_id INTEGER PRIMARY KEY,
    profile_key TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    service_track TEXT NOT NULL CHECK (
        service_track IN ('newcomer', 'growth', 'hybrid')
    ),
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
    developer_profile_id INTEGER NOT NULL REFERENCES developer_profile(developer_profile_id)
        ON DELETE CASCADE,
    skill_name TEXT NOT NULL COLLATE NOCASE,
    skill_level INTEGER NOT NULL CHECK (skill_level BETWEEN 0 AND 4),
    evidence_source TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (developer_profile_id, skill_name)
);

CREATE TABLE IF NOT EXISTS task_skill_requirement (
    task_candidate_id INTEGER NOT NULL REFERENCES task_candidate(task_candidate_id)
        ON DELETE CASCADE,
    skill_name TEXT NOT NULL COLLATE NOCASE,
    minimum_level INTEGER NOT NULL CHECK (minimum_level BETWEEN 0 AND 4),
    importance REAL NOT NULL CHECK (importance > 0 AND importance <= 1),
    requirement_source TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    PRIMARY KEY (task_candidate_id, skill_name)
);

CREATE INDEX IF NOT EXISTS developer_profile_track_idx
    ON developer_profile(service_track);

CREATE INDEX IF NOT EXISTS task_skill_requirement_skill_idx
    ON task_skill_requirement(skill_name, minimum_level);
