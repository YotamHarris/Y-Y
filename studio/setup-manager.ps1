[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$Guild,
    [string]$Channel,
    [string]$Owner,
    [switch]$Token,
    [switch]$Activate
)
$ErrorActionPreference = 'Stop'
if ($Token) {
    Write-Host 'Wait for the hidden Discord bot token prompt, then paste the token and press Enter.'
}
# The project's data folder (%LOCALAPPDATA%\<studio.toml name>), from studio_config beside this script.
$dataDir = python -c "import sys; sys.path.insert(0, sys.argv[1]); import studio_config; print(studio_config.data_dir())" $PSScriptRoot
if ($LASTEXITCODE -ne 0 -or -not $dataDir) { throw 'Could not read the project name from studio.toml' }
$managerRoot = Join-Path $dataDir.Trim() 'board/manager'
$managerPython = Join-Path $managerRoot 'venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $managerPython)) {
    python -m venv (Join-Path $managerRoot 'venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create manager virtual environment' }
}
& $managerPython -m pip install -r (Join-Path $PSScriptRoot 'manager-requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install manager dependencies' }
$managerArgs = @((Join-Path $PSScriptRoot 'fe_manager.py'), 'setup')
if ($Guild) { $managerArgs += @('--guild', $Guild) }
if ($Channel) { $managerArgs += @('--channel', $Channel) }
if ($Owner) { $managerArgs += @('--owner', $Owner) }
if ($Token) { $managerArgs += '--token' }
& $managerPython @managerArgs
if ($LASTEXITCODE -ne 0) { throw 'Manager configuration failed' }
if ($Activate) {
    & $managerPython (Join-Path $PSScriptRoot 'fe_manager.py') doctor
    if ($LASTEXITCODE -ne 0) { throw 'Resolve doctor findings before activation' }
    & $managerPython (Join-Path $PSScriptRoot 'fe_manager.py') install
    if ($LASTEXITCODE -ne 0) { throw 'Could not install the sign-in task' }
    & $managerPython (Join-Path $PSScriptRoot 'fe_manager.py') start
    if ($LASTEXITCODE -ne 0) { throw 'Could not start the manager' }
}
