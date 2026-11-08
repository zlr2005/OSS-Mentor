param([switch]$PlanOnly, [switch]$Round2, [switch]$Round3, [switch]$Round4, [switch]$Round5, [switch]$Round6, [switch]$Round7, [switch]$Round8, [switch]$Round9, [switch]$Round10, [switch]$Round11, [switch]$Round12, [switch]$Round13, [switch]$Round14, [switch]$Round15, [switch]$Round16, [switch]$Round17, [switch]$Round18, [switch]$Round19, [switch]$Round20, [string]$ResumeDirectory)
# Remote access: fixed SELECT via HTTP GET only. All output is local on D drive.
$ErrorActionPreference = 'Stop'
if (@($Round2,$Round3,$Round4,$Round5,$Round6,$Round7,$Round8,$Round9,$Round10,$Round11,$Round12,$Round13,$Round14,$Round15,$Round16,$Round17,$Round18,$Round19,$Round20 | Where-Object { $_ }).Count -gt 1) { throw 'Choose only one round' }
$collectionResultLimit = if ($Round10 -or $Round11 -or $Round12 -or $Round13 -or $Round14 -or $Round15 -or $Round16 -or $Round17 -or $Round18 -or $Round19 -or $Round20) { 500 } else { 100 }
$collectionRoot = 'D:\OSS-Mentor\data\teacher_clickhouse'
$repositorySeeds = @(
    @('airbnb/visx',85095608,698437),
    @('CircuitVerse/Interactive-Book',188953831,42944450),
    @('facebook/buck2',450625045,69631),
    @('facebook/docusaurus',94911145,69631),
    @('facebook/zstd',29759715,69631),
    @('google/ml-compiler-opt',267719344,1342004),
    @('google/styleguide',35969061,1342004),
    @('google/zx',364474335,1342004),
    @('langchain-ai/deepagents',1027384981,126733545),
    @('microsoft/Agents-for-net',884411799,6154722),
    @('microsoft/SandDance',156293506,6154722),
    @('microsoft/sarif-js-sdk',349233926,6154722),
    @('microsoft/vscode-mssql',61973853,6154722),
    @('supercog-ai/agentic',926248435,173204339)
)
function New-CollectionBatch($seed, [int]$month, [int]$day = 0, [int]$endDay = 0, [int]$hour = -1, [switch]$DistinctEvents, [ValidateSet(100,500)][int]$ResultLimit = 100) {
        $repoName,$repoId,$ownerId = $seed
        $rangeFilter = ''
        $batchKey = $repoName.Replace('/','__') + '_' + $month
        $startEpoch = $null
        $endEpoch = $null
        if ($day -gt 0) {
            $localStart = [DateTimeOffset]::new([datetime]::new([int]($month / 100),($month % 100),$day),[TimeSpan]::FromHours(8))
            if ($hour -ge 0) {
                if ($hour -gt 23 -or $endDay -gt 0) { throw 'Invalid hourly range' }
                $localStart = $localStart.AddHours($hour)
            }
            $startEpoch = $localStart.ToUnixTimeSeconds()
            $endEpoch = if ($endDay -gt 0) { $localStart.AddDays($endDay - $day).ToUnixTimeSeconds() } else { $localStart.AddDays(1).ToUnixTimeSeconds() }
            if ($hour -ge 0) { $endEpoch = $localStart.AddHours(1).ToUnixTimeSeconds() }
            if ($endEpoch -le $startEpoch) { throw 'Invalid bounded time range' }
            $rangeFilter = " AND created_at>=toDateTime($startEpoch) AND created_at<toDateTime($endEpoch)"
            $batchKey += ('_day' + $day.ToString('00'))
            if ($endDay -gt 0) { $batchKey += ('_to' + $endDay.ToString('00')) }
            if ($hour -ge 0) { $batchKey += ('_hour' + $hour.ToString('00')) }
        }
        # Explicit string format and epoch avoid relying on DateTime JSON timezone serialization.
        $query = "SELECT platform, type, action, repo_id, repo_name, issue_id, issue_number, issue_title, ``issue_labels.name``, toUnixTimestamp(created_at) AS event_epoch, formatDateTime(created_at,'%Y-%m-%dT%H:%i:%SZ','UTC') AS event_time_utc FROM opensource.events WHERE toYYYYMM(created_at)=$month AND type='IssuesEvent' AND action='opened' AND org_id IN (0,$ownerId) AND repo_id=$repoId$rangeFilter LIMIT 100 SETTINGS max_threads=1, max_block_size=8192, max_execution_time=15, max_rows_to_read=1000000, max_bytes_to_read=134217728 FORMAT JSON"
        if ($ResultLimit -ne 100) { $query = $query -replace ' LIMIT 100 SETTINGS ', (' LIMIT ' + $ResultLimit + ' SETTINGS ') }
        if ($DistinctEvents) { $query = $query -replace '^SELECT ', 'SELECT DISTINCT ' }
        if ($query -notmatch '^SELECT ' -or $query.Contains(';')) { throw 'Read-only guard rejected SQL' }
        $batch = [pscustomobject]@{key=$batchKey;repository=$repoName;repo_id=[long]$repoId;month=$month;sql=$query}
        if ($ResultLimit -ne 100) { $batch | Add-Member -NotePropertyName result_limit -NotePropertyValue $ResultLimit }
        if ($day -gt 0) { $batch | Add-Member -NotePropertyName start_epoch -NotePropertyValue $startEpoch; $batch | Add-Member -NotePropertyName end_epoch -NotePropertyValue $endEpoch }
        return $batch
}
$collectionPlan = @(if ($Round5 -or $Round6 -or $Round7 -or $Round8 -or $Round9 -or $Round10 -or $Round11 -or $Round12 -or $Round13 -or $Round14 -or $Round15 -or $Round16 -or $Round17 -or $Round18 -or $Round19 -or $Round20) {
    $manifestName = if ($Round20) { 'teacher_round20_plan.json' } elseif ($Round19) { 'teacher_round19_plan.json' } elseif ($Round18) { 'teacher_round18_plan.json' } elseif ($Round17) { 'teacher_round17_plan.json' } elseif ($Round16) { 'teacher_round16_plan.json' } elseif ($Round15) { 'teacher_round15_plan.json' } elseif ($Round14) { 'teacher_round14_plan.json' } elseif ($Round13) { 'teacher_round13_plan.json' } elseif ($Round12) { 'teacher_round12_plan.json' } elseif ($Round11) { 'teacher_round11_plan.json' } elseif ($Round10) { 'teacher_round10_plan.json' } elseif ($Round9) { 'teacher_round9_plan.json' } elseif ($Round8) { 'teacher_round8_plan.json' } elseif ($Round7) { 'teacher_round7_plan.json' } elseif ($Round6) { 'teacher_round6_plan.json' } else { 'teacher_round5_plan.json' }
    $manifestPath = Join-Path $PSScriptRoot ('../../config/midterm_datasets/' + $manifestName)
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    foreach ($spec in $manifest.batches) {
        $allowedMonths = if ($Round20) { @(202609,202610) } elseif ($Round19) { @(202609,202610) } elseif ($Round18) { @(202609,202610) } elseif ($Round17) { @(202609,202610) } elseif ($Round16) { @(202609,202610) } elseif ($Round15) { @(202609,202610) } elseif ($Round14) { @(202609,202610) } elseif ($Round13) { @(202607,202608,202609) } elseif ($Round12) { @(202608,202609,202610) } elseif ($Round11) { @(202609,202610) } elseif ($Round10) { @(202610) } elseif ($Round6 -or $Round7 -or $Round8 -or $Round9) { @(202601,202602,202603,202604,202610) } else { @(202605,202606,202607,202608,202609,202610) }
        if ($spec.name -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$' -or $spec.id -le 0 -or $spec.owner -le 0 -or $spec.month -notin $allowedMonths) { throw 'Invalid manifest scope' }
        $maxDay = [DateTime]::DaysInMonth([int]($spec.month/100),$spec.month%100)
        if ($spec.day -lt 0 -or $spec.day -gt $maxDay -or $spec.end_day -lt 0 -or $spec.end_day -gt $maxDay+1 -or ($spec.end_day -gt 0 -and ($spec.day -eq 0 -or $spec.end_day -le $spec.day))) { throw 'Invalid round5 date range' }
        $queryHour = if ($Round6 -or $Round7 -or $Round8 -or $Round9 -or $Round10 -or $Round11 -or $Round12 -or $Round13 -or $Round14 -or $Round15 -or $Round16 -or $Round17 -or $Round18 -or $Round19 -or $Round20) { [int]$spec.hour } else { -1 }
        if ($queryHour -lt -1 -or $queryHour -gt 23 -or ($queryHour -ge 0 -and $spec.day -eq 0)) { throw 'Invalid manifest hour' }
        $distinctEvents = ($Round9 -or $Round10 -or $Round11 -or $Round12 -or $Round13 -or $Round14 -or $Round15 -or $Round16 -or $Round17 -or $Round18 -or $Round19 -or $Round20) -and [bool]$spec.distinct_events
        if ($Round10 -and (-not $distinctEvents -or $spec.day -lt 1 -or $spec.end_day -gt 19 -or $spec.end_day -le $spec.day -or $spec.end_day-$spec.day -gt 7 -or $queryHour -ne -1)) { throw 'Invalid bounded pilot range' }
        if (($Round11 -or $Round12 -or $Round13 -or $Round14 -or $Round15 -or $Round16 -or $Round17 -or $Round18 -or $Round19 -or $Round20) -and (-not $distinctEvents -or $spec.day -lt 1 -or $spec.end_day -le $spec.day -or $spec.end_day-$spec.day -gt 7 -or $queryHour -ne -1 -or ($spec.month -eq 202610 -and $spec.end_day -gt 19))) { throw 'Invalid bounded expansion range' }
        $batch = New-CollectionBatch @($spec.name,[long]$spec.id,[long]$spec.owner) $spec.month $spec.day $spec.end_day $queryHour -DistinctEvents:$distinctEvents -ResultLimit $collectionResultLimit
        $batch | Add-Member -NotePropertyName collection_reason -NotePropertyValue $spec.reason
        $batch | Add-Member -NotePropertyName partial_current_day -NotePropertyValue ([bool]$spec.partial_day)
        if ($spec.parent_key) { $batch | Add-Member -NotePropertyName parent_key -NotePropertyValue $spec.parent_key }
        $batch
    }
} elseif ($Round4) {
    # Discover exclusively in the teacher table, across 25 previously unqueried projects.
    $seedPath = Join-Path $PSScriptRoot '../../config/midterm_datasets/teacher_round4_repository_seeds.json'
    $newSeeds = (Get-Content -LiteralPath $seedPath -Raw | ConvertFrom-Json).repositories
    foreach ($range in @(@{month=202610;days=16},@{month=202609;days=30},@{month=202608;days=31})) {
        for ($day=1; $day -le $range.days; $day+=7) {
            $endDay = [Math]::Min($day+7,$range.days+1)
            foreach ($seed in $newSeeds) {
                if ($seed.archived -or $seed.disabled -or -not $seed.has_issues -or $seed.name -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$' -or $seed.id -le 0 -or $seed.owner -le 0) { throw 'Invalid repository seed' }
                New-CollectionBatch @($seed.name,[long]$seed.id,[long]$seed.owner) $range.month $day $endDay
            }
        }
    }
    foreach ($month in @(202607,202606,202605)) {
        foreach ($seed in $newSeeds) { New-CollectionBatch @($seed.name,[long]$seed.id,[long]$seed.owner) $month }
    }
} elseif ($Round3) {
    # Target only the known failed/saturated ranges from round2.
    New-CollectionBatch @('langchain-ai/deepagents',1027384981,126733545) 202609 19
    $repairRanges = @(
        @{seed=@('microsoft/vscode',41881900,6154722);month=202610;days=16},
        @{seed=@('pandas-dev/pandas',858127,21206976);month=202610;days=16},
        @{seed=@('microsoft/vscode',41881900,6154722);month=202609;days=30},
        @{seed=@('nodejs/node',27193779,9950313);month=202609;days=30},
        @{seed=@('pandas-dev/pandas',858127,21206976);month=202609;days=30},
        @{seed=@('langchain-ai/deepagents',1027384981,126733545);month=202606;days=30},
        @{seed=@('langchain-ai/deepagents',1027384981,126733545);month=202605;days=31}
    )
    foreach ($range in $repairRanges) {
        foreach ($day in 1..$range.days) { New-CollectionBatch $range.seed $range.month $day }
    }
} elseif ($Round2) {
    # Repair one failed range, then partition the saturated September range into days.
    New-CollectionBatch @('google/zx',364474335,1342004) 202607
    foreach ($day in 1..30) { New-CollectionBatch @('langchain-ai/deepagents',1027384981,126733545) 202609 $day }
    $seedPath = Join-Path $PSScriptRoot '../../config/midterm_datasets/teacher_round2_repository_seeds.json'
    $newSeeds = (Get-Content -LiteralPath $seedPath -Raw | ConvertFrom-Json).repositories
    foreach ($month in @(202610,202609,202608,202607)) {
        foreach ($newSeed in $newSeeds) {
            if ($newSeed.archived -or $newSeed.disabled -or -not $newSeed.has_issues) { throw 'New repository seed is unavailable' }
            if ($newSeed.name -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') { throw 'Invalid repository name' }
            New-CollectionBatch @($newSeed.name,[long]$newSeed.id,[long]$newSeed.owner) $month
        }
    }
    foreach ($month in @(202606,202605)) { foreach ($seed in $repositorySeeds) { New-CollectionBatch $seed $month } }
} else {
    foreach ($month in @(202610,202609,202608,202607)) {
        foreach ($seed in $repositorySeeds) { New-CollectionBatch $seed $month }
    }
})
if (@($collectionPlan.key | Select-Object -Unique).Count -ne $collectionPlan.Count) { throw 'Duplicate query keys' }
if ($PlanOnly) { ConvertTo-Json -InputObject $collectionPlan -Depth 6; return }
if ($ResumeDirectory) {
    $collectionDirectory = [IO.Path]::GetFullPath($ResumeDirectory)
    if ((Split-Path $collectionDirectory) -ne $collectionRoot -or (Split-Path $collectionDirectory -Leaf) -notmatch '^(expanded|round[2-9]|round10|round11|round12|round13|round14|round15|round16|round17|round18|round19|round20)_\d{8}T\d{6}Z$') { throw 'Resume directory must be a collection batch directly under the D drive collection root' }
    if (-not (Test-Path -LiteralPath $collectionDirectory -PathType Container)) { throw 'Resume directory not found' }
} else {
    $batchPrefix = if ($Round20) { 'round20_' } elseif ($Round19) { 'round19_' } elseif ($Round18) { 'round18_' } elseif ($Round17) { 'round17_' } elseif ($Round16) { 'round16_' } elseif ($Round15) { 'round15_' } elseif ($Round14) { 'round14_' } elseif ($Round13) { 'round13_' } elseif ($Round12) { 'round12_' } elseif ($Round11) { 'round11_' } elseif ($Round10) { 'round10_' } elseif ($Round9) { 'round9_' } elseif ($Round8) { 'round8_' } elseif ($Round7) { 'round7_' } elseif ($Round6) { 'round6_' } elseif ($Round5) { 'round5_' } elseif ($Round4) { 'round4_' } elseif ($Round3) { 'round3_' } elseif ($Round2) { 'round2_' } else { 'expanded_' }
    $collectionDirectory = Join-Path $collectionRoot ($batchPrefix + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'))
    if (Test-Path -LiteralPath $collectionDirectory) { throw 'Batch directory already exists' }
    [IO.Directory]::CreateDirectory($collectionDirectory) | Out-Null
}
function Save-CollectionJson($Value,[string]$Path) {
    $jsonText = ConvertTo-Json -InputObject $Value -Depth 18
    [IO.File]::WriteAllText($Path + '.partial',$jsonText,[Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath ($Path + '.partial') -Destination $Path -Force
}
function Get-CollectionFailureCategory([string]$Body) {
    # Persist only fixed labels; never save response bodies, headers or credentials.
    if ($Body -match 'AUTHENTICATION_FAILED|Authentication failed|password is incorrect') { return 'authentication_failed' }
    if ($Body -match 'ACCESS_DENIED|Not enough privileges') { return 'access_denied' }
    if ($Body -match 'TOO_MANY_ROWS|TOO_MANY_BYTES|TIMEOUT_EXCEEDED') { return 'query_protection' }
    return 'unknown'
}
$planFile = Join-Path $collectionDirectory 'query_plan.json'
if (Test-Path -LiteralPath $planFile) {
    $oldPlan = Get-Content -LiteralPath $planFile -Raw
    if (($oldPlan | ConvertFrom-Json | ConvertTo-Json -Depth 6 -Compress) -ne ($collectionPlan | ConvertTo-Json -Depth 6 -Compress)) { throw 'Resume plan differs from current plan' }
} else { Save-CollectionJson $collectionPlan $planFile }
$collectionResults = [Collections.Generic.List[object]]::new()
$collectionUnique = @{}
function Update-CollectionSummary([string]$State) {
    Save-CollectionJson @{
        status=$State;output=$collectionDirectory;observed_at=[DateTime]::UtcNow.ToString('o');readonly=$true;method='GET';
        planned_batches=$collectionPlan.Count;processed_batches=$collectionResults.Count;
        successful_batches=@($collectionResults | Where-Object status -eq 'exported').Count;
        failed_batches=@($collectionResults | Where-Object status -eq 'failed').Count;
        saturated_batches=@($collectionResults | Where-Object possibly_truncated -eq $true).Count;
        unique_issues=$collectionUnique.Count;github_verified=$false;frozen=$false;
        partial_current_day_batches=@($collectionPlan | Where-Object partial_current_day -eq $true).Count;
        result_limit=$collectionResultLimit;
        coverage=('At most ' + $collectionResultLimit + ' projected rows per planned range; saturated ranges remain incomplete');batches=@($collectionResults.ToArray())
    } (Join-Path $collectionDirectory 'status.json')
    Save-CollectionJson @($collectionUnique.Values | Sort-Object repo_name,issue_number) (Join-Path $collectionDirectory 'issues_discovered.json')
}
Update-CollectionSummary 'waiting_for_credentials'
Write-Host ('READ ONLY: ' + $collectionPlan.Count + ' sequential HTTP GET SELECT batches; round10=' + [bool]$Round10 + '; round11=' + [bool]$Round11 + '; round12=' + [bool]$Round12 + '; round13=' + [bool]$Round13 + '; round14=' + [bool]$Round14 + '; round15=' + [bool]$Round15 + '; round16=' + [bool]$Round16 + '; round17=' + [bool]$Round17 + '; round18=' + [bool]$Round18 + '; round19=' + [bool]$Round19 + '; round20=' + [bool]$Round20)
Write-Host ('At most ' + $collectionResultLimit + ' projected rows per batch. Query protection limits are unchanged.')
Write-Host ('Output: ' + $collectionDirectory)
Write-Host 'Enter credentials once. Password is hidden and never saved.'
$collectionUsername = Read-Host 'Username'
$collectionSecret = Read-Host 'Password' -AsSecureString
$collectionHandler = [Net.Http.HttpClientHandler]::new()
$collectionHandler.AllowAutoRedirect = $false
$collectionClient = [Net.Http.HttpClient]::new($collectionHandler)
$collectionClient.Timeout = [TimeSpan]::FromSeconds(35)
$collectionFailures = 0
try {
    if ([string]::IsNullOrWhiteSpace($collectionUsername) -or $collectionSecret.Length -eq 0) { throw 'Credentials missing' }
    $collectionCredential = [PSCredential]::new($collectionUsername,$collectionSecret)
    $collectionClient.DefaultRequestHeaders.Add('X-ClickHouse-User',$collectionUsername)
    $collectionClient.DefaultRequestHeaders.Add('X-ClickHouse-Key',$collectionCredential.GetNetworkCredential().Password)
    if ($Round14 -or $Round15 -or $Round16 -or $Round17 -or $Round18 -or $Round19 -or $Round20) {
        # Validate connectivity before any events scan. No account information is returned.
        $connectionCheck = @{status='checking';sql='SELECT 1 AS ok FORMAT JSON';readonly=$true;method='GET'}
        $connectionResponse = $null
        try {
            $connectionUri = 'http://cc-2ze7189376o5m9759.public.clickhouse.ads.aliyuncs.com:8123/?database=opensource&query=' + [Uri]::EscapeDataString($connectionCheck.sql)
            $connectionResponse = $collectionClient.GetAsync($connectionUri).GetAwaiter().GetResult()
            $connectionCheck.http_status = [int]$connectionResponse.StatusCode
            $connectionBody = $connectionResponse.Content.ReadAsStringAsync().GetAwaiter().GetResult()
            if (-not $connectionResponse.IsSuccessStatusCode) {
                $connectionCheck.error_category = Get-CollectionFailureCategory $connectionBody
                throw 'Connection check failed'
            }
            $connectionPayload = $connectionBody | ConvertFrom-Json -ErrorAction Stop
            if (@($connectionPayload.data).Count -ne 1 -or $connectionPayload.data[0].ok -ne 1) { throw 'Invalid connection check response' }
            $connectionCheck.status = 'passed'
            Write-Host 'Connection check passed; starting teacher events queries.'
        } catch {
            $connectionCheck.status = 'failed'
            if (-not $connectionCheck.ContainsKey('error_category')) { $connectionCheck.error_category = 'transport_or_invalid_response' }
            Write-Host ('Connection check failed: HTTP ' + $connectionCheck.http_status + '; category=' + $connectionCheck.error_category)
            throw 'Stopped before event queries; check connection_check.json'
        } finally {
            $connectionCheck.observed_at = [DateTime]::UtcNow.ToString('o')
            Save-CollectionJson $connectionCheck (Join-Path $collectionDirectory 'connection_check.json')
            if ($null -ne $connectionResponse) { $connectionResponse.Dispose() }
            $connectionBody=$null; $connectionPayload=$null
        }
    }
    foreach ($batch in $collectionPlan) {
        $resultFile = Join-Path $collectionDirectory ($batch.key + '.json')
        $batchResponse = $null
        $batchPayload = $null
        try {
            if (Test-Path -LiteralPath $resultFile) {
                $batchPayload = Get-Content -LiteralPath $resultFile -Raw | ConvertFrom-Json -ErrorAction Stop
            } else {
                $batchUri = 'http://cc-2ze7189376o5m9759.public.clickhouse.ads.aliyuncs.com:8123/?database=opensource&query=' + [Uri]::EscapeDataString($batch.sql)
                $batchResponse = $collectionClient.GetAsync($batchUri).GetAwaiter().GetResult()
                $batchText = $batchResponse.Content.ReadAsStringAsync().GetAwaiter().GetResult()
                if (-not $batchResponse.IsSuccessStatusCode) { throw ('Query HTTP ' + [int]$batchResponse.StatusCode) }
                # Reject partial HTTP 200 responses with appended errors: accept only complete JSON.
                try { $batchPayload = $batchText | ConvertFrom-Json -ErrorAction Stop } catch { throw 'Invalid or incomplete JSON response' }
            }
            if ($null -eq $batchPayload.data) { throw 'Response has no data array' }
            $batchRows = @($batchPayload.data)
            $batchResultLimit = if ($batch.PSObject.Properties.Name -contains 'result_limit') { [int]$batch.result_limit } else { 100 }
            if ($batchResultLimit -notin @(100,500) -or $batchRows.Count -gt $batchResultLimit -or [long]$batchPayload.rows -ne $batchRows.Count) { throw 'Invalid row count' }
            if (@($batchRows | Where-Object { $_.repo_id -ne $batch.repo_id -or $_.type -ne 'IssuesEvent' -or $_.action -ne 'opened' }).Count) { throw 'Rows do not match query' }
            if ($batch.PSObject.Properties.Name -contains 'start_epoch') {
                if (@($batchRows | Where-Object { [long]$_.event_epoch -lt $batch.start_epoch -or [long]$_.event_epoch -ge $batch.end_epoch }).Count) { throw 'Rows outside requested daily range' }
            }
            if (-not (Test-Path -LiteralPath $resultFile)) { Save-CollectionJson $batchPayload $resultFile }
            foreach ($row in $batchRows) {
                if ($row.platform -eq 'GitHub' -and $row.repo_name -eq $batch.repository -and $row.issue_number -gt 0) {
                    $issueKey = [string]$row.repo_id + ':' + $row.issue_number
                    if (-not $collectionUnique.ContainsKey($issueKey)) { $collectionUnique[$issueKey] = $row }
                }
            }
            $collectionResults.Add(@{key=$batch.key;status='exported';events=$batchRows.Count;possibly_truncated=($batchRows.Count -eq $batchResultLimit);statistics=$batchPayload.statistics})
            $collectionFailures = 0
            Write-Host ($batch.key + ': ' + $batchRows.Count + ' events; total unique issues: ' + $collectionUnique.Count)
        } catch {
            $collectionFailures++
            # Do not persist server response bodies or credentials in error logs.
            $safeError = 'Batch failed; response was not accepted'
            if ($_.Exception.Message -match '^Query HTTP \d{3}$') { $safeError = $_.Exception.Message }
            $failedBatch = @{key=$batch.key;status='failed';error=$safeError}
            if ($null -ne $batchResponse -and -not $batchResponse.IsSuccessStatusCode) { $failedBatch.error_category = Get-CollectionFailureCategory $batchText }
            $collectionResults.Add($failedBatch)
            Write-Host ($batch.key + ': ' + $safeError)
            if ($safeError -match 'HTTP (401|403)' -or $collectionFailures -ge 2) { throw 'Stopped after authentication failure or two consecutive batch failures; saved results can be resumed' }
        } finally { if ($null -ne $batchResponse) { $batchResponse.Dispose() }; $batchText=$null }
        Update-CollectionSummary 'running'
        Start-Sleep -Milliseconds 400
    }
    Update-CollectionSummary 'finished_unverified'
} catch {
    Update-CollectionSummary 'stopped_unverified'
    Write-Host 'Collection stopped. Check status.json; successful batches are preserved.'
} finally {
    $collectionClient.Dispose(); $collectionHandler.Dispose(); $collectionSecret.Dispose(); $collectionCredential=$null
}
Write-Host ('Unique discovered issues: ' + $collectionUnique.Count)
Write-Host ('Output: ' + $collectionDirectory)
Write-Host 'These are discovery records, not approved recommendation candidates.'
Read-Host 'Finished. Press Enter to close' | Out-Null
