CREATE TABLE IF NOT EXISTS recommendation_feedback (
    recommendation_feedback_id INTEGER PRIMARY KEY,
    task_candidate_id INTEGER NOT NULL REFERENCES task_candidate(task_candidate_id)
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
    recommendation_feedback_event_id INTEGER PRIMARY KEY,
    recommendation_feedback_id INTEGER NOT NULL
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

CREATE INDEX IF NOT EXISTS recommendation_feedback_context_idx
    ON recommendation_feedback(feedback_context, updated_at DESC);

CREATE INDEX IF NOT EXISTS recommendation_feedback_event_timeline_idx
    ON recommendation_feedback_event(recommendation_feedback_id, occurred_at);
