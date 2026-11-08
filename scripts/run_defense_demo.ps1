param(
    [string]$PythonPath = 'D:/OSS-Mentor-assets/pr39/runtime-cpu/Scripts/python.exe',
    [int]$Port = 8766
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot
if (-not (Test-Path -LiteralPath $PythonPath)) { throw 'Python runtime missing; pass -PythonPath with your Python 3.11+ executable.' }
$demoDirectory = Join-Path $repoRoot 'data/midterm/working/defense_demo_runtime'
New-Item -ItemType Directory -Force -Path $demoDirectory | Out-Null
$demoDatabase = Join-Path $demoDirectory 'demo.sqlite3'
$env:PYTHONPATH = Join-Path $repoRoot 'src'
$env:TEMP = $demoDirectory
$env:TMP = $demoDirectory
Remove-Item Env:SSLKEYLOGFILE -ErrorAction SilentlyContinue
Write-Host 'Explicit RULE-RANKING demo. This is not the research LambdaMART or CODER result.'
Write-Host 'Expired task snapshots remain excluded. OAuth and live refresh are not required.'
& $PythonPath -m oss_mentor init-demo --database $demoDatabase
if ($LASTEXITCODE -ne 0) { throw 'Demo initialization failed.' }
Write-Host "Open http://127.0.0.1:$Port/ ; stop with Ctrl+C."
& $PythonPath -m oss_mentor serve-api --database $demoDatabase --host 127.0.0.1 --port $Port --legacy-ranking --refresh-interval-seconds 0
if ($LASTEXITCODE -ne 0) { throw 'Demo server exited with an error.' }
