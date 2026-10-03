param([switch]$RegisterDiscord)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $repoRoot
if (-not (Test-Path -LiteralPath '.tools/Scripts/python.exe')) {
  & python -m venv .tools
  if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.11+ to create the local tools environment.' }
}
& '.\.tools\Scripts\python.exe' -m pip install cmake==3.31.6 ninja==1.11.1.4
if ($LASTEXITCODE -ne 0) { throw 'Local CMake installation failed' }
& npm.cmd ci
if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
& npm.cmd run build
if ($LASTEXITCODE -ne 0) { throw 'TypeScript build failed' }
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env'; Write-Host 'Created .env. Fill in your Discord IDs and tokens before starting the bot.' }
if ($RegisterDiscord) { & npm.cmd run register; if ($LASTEXITCODE -ne 0) { throw 'Discord registration failed' } }
