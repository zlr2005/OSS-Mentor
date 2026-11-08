-- Read-only Issue batch; local export only.
SELECT platform, type, action, repo_id, repo_name, issue_id, issue_number,
    issue_title, body, `issue_labels.name`,
    toTimeZone(issue_created_at, 'UTC') AS issue_created_at_utc,
    toTimeZone(created_at, 'UTC') AS event_time_utc
FROM opensource.events
WHERE toYYYYMM(created_at) IN (202607, 202608, 202609, 202610)
  AND type = 'IssuesEvent' AND action = 'opened'
  AND org_id IN (0, 69631, 698437, 42944450, 1342004, 126733545, 6154722, 173204339)
  AND repo_id IN (97053386, 6498492, 85095608, 188953831, 450625045, 94911145,
    36469177, 29759715, 267719344, 35969061, 364474335, 1027384981,
    884411799, 156293506, 349233926, 61973853, 926248435)
LIMIT 1000
SETTINGS max_threads = 1, max_block_size = 8192, max_execution_time = 15,
    max_rows_to_read = 1000000, max_bytes_to_read = 134217728;
