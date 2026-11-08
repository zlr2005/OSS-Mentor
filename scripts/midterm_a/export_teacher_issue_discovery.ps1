param([switch]$PlanOnly)
# Fixed SELECT via HTTP GET. No remote changes; credentials exist only in memory.
$ErrorActionPreference = 'Stop'
$batchSql = @'
SELECT platform, type, action, repo_id, repo_name, issue_id, issue_number, issue_title, `issue_labels.name`, toTimeZone(created_at,'UTC') AS event_time_utc FROM opensource.events WHERE toYYYYMM(created_at)=202610 AND type='IssuesEvent' AND action='opened' AND org_id IN (0,69631) AND repo_id=94911145 LIMIT 100 SETTINGS max_threads=1, max_block_size=8192, max_execution_time=15, max_rows_to_read=1000000, max_bytes_to_read=134217728 FORMAT JSON
'@
if ($batchSql -notmatch '^SELECT ' -or $batchSql.Contains(';')) { throw 'Read-only guard rejected SQL' }
if ($PlanOnly) { Write-Output $batchSql; return }
$batchDirectory = Join-Path 'D:\OSS-Mentor\data\teacher_clickhouse' ('docusaurus_202610_' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'))
[IO.Directory]::CreateDirectory($batchDirectory) | Out-Null
@{readonly=$true;method='GET';sql=$batchSql;limit=100;repository='facebook/docusaurus';month=202610} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $batchDirectory 'query_plan.json') -Encoding utf8
Write-Host 'READ ONLY: SELECT via HTTP GET. Output goes to D drive only.'
Write-Host 'Password is hidden, used in memory only, and is never saved.'
$batchUsername = Read-Host 'Username'
$batchSecret = Read-Host 'Password' -AsSecureString
$batchHandler = [Net.Http.HttpClientHandler]::new()
$batchHandler.AllowAutoRedirect = $false
$batchClient = [Net.Http.HttpClient]::new($batchHandler)
$batchClient.Timeout = [TimeSpan]::FromSeconds(35)
$batchResponse = $null
try {
    if ([string]::IsNullOrWhiteSpace($batchUsername) -or $batchSecret.Length -eq 0) { throw 'Credentials missing' }
    $batchCredential = [PSCredential]::new($batchUsername,$batchSecret)
    $batchClient.DefaultRequestHeaders.Add('X-ClickHouse-User',$batchUsername)
    $batchClient.DefaultRequestHeaders.Add('X-ClickHouse-Key',$batchCredential.GetNetworkCredential().Password)
    $batchUri = 'http://cc-2ze7189376o5m9759.public.clickhouse.ads.aliyuncs.com:8123/?database=opensource&query=' + [Uri]::EscapeDataString($batchSql)
    $batchResponse = $batchClient.GetAsync($batchUri).GetAwaiter().GetResult()
    $batchText = $batchResponse.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    # Validate the whole response before accepting any rows: HTTP 200 can contain an appended server error.
    try { $batchJson = $batchText | ConvertFrom-Json -ErrorAction Stop } catch { throw 'Response was not valid JSON; no batch accepted' }
    if (-not $batchResponse.IsSuccessStatusCode -or $null -eq $batchJson.data) { throw ('Query failed; HTTP ' + [int]$batchResponse.StatusCode) }
    $batchRows = @($batchJson.data)
    if ($batchRows.Count -gt 100) { throw 'Unexpected row count' }
    if (@($batchRows | Where-Object { $_.repo_id -ne 94911145 -or $_.type -ne 'IssuesEvent' -or $_.action -ne 'opened' }).Count) { throw 'Unexpected rows' }
    $batchText | Set-Content -LiteralPath (Join-Path $batchDirectory 'events.json') -Encoding utf8
    $batchUnique = @($batchRows | Where-Object { $_.platform -eq 'GitHub' -and $_.repo_name -eq 'facebook/docusaurus' -and $_.issue_number -gt 0 } | Sort-Object repo_id,issue_number -Unique)
    ConvertTo-Json -InputObject $batchUnique -Depth 12 | Set-Content -LiteralPath (Join-Path $batchDirectory 'issues_discovered.json') -Encoding utf8
    @{status='exported_unverified';events=$batchRows.Count;unique_issues=$batchUnique.Count;limit=100;possibly_truncated=($batchRows.Count -eq 100);github_verified=$false;frozen=$false;observed_at=[DateTime]::UtcNow.ToString('o');output=$batchDirectory} |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $batchDirectory 'status.json') -Encoding utf8
    Write-Host ('Exported events: ' + $batchRows.Count + '; unique issues: ' + $batchUnique.Count)
    Write-Host ('Output: ' + $batchDirectory)
} catch {
    @{status='failed';error=$_.Exception.Message;observed_at=[DateTime]::UtcNow.ToString('o')} |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $batchDirectory 'status.json') -Encoding utf8
    Write-Host ('Failed: ' + $_.Exception.Message)
} finally {
    if ($null -ne $batchResponse) { $batchResponse.Dispose() }
    $batchClient.Dispose()
    $batchHandler.Dispose()
    $batchSecret.Dispose()
    $batchCredential = $null
    $batchText = $null
}
Read-Host 'Finished. Press Enter to close' | Out-Null
