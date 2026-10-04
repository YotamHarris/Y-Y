param([switch]$NoOpen)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $repoRoot
& (Join-Path $PSScriptRoot 'multica.ps1') -Action Start -Public
& npm.cmd run build
if ($LASTEXITCODE -ne 0) { throw 'Bot build failed.' }
$boardFile = Join-Path $repoRoot '.yy/multica/board.json'
if (!(Test-Path -LiteralPath $boardFile)) {
    & node --env-file=.env tools/bot/dist/src/connect-board.js
    if ($LASTEXITCODE -ne 0) { throw 'Board connection failed. Complete the Multica CLI login first.' }
}
$lock = Join-Path $repoRoot '.yy/service.lock'
$alive = $false
if (Test-Path -LiteralPath $lock) {
    $botPid = [int](Get-Content -LiteralPath $lock -Raw).Trim()
    $botProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$botPid"
    if ($botProcess) {
        if ($botProcess.Name -ne 'node.exe' -or $botProcess.CommandLine -notmatch 'tools[/\\]bot[/\\]dist[/\\]src[/\\]index\.js') {
            throw 'The service lock points to an unexpected process. Inspect it before restarting.'
        }
        $alive = $true
    }
}
if (!$alive) {
    $nodePath = (Get-Command node.exe -ErrorAction Stop).Source
    $process = Start-Process -FilePath $nodePath -ArgumentList @('--env-file=.env', 'tools/bot/dist/src/index.js') -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $repoRoot '.yy/bot.stdout.log') -RedirectStandardError (Join-Path $repoRoot '.yy/bot.stderr.log')
    Start-Sleep -Seconds 3
    if ($process.HasExited) { throw 'The bot stopped during startup. See .yy/bot.stderr.log.' }
}
$board = Get-Content -LiteralPath $boardFile -Raw | ConvertFrom-Json
$origin = (Get-Content -LiteralPath (Join-Path $repoRoot '.yy/multica/public-url.txt') -Raw).Trim().TrimEnd('/')
$url = "$origin/yyengine/projects/$($board.projectId)"
Write-Host "TapDemo board: $url"
Write-Host 'The Discord bot and board manager run in the background. Keep this PC awake.'
if (!$NoOpen) { Start-Process $url }
