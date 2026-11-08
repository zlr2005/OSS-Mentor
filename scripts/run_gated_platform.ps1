param(
    [string]$PythonPath = 'python',
    [string]$Database = 'data/oss_mentor.sqlite3',
    [string]$Bundle = 'data/gated-v1/bundle',
    [string]$Cache = 'data/midterm/platform/gated_live',
    [int]$Port = 8767,
    [switch]$AssumeInventoryAvailable,
    [string]$WorkerConfig
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$previousDirectory = Get-Location
$previousPythonPath = $env:PYTHONPATH
try {
    Set-Location -LiteralPath $repoRoot
    $env:PYTHONPATH = Join-Path $repoRoot 'src'
    if ($WorkerConfig) {
        $workerSettings = Get-Content -LiteralPath $WorkerConfig -Raw | ConvertFrom-Json
        if (-not $PSBoundParameters.ContainsKey('Bundle')) { $Bundle = $workerSettings.bundle }
        if (-not $PSBoundParameters.ContainsKey('Cache')) { $Cache = $workerSettings.cache }
    }
    if (-not $WorkerConfig -and -not (Test-Path -LiteralPath $Cache -PathType Container)) {
        throw 'Current feature cache missing. Export real inputs with scripts/export_gated_inputs.py, then publish trusted features with scripts/prepare_gated_context.py.'
    }
    $inventoryOptions = @()
    if ($AssumeInventoryAvailable) { $inventoryOptions = @('--assume-inventory-available') }
    if ($WorkerConfig) { $inventoryOptions += @('--gated-worker-config', $WorkerConfig) }
    & $PythonPath -m oss_mentor serve-api --database $Database --host 127.0.0.1 --port $Port `
        --gated-bundle $Bundle --gated-cache $Cache --refresh-interval-seconds 0 @inventoryOptions
    if ($LASTEXITCODE -ne 0) { throw "Gated platform exited with code $LASTEXITCODE" }
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    Set-Location -LiteralPath $previousDirectory.Path
}
