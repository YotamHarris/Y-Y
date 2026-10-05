$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $repoRoot 'scripts/configure.ps1') -LibraryOnly
function Assert-Setup { param([bool]$Condition, [string]$Message) if (-not $Condition) { throw $Message } }
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('yy-configure-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
  $path = Join-Path $temporary '.env'
  [IO.File]::WriteAllText($path, "# Keep this comment`nYY_DATA_DIR='D:\Agent Data'`nDISCORD_TOKEN=old`nDISCORD_TOKEN=duplicate`nOPENAI_API_KEY=remove-me`n")
  Write-EnvSettings $path ([ordered]@{DISCORD_TOKEN = 'fake#token with spaces'; YY_CLAUDE_PATH = "D:\Friend's tools\claude.exe"}) @('OPENAI_API_KEY')
  $saved = Read-EnvSettings $path
  Assert-Setup ($saved.DISCORD_TOKEN -eq 'fake#token with spaces') 'Secret did not round-trip.'
  Assert-Setup ($saved.YY_DATA_DIR -eq 'D:\Agent Data') 'Unrelated setting changed.'
  Assert-Setup (-not $saved.ContainsKey('OPENAI_API_KEY')) 'Subscription API key was retained.'
  $raw = [IO.File]::ReadAllText($path)
  Assert-Setup ($raw.StartsWith('# Keep this comment')) 'Comment was removed.'
  Assert-Setup (([regex]::Matches($raw, '(?m)^DISCORD_TOKEN=')).Count -eq 1) 'Duplicate credential was retained.'
  $probe = & node "--env-file=$path" -e 'console.log(JSON.stringify({token:process.env.DISCORD_TOKEN,path:process.env.YY_CLAUDE_PATH}))'
  if ($LASTEXITCODE -ne 0) { throw 'Node env-file probe failed.' }
  $parsed = $probe | ConvertFrom-Json
  Assert-Setup ($parsed.token -eq $saved.DISCORD_TOKEN -and $parsed.path -eq $saved.YY_CLAUDE_PATH) 'Node parsed .env differently.'

  # Exercise the entire local wizard in an isolated fixture, using fake tokens only.
  New-Item -ItemType Directory -Path (Join-Path $temporary 'scripts') | Out-Null
  Copy-Item -LiteralPath (Join-Path $repoRoot 'scripts/configure.ps1') -Destination (Join-Path $temporary 'scripts/configure.ps1')
  $global:YYSetupTestAnswers = [Collections.Generic.Queue[string]]::new()
  foreach ($answer in @('', 'fake-github-secret', 'n')) { $global:YYSetupTestAnswers.Enqueue($answer) }
  function Read-Host {
    param([string]$Prompt, [switch]$AsSecureString)
    if (-not $global:YYSetupTestAnswers.Count) { throw "Unexpected wizard prompt: $Prompt" }
    $answer = $global:YYSetupTestAnswers.Dequeue()
    if ($AsSecureString) { $secure = [Security.SecureString]::new(); foreach ($c in $answer.ToCharArray()) { $secure.AppendChar($c) }; return $secure }
    return $answer
  }
  $global:YYSetupTestOutput = ''
  function Write-Host { param([object]$Object, [object]$ForegroundColor) $global:YYSetupTestOutput += [string]$Object + "`n" }
  & (Join-Path $temporary 'scripts/configure.ps1')
  $wizard = Read-EnvSettings $path
  Assert-Setup ($global:YYSetupTestAnswers.Count -eq 0) 'Wizard missed expected prompts.'
  Assert-Setup ($wizard.DISCORD_TOKEN -eq 'fake#token with spaces') 'Blank input did not retain secret.'
  Assert-Setup ($wizard.GITHUB_REPOSITORY -eq 'YotamHarris/Y-Y') 'Repository was not saved.'
  Assert-Setup ($wizard.GITHUB_TOKEN -eq 'fake-github-secret') 'Coordinator token was not saved.'
  Assert-Setup ($global:YYSetupTestOutput -notmatch 'fake-github-secret|fake-claude-secret|fake#token') 'Wizard printed a secret.'

  # Existing environments must remain intact; secrets must be sent on stdin.
  $global:YYSetupTestGhCalls = [Collections.Generic.List[object]]::new()
  function Invoke-RestMethod { param([string]$Uri, [hashtable]$Headers) return @{name = 'testflight-tapdemo'} }
  function gh {
    $global:YYSetupTestGhCalls.Add(@{arguments = @($args); stdin = @($input) -join "`n"})
    $global:LASTEXITCODE = 0
  }
  Set-TestFlightSettings 'owner/repo' 'tapdemo' ([ordered]@{PROFILE_NAME = 'Profile with spaces'}) ([ordered]@{ASC_KEY_CONTENT = "fake-key`nline-two"; P12_PASSWORD = 'fake-password'})
  Assert-Setup ($global:YYSetupTestGhCalls.Count -eq 3) 'Existing environment was changed or uploads were omitted.'
  Assert-Setup ($global:YYSetupTestGhCalls[1].stdin -eq "fake-key`nline-two") 'Multiline key did not reach stdin intact.'
  Assert-Setup ((($global:YYSetupTestGhCalls | ForEach-Object { $_.arguments -join ' ' }) -join ' ') -notmatch 'fake-key|fake-password') 'Secrets reached command arguments.'

  # Run the optional Apple section with fake files and mocked remote writes.
  New-Item -ItemType Directory -Path (Join-Path $temporary 'config') | Out-Null
  Copy-Item -LiteralPath (Join-Path $repoRoot 'config/games.json') -Destination (Join-Path $temporary 'config/games.json')
  $certificate = Join-Path $temporary 'fake.p12'; $profile = Join-Path $temporary 'fake.mobileprovision'; $key = Join-Path $temporary 'fake.p8'
  [IO.File]::WriteAllText($certificate, 'fake-certificate'); [IO.File]::WriteAllText($profile, 'fake-profile')
  [IO.File]::WriteAllText($key, "-----BEGIN PRIVATE KEY-----`nfake-key`n-----END PRIVATE KEY-----")
  $global:YYSetupTestGhCalls.Clear()
  foreach ($answer in @('', '', 'y', '', 'ABCDE12345', '', 'Dummy profile', $certificate, $profile, $key, 'fake-password', 'ABCDE12345', '11111111-2222-3333-4444-555555555555', 'fake-setup-token')) { $global:YYSetupTestAnswers.Enqueue($answer) }
  $oldGhToken = $env:GH_TOKEN
  try {
    $env:GH_TOKEN = 'fake-original-token'
    & (Join-Path $temporary 'scripts/configure.ps1')
    Assert-Setup ($env:GH_TOKEN -eq 'fake-original-token') 'Setup token environment was not restored.'
  } finally { $env:GH_TOKEN = $oldGhToken }
  Assert-Setup ($global:YYSetupTestAnswers.Count -eq 0 -and $global:YYSetupTestGhCalls.Count -eq 9) 'Apple wizard omitted settings.'
  Assert-Setup ($global:YYSetupTestGhCalls[3].stdin -eq [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes('fake-certificate'))) 'Certificate was not base64 encoded.'
  Assert-Setup ($global:YYSetupTestOutput -notmatch 'fake-password|fake-key|fake-setup-token') 'Apple wizard printed secrets.'
  Assert-Setup ([IO.File]::ReadAllText($path) -notmatch 'fake-password|fake-key|fake-setup-token|ASC_KEY') 'Apple credentials were stored locally.'
  function Invoke-RestMethod {
    $missing = [Exception]::new('Not found')
    $missing | Add-Member -NotePropertyName Response -NotePropertyValue ([pscustomobject]@{StatusCode = 404})
    throw $missing
  }
  $global:YYSetupTestGhCalls.Clear()
  Set-TestFlightSettings 'owner/repo' 'tapdemo' ([ordered]@{}) ([ordered]@{})
  Assert-Setup ($global:YYSetupTestGhCalls.Count -eq 1 -and $global:YYSetupTestGhCalls[0].arguments -contains 'PUT') 'Missing environment was not created.'
  function gh { $global:LASTEXITCODE = 1; 'fake-password error' }
  $failure = ''
  try { Invoke-SetupGh @('secret', 'set', 'P12_PASSWORD') 'fake-password' } catch { $failure = $_.Exception.Message }
  Assert-Setup ($failure -match 'GitHub setup failed' -and $failure -notmatch 'fake-password') 'Upload error leaked secret or was ignored.'

  # Permission failure can be repaired without repeating Apple credential prompts.
  $global:YYSetupTestProbeCount = 0; $global:YYSetupTestGhCalls.Clear()
  function Invoke-RestMethod {
    $global:YYSetupTestProbeCount++
    if ($global:YYSetupTestProbeCount -gt 1) { return @{name = 'testflight-tapdemo'} }
    $missing = [Exception]::new('Not found')
    $missing | Add-Member -NotePropertyName Response -NotePropertyValue ([pscustomobject]@{StatusCode = 404})
    throw $missing
  }
  function gh {
    $global:YYSetupTestGhCalls.Add(@{arguments = @($args); stdin = @($input) -join "`n"; token = $env:GH_TOKEN})
    if ($args -contains 'PUT') { $global:LASTEXITCODE = 1; return 'fake-password failure (HTTP 403)' }
    $global:LASTEXITCODE = 0
  }
  foreach ($answer in @('', '', 'y', '', 'ABCDE12345', '', 'Dummy profile', $certificate, $profile, $key, 'fake-password', 'ABCDE12345', '11111111-2222-3333-4444-555555555555', 'fake-setup-token', 'y', 'fake-replacement-token')) { $global:YYSetupTestAnswers.Enqueue($answer) }
  & (Join-Path $temporary 'scripts/configure.ps1')
  Assert-Setup ($global:YYSetupTestAnswers.Count -eq 0 -and $global:YYSetupTestGhCalls.Count -eq 10) 'Permission repair restarted or omitted credential prompts.'
  Assert-Setup ($global:YYSetupTestGhCalls[1].token -eq 'fake-replacement-token') 'Retry ignored replacement token.'
  Assert-Setup ($global:YYSetupTestOutput -match 'creating environment testflight-tapdemo \(HTTP 403\)' -and $global:YYSetupTestOutput -notmatch 'fake-password|fake-replacement-token') 'Error details were missing or leaked a secret.'

  # A successful native process writing to stderr must not count as a failed upload.
  function Resolve-SetupGh { return (Get-Command node).Source }
  $nativeOutput = @(Invoke-SetupGh -Arguments @('-e', "process.stderr.write('fake-password');process.exit(0)"))
  Assert-Setup ($nativeOutput.Count -eq 0) 'Native stderr escaped the setup wrapper.'
  Write-Output 'Credential setup tests passed (local wizard, Node parsing, preservation, upload transport, redaction).'
} finally {
  $resolved = [IO.Path]::GetFullPath($temporary)
  $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
  if ($resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and [IO.Path]::GetFileName($resolved) -like 'yy-configure-*') {
    Remove-Item -LiteralPath $resolved -Recurse -Force
  }
}
