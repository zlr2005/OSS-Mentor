-- Read-only verification, restricted to one known repository and month.
-- Repository/owner IDs were verified from the public GitHub repository API.
-- This is SELECT only: no INSERT/UPDATE/DELETE/CREATE/ALTER or persistent SET.
SELECT
    platform,
    type,
    action,
    repo_id,
    repo_name,
    issue_number,
    issue_title,
    substringUTF8(body, 1, 1500) AS body_excerpt,
    `issue_labels.name`,
    toTimeZone(created_at, 'UTC') AS event_time_utc
FROM opensource.events
WHERE toYYYYMM(created_at) = 202610
  AND type = 'IssuesEvent'
  AND action = 'opened'
  AND org_id IN (0, 69631)
  AND repo_id = 94911145
LIMIT 5
SETTINGS
    max_threads = 1,
    max_block_size = 8192,
    max_execution_time = 15,
    max_rows_to_read = 1000000,
    max_bytes_to_read = 134217728;
