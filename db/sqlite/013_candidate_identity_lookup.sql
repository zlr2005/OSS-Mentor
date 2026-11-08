-- Global identity checks must remain fast as the inventory grows.
CREATE INDEX IF NOT EXISTS task_candidate_github_identity_idx ON task_candidate(github_issue_id);
