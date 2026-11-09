[CmdletBinding()]
param(
    [int]$Port = 8765,
    [string]$Database = "",
    [switch]$NormalBrowser,
    [switch]$Reconfigure,
    [switch]$NoBrowser,
    [switch]$UseTerminalConfig
)

$ErrorActionPreference = "Stop"

if (
    $env:OS -eq "Windows_NT" -and
    -not $UseTerminalConfig -and
    $env:OSS_MENTOR_GUI_RELAUNCHED -ne "1" -and
    [System.Threading.Thread]::CurrentThread.GetApartmentState() -ne "STA"
) {
    $env:OSS_MENTOR_GUI_RELAUNCHED = "1"
    $relaunchArgs = @(
        "-NoProfile",
        "-STA",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        $PSCommandPath,
        "-Port",
        [string]$Port
    )
    if ($Database) {
        $relaunchArgs += @("-Database", $Database)
    }
    if ($NormalBrowser) {
        $relaunchArgs += "-NormalBrowser"
    }
    if ($Reconfigure) {
        $relaunchArgs += "-Reconfigure"
    }
    if ($NoBrowser) {
        $relaunchArgs += "-NoBrowser"
    }
    & powershell.exe @relaunchArgs
    exit $LASTEXITCODE
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $repoRoot

$env:PYTHONPATH = Join-Path $repoRoot "src"
$configPath = Join-Path $repoRoot ".env.local"
$databasePath = if ($Database) {
    [System.IO.Path]::GetFullPath((Join-Path $repoRoot $Database))
} else {
    Join-Path $repoRoot "data\oss_mentor.sqlite3"
}

function Read-DotEnv {
    param([string]$Path)

    $values = @{}
    if (-not (Test-Path -LiteralPath $Path)) {
        return $values
    }
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }
        if ($trimmed -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            $values[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'")
        }
    }
    return $values
}

function Read-SecretText {
    param([string]$Prompt)

    $secure = Read-Host -Prompt $Prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function Show-OAuthConfigDialog {
    param(
        [int]$TargetPort,
        [string]$InitialClientId = ""
    )

    if ($env:OS -ne "Windows_NT") {
        throw "The graphical OAuth dialog requires Windows."
    }

    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    [System.Windows.Forms.Application]::EnableVisualStyles()

    $homepageUrl = "http://127.0.0.1:$TargetPort"
    $callbackUrl = "$homepageUrl/api/v1/auth/github/callback"
    $script:oauthDialogResult = $null

    $form = New-Object System.Windows.Forms.Form
    $form.Text = "OSS-Mentor · GitHub OAuth 配置"
    $form.ClientSize = New-Object System.Drawing.Size(700, 720)
    $form.StartPosition = "CenterScreen"
    $form.FormBorderStyle = "FixedDialog"
    $form.MaximizeBox = $false
    $form.MinimizeBox = $false
    $form.BackColor = [System.Drawing.Color]::FromArgb(245, 245, 247)
    $form.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 10)

    $card = New-Object System.Windows.Forms.Panel
    $card.Location = New-Object System.Drawing.Point(20, 20)
    $card.Size = New-Object System.Drawing.Size(660, 680)
    $card.BackColor = [System.Drawing.Color]::White
    $form.Controls.Add($card)

    $brand = New-Object System.Windows.Forms.Label
    $brand.Location = New-Object System.Drawing.Point(24, 22)
    $brand.Size = New-Object System.Drawing.Size(42, 42)
    $brand.Text = "OM"
    $brand.TextAlign = "MiddleCenter"
    $brand.BackColor = [System.Drawing.Color]::FromArgb(217, 45, 32)
    $brand.ForeColor = [System.Drawing.Color]::White
    $brand.Font = New-Object System.Drawing.Font("Segoe UI", 11, [System.Drawing.FontStyle]::Bold)
    $card.Controls.Add($brand)

    $title = New-Object System.Windows.Forms.Label
    $title.Location = New-Object System.Drawing.Point(80, 20)
    $title.Size = New-Object System.Drawing.Size(550, 28)
    $title.Text = "连接 GitHub OAuth"
    $title.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 16, [System.Drawing.FontStyle]::Bold)
    $card.Controls.Add($title)

    $subtitle = New-Object System.Windows.Forms.Label
    $subtitle.Location = New-Object System.Drawing.Point(82, 49)
    $subtitle.Size = New-Object System.Drawing.Size(550, 24)
    $subtitle.Text = "配置一次后，后续启动会直接打开图形登录页。"
    $subtitle.ForeColor = [System.Drawing.Color]::FromArgb(95, 99, 104)
    $card.Controls.Add($subtitle)

    $steps = New-Object System.Windows.Forms.GroupBox
    $steps.Location = New-Object System.Drawing.Point(24, 84)
    $steps.Size = New-Object System.Drawing.Size(612, 150)
    $steps.Text = "操作步骤"
    $steps.BackColor = [System.Drawing.Color]::FromArgb(248, 248, 250)
    $steps.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 9, [System.Drawing.FontStyle]::Bold)
    $card.Controls.Add($steps)

    $stepsText = New-Object System.Windows.Forms.Label
    $stepsText.Location = New-Object System.Drawing.Point(14, 24)
    $stepsText.Size = New-Object System.Drawing.Size(580, 112)
    $stepsText.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 9)
    $stepsText.Text = @"
1. 点击下方“打开 GitHub OAuth Apps”，选择 OAuth Apps → New OAuth App。
2. 在 GitHub 表单中填写下面两个 URL，可直接点击“复制”。
3. 点击 Register application，复制页面顶部的 Client ID。
4. 点击 Generate a new client secret，复制 Secret，再粘贴到下方。
"@
    $steps.Controls.Add($stepsText)

    $homepageLabel = New-Object System.Windows.Forms.Label
    $homepageLabel.Location = New-Object System.Drawing.Point(24, 244)
    $homepageLabel.Size = New-Object System.Drawing.Size(600, 22)
    $homepageLabel.Text = "Homepage URL"
    $homepageLabel.ForeColor = [System.Drawing.Color]::FromArgb(95, 99, 104)
    $card.Controls.Add($homepageLabel)

    $homepageBox = New-Object System.Windows.Forms.TextBox
    $homepageBox.Location = New-Object System.Drawing.Point(24, 267)
    $homepageBox.Size = New-Object System.Drawing.Size(510, 30)
    $homepageBox.Text = $homepageUrl
    $homepageBox.ReadOnly = $true
    $homepageBox.BackColor = [System.Drawing.Color]::FromArgb(248, 248, 250)
    $card.Controls.Add($homepageBox)

    $copyHomeButton = New-Object System.Windows.Forms.Button
    $copyHomeButton.Location = New-Object System.Drawing.Point(542, 266)
    $copyHomeButton.Size = New-Object System.Drawing.Size(82, 32)
    $copyHomeButton.Text = "复制"
    $copyHomeButton.FlatStyle = "Flat"
    $card.Controls.Add($copyHomeButton)

    $callbackLabel = New-Object System.Windows.Forms.Label
    $callbackLabel.Location = New-Object System.Drawing.Point(24, 304)
    $callbackLabel.Size = New-Object System.Drawing.Size(600, 22)
    $callbackLabel.Text = "Authorization callback URL"
    $callbackLabel.ForeColor = [System.Drawing.Color]::FromArgb(95, 99, 104)
    $card.Controls.Add($callbackLabel)

    $callbackBox = New-Object System.Windows.Forms.TextBox
    $callbackBox.Location = New-Object System.Drawing.Point(24, 327)
    $callbackBox.Size = New-Object System.Drawing.Size(510, 30)
    $callbackBox.Text = $callbackUrl
    $callbackBox.ReadOnly = $true
    $callbackBox.BackColor = [System.Drawing.Color]::FromArgb(248, 248, 250)
    $card.Controls.Add($callbackBox)

    $copyCallbackButton = New-Object System.Windows.Forms.Button
    $copyCallbackButton.Location = New-Object System.Drawing.Point(542, 326)
    $copyCallbackButton.Size = New-Object System.Drawing.Size(82, 32)
    $copyCallbackButton.Text = "复制"
    $copyCallbackButton.FlatStyle = "Flat"
    $card.Controls.Add($copyCallbackButton)

    $openGithubButton = New-Object System.Windows.Forms.Button
    $openGithubButton.Location = New-Object System.Drawing.Point(24, 370)
    $openGithubButton.Size = New-Object System.Drawing.Size(330, 34)
    $openGithubButton.Text = "打开 GitHub OAuth Apps（自动复制 Callback）"
    $openGithubButton.FlatStyle = "Flat"
    $openGithubButton.ForeColor = [System.Drawing.Color]::FromArgb(217, 45, 32)
    $card.Controls.Add($openGithubButton)

    $clientIdLabel = New-Object System.Windows.Forms.Label
    $clientIdLabel.Location = New-Object System.Drawing.Point(24, 414)
    $clientIdLabel.Size = New-Object System.Drawing.Size(600, 22)
    $clientIdLabel.Text = "GitHub OAuth Client ID"
    $clientIdLabel.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 10, [System.Drawing.FontStyle]::Bold)
    $card.Controls.Add($clientIdLabel)

    $clientIdBox = New-Object System.Windows.Forms.TextBox
    $clientIdBox.Location = New-Object System.Drawing.Point(24, 437)
    $clientIdBox.Size = New-Object System.Drawing.Size(600, 30)
    $clientIdBox.Text = $InitialClientId
    $card.Controls.Add($clientIdBox)

    $clientSecretLabel = New-Object System.Windows.Forms.Label
    $clientSecretLabel.Location = New-Object System.Drawing.Point(24, 478)
    $clientSecretLabel.Size = New-Object System.Drawing.Size(600, 22)
    $clientSecretLabel.Text = "GitHub OAuth Client Secret"
    $clientSecretLabel.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 10, [System.Drawing.FontStyle]::Bold)
    $card.Controls.Add($clientSecretLabel)

    $secretBox = New-Object System.Windows.Forms.TextBox
    $secretBox.Location = New-Object System.Drawing.Point(24, 501)
    $secretBox.Size = New-Object System.Drawing.Size(600, 30)
    $secretBox.UseSystemPasswordChar = $true
    $card.Controls.Add($secretBox)

    $showSecret = New-Object System.Windows.Forms.CheckBox
    $showSecret.Location = New-Object System.Drawing.Point(24, 540)
    $showSecret.Size = New-Object System.Drawing.Size(180, 25)
    $showSecret.Text = "显示 Client Secret"
    $card.Controls.Add($showSecret)

    $privacy = New-Object System.Windows.Forms.Label
    $privacy.Location = New-Object System.Drawing.Point(24, 574)
    $privacy.Size = New-Object System.Drawing.Size(600, 34)
    $privacy.Text = "配置只保存在本机 .env.local，不会提交到 Git，也不会在日志中输出 Secret。"
    $privacy.ForeColor = [System.Drawing.Color]::FromArgb(95, 99, 104)
    $card.Controls.Add($privacy)

    $errorLabel = New-Object System.Windows.Forms.Label
    $errorLabel.Location = New-Object System.Drawing.Point(24, 607)
    $errorLabel.Size = New-Object System.Drawing.Size(600, 28)
    $errorLabel.ForeColor = [System.Drawing.Color]::FromArgb(180, 35, 24)
    $errorLabel.Visible = $false
    $card.Controls.Add($errorLabel)

    $cancelButton = New-Object System.Windows.Forms.Button
    $cancelButton.Location = New-Object System.Drawing.Point(398, 630)
    $cancelButton.Size = New-Object System.Drawing.Size(112, 40)
    $cancelButton.Text = "取消"
    $cancelButton.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
    $card.Controls.Add($cancelButton)

    $saveButton = New-Object System.Windows.Forms.Button
    $saveButton.Location = New-Object System.Drawing.Point(520, 630)
    $saveButton.Size = New-Object System.Drawing.Size(104, 40)
    $saveButton.Text = "保存并启动"
    $saveButton.FlatStyle = "Flat"
    $saveButton.FlatAppearance.BorderSize = 0
    $saveButton.BackColor = [System.Drawing.Color]::FromArgb(217, 45, 32)
    $saveButton.ForeColor = [System.Drawing.Color]::White
    $saveButton.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 10, [System.Drawing.FontStyle]::Bold)
    $card.Controls.Add($saveButton)

    $form.AcceptButton = $saveButton
    $form.CancelButton = $cancelButton

    $copyHomeButton.Add_Click({
        [System.Windows.Forms.Clipboard]::SetText($homepageBox.Text)
        $copyHomeButton.Text = "已复制"
    })
    $copyCallbackButton.Add_Click({
        [System.Windows.Forms.Clipboard]::SetText($callbackBox.Text)
        $copyCallbackButton.Text = "已复制"
    })
    $openGithubButton.Add_Click({
        [System.Windows.Forms.Clipboard]::SetText($callbackBox.Text)
        $copyCallbackButton.Text = "已复制"
        Start-Process "https://github.com/settings/developers"
    })
    $showSecret.Add_CheckedChanged({
        $secretBox.UseSystemPasswordChar = -not $showSecret.Checked
    })
    $saveButton.Add_Click({
        $clientIdValue = $clientIdBox.Text.Trim()
        $clientSecretValue = $secretBox.Text.Trim()
        if (-not $clientIdValue -or -not $clientSecretValue) {
            $errorLabel.Text = "请填写 Client ID 和 Client Secret。"
            $errorLabel.Visible = $true
            return
        }
        $script:oauthDialogResult = [pscustomobject]@{
            ClientId = $clientIdValue
            ClientSecret = $clientSecretValue
        }
        $form.DialogResult = [System.Windows.Forms.DialogResult]::OK
        $form.Close()
    })
    $form.Add_Shown({
        $clientIdBox.Focus()
    })

    try {
        $result = $form.ShowDialog()
        if ($result -ne [System.Windows.Forms.DialogResult]::OK) {
            return $null
        }
        return $script:oauthDialogResult
    } finally {
        $form.Dispose()
    }
}

function New-SessionSecret {
    $bytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    } finally {
        $generator.Dispose()
    }
    return [Convert]::ToBase64String($bytes)
}

function Save-OAuthConfig {
    param(
        [string]$Path,
        [string]$ClientId,
        [string]$ClientSecret,
        [string]$SessionSecret
    )

    $contents = @(
        "# Generated by start-web.ps1. Do not commit this file."
        "GITHUB_OAUTH_CLIENT_ID=$ClientId"
        "GITHUB_OAUTH_CLIENT_SECRET=$ClientSecret"
        "OSS_MENTOR_SESSION_SECRET=$SessionSecret"
    )
    [System.IO.File]::WriteAllLines(
        $Path,
        $contents,
        (New-Object System.Text.UTF8Encoding($false))
    )
}

function Test-PortListening {
    param([int]$TargetPort)

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect("127.0.0.1", $TargetPort, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(300)) {
            return $false
        }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Open-AppWindow {
    param(
        [int]$TargetPort,
        [bool]$UseNormalBrowser
    )

    $url = "http://127.0.0.1:$TargetPort/login?return_to=%2Fprofile"
    $browser = $null
    $privateArgument = $null

    if (-not $UseNormalBrowser) {
        $edgeCandidates = @(
            (Join-Path $env:ProgramFiles "Microsoft\Edge\Application\msedge.exe")
        )
        if (${env:ProgramFiles(x86)}) {
            $edgeCandidates += Join-Path ${env:ProgramFiles(x86)} "Microsoft\Edge\Application\msedge.exe"
        }
        $edge = $edgeCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
        if ($edge) {
            $browser = $edge
            $privateArgument = "--inprivate"
        } else {
            $chromeCandidates = @(
                (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe")
            )
            if (${env:ProgramFiles(x86)}) {
                $chromeCandidates += Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"
            }
            $chrome = $chromeCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
            if ($chrome) {
                $browser = $chrome
                $privateArgument = "--incognito"
            }
        }
    }

    if ($browser) {
        Start-Process -FilePath $browser -ArgumentList @($privateArgument, $url)
    } else {
        Start-Process $url
    }
}

$config = Read-DotEnv -Path $configPath
if ($Reconfigure -and (Test-Path -LiteralPath $configPath)) {
    Remove-Item -LiteralPath $configPath -Force
    $config = @{}
}

$clientId = if ($Reconfigure) { "" } elseif ($env:GITHUB_OAUTH_CLIENT_ID) {
    $env:GITHUB_OAUTH_CLIENT_ID
} else {
    [string]$config["GITHUB_OAUTH_CLIENT_ID"]
}
$clientSecret = if ($Reconfigure) { "" } elseif ($env:GITHUB_OAUTH_CLIENT_SECRET) {
    $env:GITHUB_OAUTH_CLIENT_SECRET
} else {
    [string]$config["GITHUB_OAUTH_CLIENT_SECRET"]
}
$sessionSecret = if ($Reconfigure) { "" } elseif ($env:OSS_MENTOR_SESSION_SECRET) {
    $env:OSS_MENTOR_SESSION_SECRET
} else {
    [string]$config["OSS_MENTOR_SESSION_SECRET"]
}

if (-not $clientId -or -not $clientSecret) {
    if (-not $UseTerminalConfig -and $env:OS -eq "Windows_NT") {
        $dialogValues = Show-OAuthConfigDialog -TargetPort $Port -InitialClientId $clientId
        if ($null -eq $dialogValues) {
            Write-Host "OAuth configuration was cancelled."
            exit 1
        }
        $clientId = $dialogValues.ClientId
        $clientSecret = $dialogValues.ClientSecret
    } else {
        Write-Host ""
        Write-Host "GitHub OAuth App configuration"
        Write-Host "Homepage URL: http://127.0.0.1:$Port"
        Write-Host "Authorization callback URL: http://127.0.0.1:$Port/api/v1/auth/github/callback"
        Write-Host ""
        Write-Host "Create or update a GitHub OAuth App with the exact URLs above."
        $clientId = Read-Host "GitHub OAuth Client ID"
        $clientSecret = Read-SecretText "GitHub OAuth Client Secret"
    }
}

if (-not $sessionSecret) {
    $sessionSecret = New-SessionSecret
}

if (-not $clientId -or -not $clientSecret) {
    throw "GitHub OAuth Client ID and Client Secret are required."
}

$env:GITHUB_OAUTH_CLIENT_ID = $clientId
$env:GITHUB_OAUTH_CLIENT_SECRET = $clientSecret
$env:OSS_MENTOR_SESSION_SECRET = $sessionSecret
$env:OSS_MENTOR_BASE_URL = "http://127.0.0.1:$Port"
$certifiPath = (& python -c "import certifi; print(certifi.where())" 2>$null)
if ($LASTEXITCODE -eq 0 -and $certifiPath -and (Test-Path -LiteralPath $certifiPath)) {
    $env:SSL_CERT_FILE = $certifiPath
    $env:REQUESTS_CA_BUNDLE = $certifiPath
}

Save-OAuthConfig `
    -Path $configPath `
    -ClientId $clientId `
    -ClientSecret $clientSecret `
    -SessionSecret $sessionSecret

if (-not (Test-Path -LiteralPath $databasePath)) {
    Write-Host "Database not found. Creating a demo database..."
    & python -m oss_mentor init-demo --database $databasePath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to initialize the demo database."
    }
}

$loginUrl = "http://127.0.0.1:$Port/login?return_to=%2Fprofile"
if (Test-PortListening -TargetPort $Port) {
    Write-Host "Port $Port is already serving something."
    Write-Host "Open: $loginUrl"
    if (-not $NoBrowser) {
        Open-AppWindow -TargetPort $Port -UseNormalBrowser:$NormalBrowser
    }
    exit 0
}

Write-Host ""
Write-Host "Starting OSS-Mentor..."
Write-Host "App:      http://127.0.0.1:$Port/"
Write-Host "Login:    $loginUrl"
Write-Host "Callback: http://127.0.0.1:$Port/api/v1/auth/github/callback"
Write-Host "Database: $databasePath"
if (-not $NormalBrowser) {
    Write-Host "Browser:  private window (allows a different GitHub account)"
} else {
    Write-Host "Browser:  normal window"
}
Write-Host "Press Ctrl+C in this window to stop the server."
Write-Host ""

$pythonExe = (Get-Command python -ErrorAction Stop).Source
$serverArguments = @(
    "-m",
    "oss_mentor",
    "serve-api",
    "--database",
    $databasePath,
    "--host",
    "127.0.0.1",
    "--port",
    [string]$Port
)
$serverProcess = Start-Process `
    -FilePath $pythonExe `
    -ArgumentList $serverArguments `
    -WorkingDirectory $repoRoot `
    -NoNewWindow `
    -PassThru

$serverReady = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    if ($serverProcess.HasExited) {
        throw "OSS-Mentor exited before the web server became ready."
    }
    if (Test-PortListening -TargetPort $Port) {
        $serverReady = $true
        break
    }
    Start-Sleep -Milliseconds 500
}

if (-not $serverReady) {
    if (-not $serverProcess.HasExited) {
        Stop-Process -Id $serverProcess.Id -Force
    }
    throw "OSS-Mentor did not become ready on port $Port."
}

if (-not $NoBrowser) {
    Open-AppWindow -TargetPort $Port -UseNormalBrowser:$NormalBrowser
}

try {
    Wait-Process -Id $serverProcess.Id
} finally {
    if (-not $serverProcess.HasExited) {
        Stop-Process -Id $serverProcess.Id -Force
    }
}
