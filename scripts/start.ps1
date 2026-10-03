$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path $PSScriptRoot -Parent)
& npm.cmd run build
if ($LASTEXITCODE -ne 0) { throw 'TypeScript build failed' }
& npm.cmd run bot
exit $LASTEXITCODE
