# Interactive credentials, SELECT/DESCRIBE/SHOW only. No remote mutations.
param([string]$RunId='teacher_sparse_readonly_20261104_v1')
$ErrorActionPreference = 'Stop'
if ($RunId -notmatch '^[a-z0-9_]+$') { throw 'invalid_local_run_id' }
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$taskOutput = Join-Path $taskRoot ('data/midterm/working/'+$RunId)
[IO.Directory]::CreateDirectory($taskOutput) | Out-Null
$taskEndpoint = 'http://cc-2ze7189376o5m9759.public.clickhouse.ads.aliyuncs.com:8123/'
$Host.UI.RawUI.WindowTitle = 'OSS-Mentor - teacher database - READ ONLY'
function Save-TaskStatus([string]$State, [hashtable]$Extra = @{}) {
    $taskStatus = @{status=$State; observed_at=[DateTime]::UtcNow.ToString('o'); password_saved=$false; remote_readonly=$true}
    foreach ($taskKey in $Extra.Keys) { $taskStatus[$taskKey]=$Extra[$taskKey] }
    $taskStatus | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $taskOutput 'status.partial') -Encoding utf8
    Move-Item -LiteralPath (Join-Path $taskOutput 'status.partial') -Destination (Join-Path $taskOutput 'status.json') -Force
}
Save-TaskStatus 'waiting_for_credentials'
Write-Host 'Teacher database: SELECT / DESCRIBE / SHOW only. No remote writes.'
Write-Host 'Enter credentials locally. Password stays in process memory only.'
$taskUser=Read-Host 'Username'
$taskSecret=Read-Host 'Password' -AsSecureString
$taskHandler=[Net.Http.HttpClientHandler]::new()
$taskHandler.AllowAutoRedirect=$false
$taskClient=[Net.Http.HttpClient]::new($taskHandler)
$taskClient.Timeout=[TimeSpan]::FromSeconds(35)
try {
    if ([string]::IsNullOrWhiteSpace($taskUser) -or $taskSecret.Length -eq 0) { throw 'credentials_missing' }
    $taskCredential=[PSCredential]::new($taskUser,$taskSecret)
    $taskClient.DefaultRequestHeaders.Add('X-ClickHouse-User',$taskUser)
    $taskClient.DefaultRequestHeaders.Add('X-ClickHouse-Key',$taskCredential.GetNetworkCredential().Password)
    $taskDone=@{}; $taskRequests=0; $taskRows=0L; $taskBytes=0L
    $taskPriorQueue=Get-Content -LiteralPath (Join-Path $taskOutput 'query_queue.json') -Raw | ConvertFrom-Json
    foreach ($taskPriorQuery in $taskPriorQueue) {
        if ($taskPriorQuery.name -ne 'connection' -and (Test-Path -LiteralPath (Join-Path $taskOutput ($taskPriorQuery.name+'.json')))) {
            $taskDone[$taskPriorQuery.name]=$true
        }
    }
    $taskDeadline=[DateTime]::UtcNow.AddMinutes(25)
    Save-TaskStatus 'connected_pending_check'
    while ([DateTime]::UtcNow -lt $taskDeadline) {
        $taskQueuePath=Join-Path $taskOutput 'query_queue.json'
        $taskQueue=if(Test-Path -LiteralPath $taskQueuePath){Get-Content -LiteralPath $taskQueuePath -Raw | ConvertFrom-Json}else{@()}
        foreach ($taskQuery in $taskQueue) {
            if ($taskDone.ContainsKey($taskQuery.name)) { continue }
            if ($taskQuery.name -notmatch '^[a-z0-9_]+$') { throw 'invalid_query_name' }
            $taskSql=[string]$taskQuery.sql
            if ($taskSql -notmatch '^(SELECT|DESCRIBE|SHOW)\s' -or $taskSql.Contains(';') -or
                $taskSql -match '(?i)\b(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|OPTIMIZE|GRANT|REVOKE|OUTFILE)\b') { throw 'readonly_query_rejected' }
            if ($taskSql -match '(?i)FROM\s+opensource\.events' -and
                ($taskSql -notmatch 'toYYYYMM\(created_at\)' -or $taskSql -notmatch 'LIMIT\s+\d+' -or
                 $taskSql -notmatch 'max_rows_to_read=1000000' -or $taskSql -notmatch 'max_bytes_to_read=134217728' -or
                 $taskSql -notmatch 'max_execution_time=15')) { throw 'unbounded_events_query_rejected' }
            if ($taskRequests -ge 30 -or $taskRows+1000000 -gt 20000000 -or $taskBytes+134217728 -gt 536870912) {
                Save-TaskStatus 'paused_budget'; return
            }
            Save-TaskStatus 'querying' @{query_name=$taskQuery.name;requests=$taskRequests}
            Write-Host ('READ ONLY: '+$taskQuery.name)
            $taskRequests++
            $taskUrl=$taskEndpoint+'?query='+[Uri]::EscapeDataString($taskSql)
            $taskResponse=$taskClient.GetAsync($taskUrl).GetAwaiter().GetResult()
            if (-not $taskResponse.IsSuccessStatusCode) {
                # Do not persist response text, request headers or credentials.
                Save-TaskStatus 'query_failed' @{query_name=$taskQuery.name;http_status=[int]$taskResponse.StatusCode;requests=$taskRequests}
                Write-Host ('Query stopped: HTTP '+[int]$taskResponse.StatusCode)
                return
            }
            $taskBody=$taskResponse.Content.ReadAsStringAsync().GetAwaiter().GetResult()
            $taskPayload=$taskBody | ConvertFrom-Json
            if ($null -eq $taskPayload.statistics) { throw 'missing_read_statistics' }
            $taskRows += [long]$taskPayload.statistics.rows_read
            $taskBytes += [long]$taskPayload.statistics.bytes_read
            [IO.File]::WriteAllText((Join-Path $taskOutput ($taskQuery.name+'.json')),$taskBody,[Text.UTF8Encoding]::new($false))
            $taskDone[$taskQuery.name]=$true
            Save-TaskStatus 'waiting_for_queries' @{requests=$taskRequests;rows_read=$taskRows;bytes_read=$taskBytes;completed=@($taskDone.Keys)}
            if ($taskPayload.statistics.rows_read -ge 200000 -or $taskPayload.statistics.bytes_read -ge 16777216 -or $taskPayload.statistics.elapsed -ge 6) {
                Save-TaskStatus 'paused_heavy_query' @{query_name=$taskQuery.name;requests=$taskRequests;rows_read=$taskRows;bytes_read=$taskBytes};return
            }
            Start-Sleep -Seconds 3
        }
        Start-Sleep -Seconds 2
    }
    Save-TaskStatus 'session_expired'
} catch {
    Save-TaskStatus 'stopped' @{category=$_.Exception.GetType().Name;script_line=$_.InvocationInfo.ScriptLineNumber}
    Write-Host 'Session stopped. A credential-free status was saved.'
} finally {
    $taskClient.DefaultRequestHeaders.Clear();$taskClient.Dispose();$taskSecret.Dispose();$taskCredential=$null
}
