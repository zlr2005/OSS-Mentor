ALTER TABLE task_candidate ADD COLUMN has_reproduction_steps INTEGER;
ALTER TABLE task_candidate ADD COLUMN has_acceptance_criteria INTEGER;
ALTER TABLE task_candidate ADD COLUMN has_expected_behavior INTEGER;
ALTER TABLE task_candidate ADD COLUMN has_affected_module_hint INTEGER;
ALTER TABLE task_candidate ADD COLUMN task_types_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE task_candidate ADD COLUMN text_clarity_score REAL;
ALTER TABLE task_candidate ADD COLUMN estimated_code_difficulty INTEGER;
ALTER TABLE task_candidate ADD COLUMN estimated_setup_difficulty INTEGER;
ALTER TABLE task_candidate ADD COLUMN estimated_project_context_difficulty INTEGER;
ALTER TABLE task_candidate ADD COLUMN estimated_collaboration_difficulty INTEGER;
ALTER TABLE task_candidate ADD COLUMN estimated_effort_bucket TEXT;
ALTER TABLE task_candidate ADD COLUMN novice_fit_probability REAL;
ALTER TABLE task_candidate ADD COLUMN newcomer_score REAL;
ALTER TABLE task_candidate ADD COLUMN growth_value_score REAL;
ALTER TABLE task_candidate ADD COLUMN feature_evidence_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE task_candidate ADD COLUMN feature_extracted_at TEXT;
ALTER TABLE task_candidate ADD COLUMN task_feature_version TEXT;

CREATE INDEX IF NOT EXISTS task_candidate_newcomer_rank_idx
    ON task_candidate(candidate_eligibility, newcomer_score DESC);

CREATE INDEX IF NOT EXISTS task_candidate_growth_rank_idx
    ON task_candidate(candidate_eligibility, growth_value_score DESC);
