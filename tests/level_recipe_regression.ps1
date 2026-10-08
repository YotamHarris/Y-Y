param(
  [Parameter(Mandatory=$true)][string]$Base,
  [string]$ToolsRoot = (Split-Path $PSScriptRoot -Parent)
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $repo
$baseHead = (& git rev-parse --verify "${Base}^{commit}" | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Base must name a pre-refactor commit.' }
$baseModel = & git show "${baseHead}:games/tapdemo/src/model.cpp"
if ($LASTEXITCODE -ne 0) { throw 'Cannot read the base model.' }

# The normal build imports MSVC and LLVM, and runs yy_tests including the bands.
. (Join-Path $repo 'scripts/build.ps1') -ToolsRoot $ToolsRoot
$scratch = Join-Path $repo 'build/windows/level-recipe-regression'
New-Item -ItemType Directory -Force -Path $scratch | Out-Null
$beforeSource = Join-Path $scratch 'base-model.cpp'
[IO.File]::WriteAllLines($beforeSource, [string[]]$baseModel)

foreach ($revision in @('before','after')) {
  $model = if ($revision -eq 'before') { $beforeSource } else { Join-Path $repo 'games/tapdemo/src/model.cpp' }
  $exe = Join-Path $scratch "$revision.exe"
  & clang-cl /nologo /std:c++20 /EHsc /O2 /Iengine/include /Igames/tapdemo/include `
    tests/level_recipe_dump.cpp $model "/Fe:$exe" "/Fo:$scratch/"
  if ($LASTEXITCODE -ne 0) { throw "Cannot compile $revision dump." }
  & $exe (Join-Path $scratch "$revision.bin")
  if ($LASTEXITCODE -ne 0) { throw "$revision dump failed." }
}

# Compare bytes, rather than native struct padding or only a hash.
@'
from pathlib import Path
import hashlib, sys
root, base = Path(sys.argv[1]), sys.argv[2]
before, after = (root / 'before.bin').read_bytes(), (root / 'after.bin').read_bytes()
assert before == after, 'Level recipe changed a field or subsequent Ghost landing'
report = (f'Base commit: {base}\n'
          f'Compared {len(before):,} bytes: identical\n'
          '10 shipped fields; 128 alternate seeds per level; advancing free-play fields;\n'
          'one collision-triggered Ghost landing and resulting field after each generation.\n'
          f'SHA256 (both): {hashlib.sha256(before).hexdigest()}\n')
(root / 'report.txt').write_text(report)
print(report, end='')
'@ | python - $scratch $baseHead
if ($LASTEXITCODE -ne 0) { throw 'Level recipe regression failed.' }
