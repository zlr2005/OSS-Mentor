param(
    [string]$Database = "data/oss_mentor.sqlite3"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$previousDirectory = Get-Location
$environmentNames = @(
    "PYTHONPATH", "GITHUB_OAUTH_CLIENT_ID", "GITHUB_OAUTH_CLIENT_SECRET",
    "OSS_MENTOR_BASE_URL", "OSS_MENTOR_SESSION_SECRET"
)
$previousEnvironment = @{}
foreach ($environmentName in $environmentNames) {
    $previousEnvironment[$environmentName] = [Environment]::GetEnvironmentVariable($environmentName, "Process")
}

try {
    Set-Location -LiteralPath $repoRoot
    $env:PYTHONPATH = Join-Path $repoRoot "src"
    $env:OSS_MENTOR_BASE_URL = "http://127.0.0.1:8765"

    Write-Host "GitHub OAuth App settings: https://github.com/settings/developers"
    Write-Host "Homepage URL: http://127.0.0.1:8765/"
    Write-Host "Callback URL: http://127.0.0.1:8765/api/v1/auth/github/callback"
    Write-Host "Credentials are used only by this process; no secret file is created."

    if (-not $env:GITHUB_OAUTH_CLIENT_ID) {
        $env:GITHUB_OAUTH_CLIENT_ID = (Read-Host "Paste OAuth App Client ID").Trim()
    }
    if (-not $env:GITHUB_OAUTH_CLIENT_SECRET) {
        $secureClientSecret = Read-Host "Paste Client Secret (hidden input)" -AsSecureString
        $secretPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureClientSecret)
        try {
            $env:GITHUB_OAUTH_CLIENT_SECRET = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPointer).Trim()
        }
        finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPointer)
            $secureClientSecret.Dispose()
        }
    }
    if (-not $env:GITHUB_OAUTH_CLIENT_ID -or -not $env:GITHUB_OAUTH_CLIENT_SECRET) {
        throw "Client ID and Client Secret are required. Run this script again with both values."
    }

    if (-not $env:OSS_MENTOR_SESSION_SECRET) {
        $sessionSecretBytes = New-Object byte[] 32
        $sessionRandom = [Security.Cryptography.RandomNumberGenerator]::Create()
        try {
            $sessionRandom.GetBytes($sessionSecretBytes)
            $env:OSS_MENTOR_SESSION_SECRET = [Convert]::ToBase64String($sessionSecretBytes)
        }
        finally {
            $sessionRandom.Dispose()
            [Array]::Clear($sessionSecretBytes, 0, $sessionSecretBytes.Length)
        }
    }

    if (-not (Test-Path -LiteralPath $Database -PathType Leaf)) {
        python -m oss_mentor init-demo --database $Database
        if ($LASTEXITCODE -ne 0) { throw "Database initialization failed." }
    }

    Write-Host "Open http://127.0.0.1:8765/login and sign in with GitHub."
    Write-Host "Save a profile, then consent to import public GitHub data."
    Write-Host "Keep this terminal open. Press Ctrl+C to stop the server."
    $refreshAuth = @()
    if (Get-Command gh -ErrorAction SilentlyContinue) { $refreshAuth = @('--use-gh-auth') }
    python -m oss_mentor serve-api --database $Database --host 127.0.0.1 --port 8765 @refreshAuth
    if ($LASTEXITCODE -ne 0) { throw "Server exited with code $LASTEXITCODE. Check the message above." }
}
finally {
    foreach ($environmentName in $environmentNames) {
        [Environment]::SetEnvironmentVariable($environmentName, $previousEnvironment[$environmentName], "Process")
    }
    Set-Location -LiteralPath $previousDirectory.Path
}
