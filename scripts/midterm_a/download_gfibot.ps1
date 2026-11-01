param([ValidateSet(8388608)][int]$ChunkBytes = 8388608)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$taskRoot = Join-Path $PSScriptRoot '../../data/midterm/sources/gfibot/6837711'
[IO.Directory]::CreateDirectory($taskRoot) | Out-Null
$taskMetadata = Join-Path $taskRoot 'zenodo_record.json'
if (-not (Test-Path -LiteralPath $taskMetadata)) {
    Invoke-WebRequest -Uri 'https://zenodo.org/api/records/6837711' -TimeoutSec 60 -OutFile $taskMetadata
}
$taskTail = Join-Path $taskRoot 'zip_tail.bin'
if (-not (Test-Path -LiteralPath $taskTail)) {
    $taskResponse = Invoke-WebRequest -Uri 'https://zenodo.org/records/6837711/files/dump.zip?download=1' -Headers @{Range='bytes=2463928930-2463994465'} -TimeoutSec 60 -OutFile "$taskTail.partial" -PassThru
    if ([int]$taskResponse.StatusCode -ne 206 -or [string]$taskResponse.Headers['Content-Range'] -ne 'bytes 2463928930-2463994465/2463994466') { throw 'Invalid ZIP tail response' }
    Move-Item -LiteralPath "$taskTail.partial" -Destination $taskTail
}
& {
    # Offsets come from the ZIP central directory, independently checked by the importer.
    $taskStart = 2314539508L
    $taskEnd = 2463993494L
    $taskOffsets = for ($taskOffset = $taskStart; $taskOffset -le $taskEnd; $taskOffset += $ChunkBytes) { $taskOffset }
    $taskOffsets | ForEach-Object -Parallel {
        $ErrorActionPreference = 'Stop'
        $ProgressPreference = 'SilentlyContinue'
        $taskOffset = $_
        $taskEnd = 2463993494L
        $ChunkBytes = $using:ChunkBytes
        $taskRoot = $using:taskRoot
        $taskLast = [Math]::Min($taskOffset + $ChunkBytes - 1, $taskEnd)
        $taskName = "range_${taskOffset}_${taskLast}.bin"
        $taskPath = Join-Path $taskRoot $taskName
        $taskExpected = $taskLast - $taskOffset + 1
        if ((Test-Path -LiteralPath $taskPath) -and (Get-Item -LiteralPath $taskPath).Length -eq $taskExpected) { return }
        $taskTemp = "$taskPath.partial"
        try {
            $taskResponse = Invoke-WebRequest -Uri 'https://zenodo.org/records/6837711/files/dump.zip?download=1' -Headers @{Range="bytes=$taskOffset-$taskLast"} -TimeoutSec 120 -OutFile $taskTemp -PassThru
            if ([int]$taskResponse.StatusCode -ne 206) { throw "Expected HTTP 206, got $([int]$taskResponse.StatusCode)" }
            if ([string]$taskResponse.Headers['Content-Range'] -ne "bytes $taskOffset-$taskLast/2463994466") { throw 'Content-Range mismatch' }
            if ((Get-Item -LiteralPath $taskTemp).Length -ne $taskExpected) { throw 'Incomplete range' }
            Move-Item -LiteralPath $taskTemp -Destination $taskPath -Force
            [pscustomobject]@{start=$taskOffset; end=$taskLast; bytes=$taskExpected; sha256=(Get-FileHash -LiteralPath $taskPath -Algorithm SHA256).Hash.ToLowerInvariant(); status=206; observed_at=[DateTime]::UtcNow.ToString('o')} |
                ConvertTo-Json -Compress | Set-Content -LiteralPath "$taskPath.json" -Encoding utf8
            Write-Output "Saved $taskName ($taskExpected bytes)"
        } finally {
            if (Test-Path -LiteralPath $taskTemp) { Remove-Item -LiteralPath $taskTemp }
        }
    } -ThrottleLimit 4
}
