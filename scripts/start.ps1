param([switch]$Foreground)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'studio.ps1') -Action Start -Foreground:$Foreground
