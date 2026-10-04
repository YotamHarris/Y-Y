param(
    [ValidateSet('Setup', 'Start', 'Stop', 'Status', 'LoginCode', 'Configure', 'Connect')]
    [string]$Action = 'Status',
    [string[]]$AllowedEmails,
    [string]$Email,
    [switch]$Public,
    [switch]$LockWorkspaceCreation,
    [string]$Distro = 'Ubuntu'
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$stateDir = Join-Path $repoRoot '.yy/multica'
$binDir = Join-Path $stateDir 'bin'
$envFile = Join-Path $stateDir '.env'
$cliPath = Join-Path $binDir 'multica.exe'
$tunnelPath = Join-Path $binDir 'cloudflared.exe'
$tunnelState = Join-Path $stateDir 'tunnel.json'
$wslState = Join-Path $stateDir 'wsl.json'
New-Item -ItemType Directory -Path $binDir -Force | Out-Null

function Invoke-Linux([string]$Script) {
    # Encode the shell program so Windows/WSL cannot reinterpret spaces or & in paths.
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Script))
    & wsl -d $Distro -u root -- bash -lc "echo $encoded | base64 -d | bash"
    if ($LASTEXITCODE -ne 0) { throw "WSL command failed ($LASTEXITCODE)." }
}
function Linux-Quote([string]$Value) { "'" + $Value.Replace("'", "'\''") + "'" }
function Linux-Path([string]$Path) {
    if ($Path -notmatch '^([A-Za-z]):[\\/](.*)$') { throw 'A local drive path is required.' }
    '/mnt/' + $Matches[1].ToLower() + '/' + $Matches[2].Replace('\', '/')
}
$composeFile = Linux-Quote (Linux-Path (Join-Path $repoRoot 'tools/multica/compose.yml'))
$linuxEnv = Linux-Quote (Linux-Path $envFile)
function Invoke-Compose([string]$Arguments) {
    Invoke-Linux "docker compose --env-file $linuxEnv -f $composeFile $Arguments"
}
function Invoke-Multica([string[]]$CliArgs) {
    & $cliPath --profile yyengine @CliArgs
    if ($LASTEXITCODE -ne 0) { throw 'Multica command failed.' }
}
function Read-Multica([string[]]$CliArgs) {
    $output = Invoke-Multica $CliArgs
    ($output -join "`n") | ConvertFrom-Json
}
function Read-Settings {
    $settings = [ordered]@{}
    if (Test-Path -LiteralPath $envFile) {
        foreach ($line in Get-Content -LiteralPath $envFile) {
            if ($line -match '^([A-Z_]+)=(.*)$') { $settings[$Matches[1]] = $Matches[2] }
        }
    }
    $settings
}
function Save-Settings($Settings) {
    [IO.File]::WriteAllLines($envFile, @($Settings.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }))
}
function Random-Hex {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    -join ($bytes | ForEach-Object { $_.ToString('x2') })
}
function Start-WslAnchor {
    if (Test-Path -LiteralPath $wslState) {
        $saved = Get-Content -LiteralPath $wslState -Raw | ConvertFrom-Json
        $process = Get-Process -Id $saved.pid -ErrorAction SilentlyContinue
        if ($process -and $process.ProcessName -eq 'wsl' -and $process.StartTime.ToUniversalTime().Ticks -eq $saved.started_ticks) { return }
    }
    # systemd services alone do not keep a WSL distribution alive.
    $process = Start-Process -FilePath 'wsl.exe' -ArgumentList @('-d', $Distro, '--exec', '/bin/sleep', 'infinity') -WindowStyle Hidden -PassThru
    @{pid=$process.Id; started_ticks=$process.StartTime.ToUniversalTime().Ticks} | ConvertTo-Json | Set-Content -LiteralPath $wslState
}
function Stop-WslAnchor {
    if (Test-Path -LiteralPath $wslState) {
        $saved = Get-Content -LiteralPath $wslState -Raw | ConvertFrom-Json
        $process = Get-Process -Id $saved.pid -ErrorAction SilentlyContinue
        if ($process -and $process.ProcessName -eq 'wsl' -and $process.StartTime.ToUniversalTime().Ticks -eq $saved.started_ticks) { Stop-Process -Id $process.Id }
        Remove-Item -LiteralPath $wslState
    }
}
function Stop-Tunnel {
    if (Test-Path -LiteralPath $tunnelState) {
        $saved = Get-Content -LiteralPath $tunnelState -Raw | ConvertFrom-Json
        $process = Get-Process -Id $saved.pid -ErrorAction SilentlyContinue
        if ($process -and $process.Path -eq $tunnelPath -and $process.StartTime.ToUniversalTime().Ticks -eq $saved.started_ticks) {
            Stop-Process -Id $process.Id
        }
        Remove-Item -LiteralPath $tunnelState
    }
}
function Start-Tunnel {
    if (!(Test-Path -LiteralPath $tunnelPath)) { throw 'Run -Action Setup first.' }
    if (Test-Path -LiteralPath $tunnelState) {
        $saved = Get-Content -LiteralPath $tunnelState -Raw | ConvertFrom-Json
        $process = Get-Process -Id $saved.pid -ErrorAction SilentlyContinue
        if ($process -and $process.Path -eq $tunnelPath -and $process.StartTime.ToUniversalTime().Ticks -eq $saved.started_ticks) { return $saved.url }
        Remove-Item -LiteralPath $tunnelState
    }
    $logPath = Join-Path $stateDir 'tunnel.stderr.log'
    $process = Start-Process -FilePath $tunnelPath -ArgumentList @('tunnel', '--url', 'http://127.0.0.1:3072', '--no-autoupdate') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $stateDir 'tunnel.stdout.log') -RedirectStandardError $logPath
    try {
        for ($attempt = 0; $attempt -lt 45; $attempt++) {
            Start-Sleep -Seconds 1
            $process.Refresh()
            if ($process.HasExited) { throw "Tunnel exited. See $logPath" }
            $log = Get-Content -LiteralPath $logPath -Raw -ErrorAction SilentlyContinue
            if ($log -match 'https://[a-z0-9-]+\.trycloudflare\.com') {
                $url = $Matches[0]
                @{pid=$process.Id; started_ticks=$process.StartTime.ToUniversalTime().Ticks; url=$url} | ConvertTo-Json | Set-Content -LiteralPath $tunnelState
                return $url
            }
        }
        throw "Tunnel did not provide a URL. See $logPath"
    } catch { if (!$process.HasExited) { Stop-Process -Id $process.Id }; throw }
}

switch ($Action) {
    'Setup' {
        Invoke-Linux 'docker info >/dev/null && docker compose version'
        if (!(Test-Path -LiteralPath $cliPath)) {
            $releaseUrl = 'https://github.com/multica-ai/multica/releases/download/v0.6.1'
            $archiveName = 'multica-cli-0.6.1-windows-amd64.zip'
            $archive = Join-Path $binDir $archiveName
            Invoke-WebRequest "$releaseUrl/$archiveName" -OutFile $archive -UseBasicParsing
            $checksums = (Invoke-WebRequest "$releaseUrl/checksums.txt" -UseBasicParsing).Content
            if ($checksums -is [byte[]]) { $checksums = [Text.Encoding]::UTF8.GetString($checksums) }
            $expected = (($checksums -split "`n" | Where-Object { $_.Trim().EndsWith($archiveName) }) -split '\s+')[0]
            if (!$expected -or (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLower() -ne $expected) { throw 'Multica checksum mismatch.' }
            Expand-Archive -LiteralPath $archive -DestinationPath $binDir -Force
        }
        if (!(Test-Path -LiteralPath $tunnelPath)) {
            $release = Invoke-RestMethod 'https://api.github.com/repos/cloudflare/cloudflared/releases/tags/2026.9.3'
            $asset = $release.assets | Where-Object name -eq 'cloudflared-windows-amd64.exe'
            Invoke-WebRequest $asset.browser_download_url -OutFile $tunnelPath -UseBasicParsing
            if (!$asset.digest -or "sha256:$((Get-FileHash -LiteralPath $tunnelPath).Hash.ToLower())" -ne $asset.digest) { throw 'Cloudflared checksum mismatch.' }
        }
        if (!(Test-Path -LiteralPath $envFile)) {
            Save-Settings ([ordered]@{POSTGRES_PASSWORD=(Random-Hex); JWT_SECRET=(Random-Hex); MULTICA_APP_URL='http://localhost:3072'; ALLOWED_EMAILS=''; DISABLE_WORKSPACE_CREATION='false'})
        }
        & $cliPath --profile yyengine config set server_url http://localhost:3072
        if ($LASTEXITCODE -ne 0) { throw 'Multica configuration failed.' }
        Invoke-Multica @('config', 'set', 'app_url', 'http://localhost:3072')
        Invoke-Compose 'pull --quiet'
        Write-Host 'Setup complete. Use -Action Configure -AllowedEmails you@example.com,friend@example.com, then -Action Start -Public.'
    }
    'Configure' {
        if (!(Test-Path -LiteralPath $envFile)) { throw 'Run -Action Setup first.' }
        $settings = Read-Settings
        if ($AllowedEmails) {
            foreach ($address in $AllowedEmails) {
                if ($address -notmatch '^[^\s,@=]+@[^\s,@=]+\.[^\s,@=]+$') { throw "Invalid email: $address" }
            }
            $settings['ALLOWED_EMAILS'] = ($AllowedEmails | ForEach-Object { $_.ToLowerInvariant() }) -join ','
        }
        if ($LockWorkspaceCreation) { $settings['DISABLE_WORKSPACE_CREATION'] = 'true' }
        Save-Settings $settings
        Invoke-Compose 'up -d'
    }
    'Start' {
        if (!(Test-Path -LiteralPath $envFile)) { throw 'Run -Action Setup first.' }
        Start-WslAnchor
        Invoke-Linux 'systemctl start docker'
        $settings = Read-Settings
        $settings['MULTICA_APP_URL'] = if ($Public) { Start-Tunnel } else { Stop-Tunnel; 'http://localhost:3072' }
        Save-Settings $settings
        Invoke-Compose 'up -d'
        & $cliPath --profile yyengine config set server_url http://localhost:3072
        if ($LASTEXITCODE -ne 0) { throw 'Multica configuration failed.' }
        Invoke-Multica @('config', 'set', 'app_url', 'http://localhost:3072')
        $ready = $false
        for ($attempt=0; $attempt -lt 45; $attempt++) {
            try { $health=Invoke-RestMethod 'http://localhost:3072/healthz'; if ($health.status -eq 'ok') { $ready=$true; break } } catch {}
            Start-Sleep -Seconds 1
        }
        if (!$ready) { throw 'Backend not ready. Use -Action Status and inspect container logs.' }
        $settings['MULTICA_APP_URL'] | Set-Content -LiteralPath (Join-Path $stateDir 'public-url.txt')
        Write-Host "Multica: $($settings['MULTICA_APP_URL'])"
        Write-Host 'Local: http://localhost:3072. Keep this PC awake. The public URL changes when a new tunnel starts.'
    }
    'Stop' {
        Stop-Tunnel
        if (Test-Path -LiteralPath $cliPath) { & $cliPath --profile yyengine daemon stop }
        Invoke-Compose 'stop'
        Stop-WslAnchor
    }
    'Status' {
        Invoke-Compose 'ps'
        if (Test-Path -LiteralPath $tunnelState) {
            $saved = Get-Content -LiteralPath $tunnelState -Raw | ConvertFrom-Json
            $process = Get-Process -Id $saved.pid -ErrorAction SilentlyContinue
            if ($process -and $process.Path -eq $tunnelPath) { Write-Host "Public: $($saved.url)" } else { Write-Host 'Tunnel is stopped.' }
        }
        $botLock = Join-Path $repoRoot '.yy/service.lock'
        if (Test-Path -LiteralPath $botLock) {
            $botPid = [int](Get-Content -LiteralPath $botLock -Raw).Trim()
            if (Get-Process -Id $botPid -ErrorAction SilentlyContinue) { Write-Host "YYEngine coordinator PID: $botPid" }
        }
    }
    'LoginCode' {
        if (!$Email) { throw '-Email is required.' }
        # Logs contain one-time login codes; show only the requested account's latest code locally.
        $logs = Invoke-Compose 'logs --no-color --tail 500 backend'
        $line = $logs | Where-Object { $_ -match ('Verification code for ' + [regex]::Escape($Email) + ':') } | Select-Object -Last 1
        if ($line -match 'Verification code for .*?:\s*(\d{6})') { Write-Host $Matches[1] } else { Write-Host 'No code found. Request one on the sign-in page first.' }
    }
    'Connect' {
        # Authentication uses the supported Multica CLI login. The existing
        # YYEngine coordinator owns this board; do not start a second executor.
        Set-Location -LiteralPath $repoRoot
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Bot build failed.' }
        & node --env-file=.env tools/bot/dist/src/connect-board.js
        if ($LASTEXITCODE -ne 0) { throw 'Board connection failed; complete Multica CLI login first.' }
        $settings = Read-Settings
        $settings['DISABLE_WORKSPACE_CREATION'] = 'true'
        Save-Settings $settings
        Invoke-Compose 'up -d'
        Write-Host 'TapDemo connected to the existing bot. Use TapDemoBoard.cmd to start the board and manager.'
    }
}
