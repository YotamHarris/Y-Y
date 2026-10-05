param(
    [ValidateSet('Setup', 'Open', 'Start', 'Doctor', 'Status', 'Pause', 'Resume', 'Stop', 'Build', 'Share', 'ShareStatus', 'Unshare')]
    [string]$Action = 'Open',
    [string]$Guild,
    [string]$Channel,
    [string]$Owner,
    [string[]]$AllowedMail,
    [string]$Cloudflared,
    [switch]$Token,
    [switch]$Activate,
    [switch]$Foreground,
    [switch]$NoOpen
)
$ErrorActionPreference = 'Stop'
$studioRepo = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $studioRepo
$studioPython = Join-Path $studioRepo '.tools/studio-venv/Scripts/python.exe'
if ($Action -eq 'Setup') {
    if (!(Test-Path -LiteralPath $studioPython)) {
        & python -m venv (Join-Path $studioRepo '.tools/studio-venv')
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
    }
    & $studioPython -m pip install -r studio/manager-requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Manager dependency installation failed.' }
    $setupArgs = @('tools/mobile/setup_studio.py')
    if ($Guild) { $setupArgs += @('--guild', $Guild) }
    if ($Channel) { $setupArgs += @('--channel', $Channel) }
    if ($Owner) { $setupArgs += @('--owner', $Owner) }
    & $studioPython @setupArgs
    if ($LASTEXITCODE -ne 0) { throw 'Studio configuration failed.' }
    if ($Token) {
        & $studioPython studio/fe_manager.py setup --token
        if ($LASTEXITCODE -ne 0) { throw 'Discord token setup failed.' }
    }
    if ($Activate) {
        & $studioPython studio/fe_manager.py doctor
        if ($LASTEXITCODE -ne 0) { throw 'Resolve manager doctor findings before activation.' }
        & $studioPython studio/fe_manager.py install
        if ($LASTEXITCODE -ne 0) { throw 'Manager sign-in task installation failed.' }
        & $studioPython studio/fe_manager.py start
        if ($LASTEXITCODE -ne 0) { throw 'Manager startup failed.' }
    }
    exit 0
}
if (!(Test-Path -LiteralPath $studioPython)) { throw 'Run ./scripts/studio.ps1 -Action Setup first.' }
if ($Action -in @('Open', 'Start')) {
    & $studioPython tools/mobile/studio_share.py start
    if ($LASTEXITCODE -ne 0) { throw 'Board sharing watcher startup failed.' }
}
switch ($Action) {
    'Share' {
        $shareArgs = @('tools/mobile/studio_share.py', 'enable')
        foreach ($email in $AllowedMail) { $shareArgs += @('--allowed-mail', $email) }
        if ($Cloudflared) { $shareArgs += @('--cloudflared', $Cloudflared) }
        & $studioPython @shareArgs
    }
    'ShareStatus' { & $studioPython tools/mobile/studio_share.py status }
    'Unshare' { & $studioPython tools/mobile/studio_share.py disable }
    'Open' {
        $openArgs = @('studio/fe_board.py', 'open')
        if ($NoOpen) { $openArgs += '--no-browser' }
        & $studioPython @openArgs
    }
    'Start' {
        $startMode = if ($Foreground) { 'serve' } else { 'start' }
        & $studioPython studio/fe_manager.py $startMode
    }
    'Build' { & $studioPython tools/mobile/yy_mobile.py build }
    default { & $studioPython studio/fe_manager.py $Action.ToLowerInvariant() }
}
if ($LASTEXITCODE -ne 0) { throw "Agent Studio $Action failed ($LASTEXITCODE)." }
