param([string]$RepoRoot = (Split-Path $PSScriptRoot -Parent), [string]$ToolsRoot = $RepoRoot, [switch]$Smoke)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $RepoRoot
$cmakePath = Join-Path $ToolsRoot '.tools/Scripts/cmake.exe'
if (-not (Test-Path -LiteralPath $cmakePath)) { $cmakePath = 'cmake' }
& $cmakePath --preset windows
if ($LASTEXITCODE -ne 0) { throw 'CMake configure failed' }
& $cmakePath --build --preset windows --parallel 4
if ($LASTEXITCODE -ne 0) { throw 'C++ build failed' }
$ctestPath = Join-Path (Split-Path $cmakePath -Parent) 'ctest.exe'
if ($cmakePath -eq 'cmake') { $ctestPath = 'ctest' }
& $ctestPath --preset windows
if ($LASTEXITCODE -ne 0) { throw 'Engine/game tests failed' }
if ($Smoke) {
  $env:YY_METRICS_PATH = Join-Path $RepoRoot 'build/windows/metrics.json'
  $env:YY_SCREENSHOT_PATH = Join-Path $RepoRoot 'build/windows/smoke.bmp'
  try { & '.\build\windows\Release\TapDemo.exe' --smoke 120; if ($LASTEXITCODE -ne 0) { throw 'TapDemo smoke failed' } }
  finally { Remove-Item Env:YY_METRICS_PATH, Env:YY_SCREENSHOT_PATH -ErrorAction SilentlyContinue }
  $binary = Get-Item -LiteralPath 'build/windows/Release/TapDemo.exe'
  Write-Host "TapDemo executable: $($binary.Length) bytes"
}
