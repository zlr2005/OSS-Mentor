$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot
$env:PYTHONPATH = Join-Path $repoRoot "src"

python -m oss_mentor serve-api `
    --database (Join-Path $repoRoot "data\oss_mentor_web.sqlite3") `
    --host 127.0.0.1 `
    --port 8765
