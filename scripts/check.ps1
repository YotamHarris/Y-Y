param([string]$RepoRoot = (Split-Path $PSScriptRoot -Parent), [string]$ToolsRoot = $RepoRoot)
$ErrorActionPreference = 'Stop'
# Game jobs cannot edit service files. Validate the trusted service checkout separately from candidate C++.
Push-Location -LiteralPath $ToolsRoot
try {
  & npm.cmd run check
  if ($LASTEXITCODE -ne 0) { throw 'Automation checks failed' }
  & python -m unittest discover -s tests -p '*_test.py'
  if ($LASTEXITCODE -ne 0) { throw 'Build tooling checks failed' }
}
finally { Pop-Location }
& (Join-Path $PSScriptRoot 'build.ps1') -RepoRoot $RepoRoot -ToolsRoot $ToolsRoot
