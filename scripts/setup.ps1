param([switch]$Manager)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path $PSScriptRoot -Parent)
if (!(Test-Path -LiteralPath '.tools/Scripts/python.exe')) {
    & python -m venv .tools
    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.11+.' }
}
& ./.tools/Scripts/python.exe -m pip install cmake==3.31.6 ninja==1.11.1.4
if ($LASTEXITCODE -ne 0) { throw 'Local build tools installation failed.' }
& npm.cmd ci
if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }
if ($Manager) { & ./scripts/studio.ps1 -Action Setup }
