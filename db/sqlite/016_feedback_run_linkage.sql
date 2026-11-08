ALTER TABLE recommendation_feedback
    ADD COLUMN recommendation_run_id TEXT
        REFERENCES recommendation_run(run_id) ON DELETE SET NULL;

ALTER TABLE recommendation_feedback_event
    ADD COLUMN run_id TEXT
        REFERENCES recommendation_run(run_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS recommendation_feedback_run_idx
    ON recommendation_feedback(recommendation_run_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS recommendation_feedback_event_run_idx
    ON recommendation_feedback_event(run_id, occurred_at);
