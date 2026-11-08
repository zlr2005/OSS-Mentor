param([switch]$InspectEvents)
# User requirement: query/export only. Never write data, schema, permissions or settings on the teacher DB.
# Read-only connection check. Credentials stay in this process and are never saved.
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$taskReport = Join-Path $taskRoot 'data/midterm/validation/a/teacher_clickhouse_connection.json'
$taskEndpoint = 'http://cc-2ze7189376o5m9759.public.clickhouse.ads.aliyuncs.com:8123/'
$taskStamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$Host.UI.RawUI.WindowTitle = "READ-ONLY VERIFY events - $taskStamp"
$taskOutput = Join-Path $taskRoot "data/midterm/sources/teacher_clickhouse/$taskStamp"
[IO.Directory]::CreateDirectory((Split-Path $taskReport)) | Out-Null
[IO.Directory]::CreateDirectory($taskOutput) | Out-Null
function Save-TaskState([string]$State, [hashtable]$Details = @{}) {
    $taskState = @{status=$State; observed_at=[DateTime]::UtcNow.ToString('o'); output_directory=$taskOutput; password_saved=$false}
    foreach ($taskKey in $Details.Keys) { $taskState[$taskKey] = $Details[$taskKey] }
    $taskTemporary = "$taskReport.partial"
    $taskState | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $taskTemporary -Encoding utf8
    Move-Item -LiteralPath $taskTemporary -Destination $taskReport -Force
    $taskState | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $taskOutput 'status.json') -Encoding utf8
}
Save-TaskState 'waiting_for_credentials'
Write-Host 'Teacher ClickHouse: read-only connection and schema check'
Write-Host "Batch: $taskStamp. SELECT / DESCRIBE only; HTTP GET; no remote writes."
Write-Host 'Enter the NEW username and password here. Password input is hidden and will not be saved.'
$taskUser = Read-Host 'Username'
$taskSecret = Read-Host 'Password' -AsSecureString
$taskHandler = [Net.Http.HttpClientHandler]::new()
$taskHandler.AllowAutoRedirect = $false
$taskClient = [Net.Http.HttpClient]::new($taskHandler)
$taskClient.Timeout = [TimeSpan]::FromSeconds(35)
try {
    if ([string]::IsNullOrWhiteSpace($taskUser) -or $taskSecret.Length -eq 0) { throw 'credentials_missing' }
    $taskCredential = [PSCredential]::new($taskUser, $taskSecret)
    $taskClient.DefaultRequestHeaders.Add('X-ClickHouse-User', $taskUser)
    $taskClient.DefaultRequestHeaders.Add('X-ClickHouse-Key', $taskCredential.GetNetworkCredential().Password)
    $taskClient.DefaultRequestHeaders.Add('User-Agent', 'OSS-Mentor-readonly-source-audit/0.1')
    Save-TaskState 'connecting'
    $taskQueries = @(
        @{name='connection'; sql='SELECT currentUser() AS current_user, currentDatabase() AS database, version() AS server_version FORMAT JSON'},
        @{name='events_schema'; sql='DESCRIBE TABLE opensource.events FORMAT JSON'},
        @{name='events_storage'; sql="SELECT database, name, engine, partition_key, sorting_key FROM system.tables WHERE database='opensource' AND name='events' FORMAT JSON"},
        @{name='events_sample'; sql='SELECT type, action, repo_name, issue_number, issue_title, created_at FROM opensource.events LIMIT 3 SETTINGS max_rows_to_read=100000, max_bytes_to_read=67108864 FORMAT JSON'}
    )
    if ($InspectEvents) {
        # No dependency on system.parts permissions. Restrict samples by event time.
        $taskMonthStart = [DateTime]::UtcNow.ToString('yyyy-MM-01 00:00:00')
        $taskMonthEnd = [DateTime]::UtcNow.AddMonths(1).ToString('yyyy-MM-01 00:00:00')
        $taskMonthNumber = [DateTime]::UtcNow.ToString('yyyyMM')
        $taskLimit = ' LIMIT 5 SETTINGS max_threads=1, max_block_size=8192, max_execution_time=15, max_rows_to_read=1000000, max_bytes_to_read=134217728 FORMAT JSON'
        $taskCommon = "platform, type, action, repo_id, repo_name, actor_id, actor_login, toTimeZone(created_at,'UTC') AS event_time_utc"
        $taskQueries = @(
            @{name='connection'; sql='SELECT currentUser() AS current_user, currentDatabase() AS database, version() AS server_version, timezone() AS server_timezone FORMAT JSON'},
            @{name='events_schema'; sql='DESCRIBE TABLE opensource.events FORMAT JSON'}
        )
        $taskColumns = @{
            IssuesEvent='issue_id, issue_number, issue_title, substringUTF8(body,1,1500) AS body_excerpt, `issue_labels.name`, `issue_assignees.login`, issue_created_at, issue_updated_at, issue_closed_at, issue_closed_by_pull_request_numbers'
            PullRequestEvent='issue_number, issue_title, issue_author_id, issue_author_login, pull_merged, pull_merge_commit_sha, pull_merged_at, pull_changed_files, pull_additions, pull_deletions'
            PushEvent='push_head, push_ref, push_size, arraySlice(`push_commits.message`,1,3) AS commit_message_examples'
            IssueCommentEvent='issue_id, issue_number, issue_title, issue_comment_id, issue_comment_author_id, issue_comment_author_login, substringUTF8(body,1,1000) AS comment_excerpt'
            WatchEvent='issue_number'
        }
        foreach ($taskType in @('IssuesEvent','PullRequestEvent','PushEvent','IssueCommentEvent','WatchEvent')) {
            $taskSelected = $taskColumns[$taskType]
            # Match the leading partition/sorting expression explicitly. Initially sample raw
            # records without ID/name predicates, so missing-field rates cannot force a large scan.
            $taskBase = "SELECT $taskCommon, $taskSelected FROM opensource.events WHERE type='$taskType'"
            $taskQueries += @{name="sample_$taskType"; sql=$taskBase + " AND toYYYYMM(created_at)=$taskMonthNumber" + $taskLimit}
        }
    }
    $taskResults = @()
    @{readonly=$true; transport='HTTP GET'; queries=$taskQueries} | ConvertTo-Json -Depth 8 |
        Set-Content -LiteralPath (Join-Path $taskOutput 'query_plan.json') -Encoding utf8
    $taskQueue = [Collections.Generic.Queue[hashtable]]::new()
    foreach ($taskQuery in $taskQueries) { $taskQueue.Enqueue($taskQuery) }
    while ($taskQueue.Count -gt 0) {
        $taskQuery = $taskQueue.Dequeue()
        if ($taskQuery.sql -notmatch '^(SELECT|DESCRIBE|SHOW)\s' -or $taskQuery.sql.Contains(';')) { throw 'read_only_query_rejected' }
        $taskUri = $taskEndpoint + '?database=opensource&max_execution_time=15&max_result_rows=1000&max_result_bytes=4194304&query=' + [Uri]::EscapeDataString($taskQuery.sql)
        # ClickHouse's HTTP GET interface permits read-only queries.
        $taskResponse = $taskClient.GetAsync($taskUri).GetAwaiter().GetResult()
        try {
            if (-not $taskResponse.IsSuccessStatusCode) {
                $taskCode = [int]$taskResponse.StatusCode
                $taskErrorBody = $taskResponse.Content.ReadAsStringAsync().GetAwaiter().GetResult()
                $taskErrorBody = $taskErrorBody.Replace($taskCredential.GetNetworkCredential().Password, '[REDACTED]')
                if ($taskErrorBody.Length -gt 4000) { $taskErrorBody = $taskErrorBody.Substring(0,4000) }
                @{query=$taskQuery.name; http_status=$taskCode; error=$taskErrorBody} | ConvertTo-Json |
                    Set-Content -LiteralPath (Join-Path $taskOutput ($taskQuery.name + '.error.json')) -Encoding utf8
                $taskResults += @{query=$taskQuery.name; status='failed'; http_status=$taskCode}
                if ($taskQuery.name -eq 'connection') { throw "connection_http_$taskCode" }
                Write-Host "Skipped $($taskQuery.name): HTTP $taskCode"
                if ($InspectEvents -and $taskQuery.name.StartsWith('sample_')) { break }
                continue
            }
            $taskBody = $taskResponse.Content.ReadAsStringAsync().GetAwaiter().GetResult()
            $taskParsed = $taskBody | ConvertFrom-Json
            [IO.File]::WriteAllText((Join-Path $taskOutput ($taskQuery.name + '.json')), $taskBody)
            $taskResults += @{query=$taskQuery.name; status='ok'; rows=$taskParsed.rows}
            Write-Host "OK: $($taskQuery.name)"
            if ($taskParsed.rows -eq 0 -and $taskQuery.ContainsKey('fallback_sql')) {
                $taskQueue.Enqueue(@{name=$taskQuery.name + '_history'; sql=$taskQuery.fallback_sql})
            }
        } finally { $taskResponse.Dispose() }
    }
    $taskFailed = @($taskResults | Where-Object {$_.status -eq 'failed'}).Count
    $taskFinalStatus = if ($taskFailed) {'connected_with_query_errors'} else {'connected'}
    Save-TaskState $taskFinalStatus @{queries=$taskResults; readonly=$true}
    Write-Host "Connection succeeded. Failed queries: $taskFailed. Results saved locally; no password saved." -ForegroundColor Green
} catch {
    # Do not print exception/request details that might contain credentials.
    Save-TaskState 'failed' @{error_type=$_.Exception.GetType().Name; category=([string]$_.Exception.Message -replace '(?s)^(?!connection_http_\d+$|credentials_missing$).+$','connection_or_query_error')}
    Write-Host 'Connection failed. A credential-free status report was saved.' -ForegroundColor Yellow
} finally {
    $taskClient.DefaultRequestHeaders.Clear()
    $taskClient.Dispose()
    $taskSecret.Dispose()
    $taskCredential = $null
}
Read-Host 'Press Enter to close this window' | Out-Null
