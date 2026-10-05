param([string]$RepoRoot = (Split-Path $PSScriptRoot -Parent), [string]$ToolsRoot = $RepoRoot, [switch]$Smoke)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $RepoRoot
$RepoRoot = (Get-Location).Path
$ToolsRoot = (Resolve-Path -LiteralPath $ToolsRoot).Path
$cache = Join-Path $env:LOCALAPPDATA 'YYEngine\build-paths'

# Ninja runs build steps through cmd.exe, which splits commands on '&' and similar characters in a path
# such as D:\Source\Y&Y. Such paths are reached through a junction (sources) or a copy (CMake) instead.
function Test-SafePath([string]$Path) { return $Path -notmatch '[&^%!()]' }
function Get-PathKey([string]$Path) {
  $hash = [Security.Cryptography.SHA1]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($Path.ToLowerInvariant()))
  return [BitConverter]::ToString($hash).Replace('-', '').Substring(0, 12)
}
function Get-SafeSource([string]$Path) {
  if (Test-SafePath $Path) { return $Path }
  $link = Join-Path $cache ('src-' + (Get-PathKey $Path))
  if (!(Test-Path -LiteralPath $link)) {
    New-Item -ItemType Directory -Force -Path $cache | Out-Null
    New-Item -ItemType Junction -Path $link -Target $Path | Out-Null
  }
  elseif ((Get-Item -LiteralPath $link).Target -ne $Path) { throw "Build junction points elsewhere: $link" }
  return $link
}

# MSVC's libraries and the Windows SDK come from vcvars64. vswhere finds no instance from some packaged
# app processes, so the standard install folders are searched as well.
function Import-VisualStudioEnvironment {
  if ($env:VCToolsInstallDir -and $env:VSCMD_ARG_TGT_ARCH -eq 'x64' -and $env:WindowsSdkDir) { return }
  $roots = @()
  $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
  if (Test-Path -LiteralPath $vswhere) { $roots += @(& $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath) }
  foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
    $roots += @(Get-ChildItem -Path (Join-Path $base 'Microsoft Visual Studio\*\*') -Directory -ErrorAction SilentlyContinue | ForEach-Object FullName)
  }
  $vcvars = $roots | Where-Object { $_ } | ForEach-Object { Join-Path $_ 'VC\Auxiliary\Build\vcvars64.bat' } |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
  if (!$vcvars) { throw 'Install Visual Studio 2022 (or its Build Tools) with Desktop development with C++.' }
  $script = Join-Path ([IO.Path]::GetTempPath()) ('yy-vcvars-' + [Guid]::NewGuid().ToString('N') + '.cmd')
  Set-Content -LiteralPath $script -Encoding ASCII -Value "@call `"$vcvars`" >nul 2>&1`r`n@if errorlevel 1 exit /b 1`r`n@set"
  try {
    $lines = & $env:ComSpec /d /s /c "`"$script`""
    if ($LASTEXITCODE -ne 0) { throw "vcvars64 failed: $vcvars" }
  } finally { Remove-Item -LiteralPath $script -ErrorAction SilentlyContinue }
  foreach ($line in $lines) { if ($line -match '^([^=]+)=(.*)$') { Set-Item -Path "env:$($Matches[1])" -Value $Matches[2] } }
  if (!$env:VCToolsInstallDir) { throw "vcvars64 did not set up MSVC: $vcvars" }
}

Import-VisualStudioEnvironment
if (!(Get-Command clang-cl.exe -ErrorAction SilentlyContinue)) {
  $llvm = Join-Path $env:ProgramFiles 'LLVM\bin'
  if (Test-Path -LiteralPath (Join-Path $llvm 'clang-cl.exe')) { $env:PATH = "$llvm;$env:PATH" }
  else { throw 'Install LLVM for clang-cl: winget install LLVM.LLVM' }
}

$cmakePath = Join-Path $ToolsRoot '.tools/Scripts/cmake.exe'
if (-not (Test-Path -LiteralPath $cmakePath)) { $cmakePath = 'cmake' }
$ninjaPath = Join-Path (Get-SafeSource $ToolsRoot) '.tools/Scripts/ninja.exe'
if (-not (Test-Path -LiteralPath $ninjaPath)) { $ninjaPath = (Get-Command ninja.exe -ErrorAction Stop).Source }
# The pip launcher starts the real CMake, which puts its own location into every build command.
$probe = Join-Path ([IO.Path]::GetTempPath()) ('yy-cmake-' + [Guid]::NewGuid().ToString('N') + '.cmake')
Set-Content -LiteralPath $probe -Value 'execute_process(COMMAND "${CMAKE_COMMAND}" -E echo "${CMAKE_COMMAND}")'
try {
  $realCmake = (& $cmakePath -P $probe | Out-String).Trim()
  if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath $realCmake)) { throw 'Could not locate CMake installation.' }
} finally { Remove-Item -LiteralPath $probe -ErrorAction SilentlyContinue }
if (!(Test-SafePath $realCmake)) {
  $data = Split-Path (Split-Path $realCmake -Parent) -Parent
  $copy = Join-Path $cache ('cmake-' + (Get-PathKey "$data|$((Get-Item -LiteralPath $realCmake).LastWriteTimeUtc.Ticks)"))
  if (!(Test-Path -LiteralPath (Join-Path $copy 'bin\cmake.exe'))) {
    if (Test-Path -LiteralPath $copy) { throw "Incomplete CMake cache: $copy. Move it aside before retrying." }
    New-Item -ItemType Directory -Force -Path $cache | Out-Null
    Copy-Item -LiteralPath $data -Destination $copy -Recurse
  }
  $realCmake = Join-Path $copy 'bin\cmake.exe'
}
$ctestPath = Join-Path (Split-Path $realCmake -Parent) 'ctest.exe'

# Preserve the old generator's outputs when migrating to Ninja.
$buildCache = Join-Path $RepoRoot 'build/windows/CMakeCache.txt'
if ((Test-Path -LiteralPath $buildCache) -and !(Select-String -LiteralPath $buildCache -Pattern '^CMAKE_GENERATOR:INTERNAL=Ninja$' -Quiet)) {
  $oldBuild = [IO.Path]::GetFullPath((Join-Path $RepoRoot 'build/windows'))
  if (!$oldBuild.StartsWith($RepoRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Build path escapes the checkout.' }
  Move-Item -LiteralPath $oldBuild -Destination ($oldBuild + '-previous-' + [Guid]::NewGuid().ToString('N'))
}

Push-Location -LiteralPath (Get-SafeSource (Get-Location).Path)
try {
  & $realCmake --preset windows "-DCMAKE_MAKE_PROGRAM=$ninjaPath"
  if ($LASTEXITCODE -ne 0) { throw 'CMake configure failed' }
  & $realCmake --build --preset windows --parallel 4
  if ($LASTEXITCODE -ne 0) { throw 'C++ build failed' }
  & $ctestPath --preset windows
  if ($LASTEXITCODE -ne 0) { throw 'Engine/game tests failed' }
}
finally { Pop-Location }
if ($Smoke) {
  $env:YY_METRICS_PATH = Join-Path $RepoRoot 'build/windows/metrics.json'
  $env:YY_SCREENSHOT_PATH = Join-Path $RepoRoot 'build/windows/smoke.bmp'
  Push-Location -LiteralPath 'build/windows'
  try { & '.\TapDemo.exe' --smoke 120; if ($LASTEXITCODE -ne 0) { throw 'TapDemo smoke failed' } }
  finally { Pop-Location; Remove-Item Env:YY_METRICS_PATH, Env:YY_SCREENSHOT_PATH -ErrorAction SilentlyContinue }
  $binary = Get-Item -LiteralPath 'build/windows/TapDemo.exe'
  Write-Host "TapDemo executable: $($binary.Length) bytes"
}
