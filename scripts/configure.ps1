param([switch]$LibraryOnly)
$ErrorActionPreference = 'Stop'

function Read-Setting {
  param([string]$Label, [string]$Default = '', [switch]$Secret, [string]$Pattern = '.')
  while ($true) {
    $hint = if ($Default) { if ($Secret) { ' [Enter keeps saved value]' } else { " [$Default]" } } else { '' }
    if ($Secret) {
      $secure = Read-Host "$Label$hint" -AsSecureString
      $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
      try { $value = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
      finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer); $secure.Dispose() }
    } else { $value = Read-Host "$Label$hint" }
    if (-not $value) { $value = $Default }
    if ($value -notmatch '[\r\n]' -and $value -match $Pattern) { return $value }
    Write-Host 'Missing or invalid value. Please try again.' -ForegroundColor Yellow
  }
}

function Read-EnvSettings {
  param([string]$Path)
  $values = @{}
  if (Test-Path -LiteralPath $Path) {
    foreach ($line in [IO.File]::ReadAllLines($Path)) {
      if ($line -match '^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*)$') {
        $name = $Matches[1]; $value = $Matches[2].Trim()
        if ($value.Length -ge 2 -and (($value.StartsWith("'") -and $value.EndsWith("'")) -or ($value.StartsWith('"') -and $value.EndsWith('"')))) {
          $value = $value.Substring(1, $value.Length - 2)
        } else { $value = ($value -split '#', 2)[0].Trim() }
        $values[$name] = $value
      }
    }
  }
  return $values
}

function Write-EnvSettings {
  param([string]$Path, [System.Collections.IDictionary]$Values, [string[]]$Remove = @())
  $lines = [Collections.Generic.List[string]]::new()
  if (Test-Path -LiteralPath $Path) {
    foreach ($line in [IO.File]::ReadAllLines($Path)) {
      if ($line -match '^\s*([A-Z][A-Z0-9_]*)\s*=') {
        if ($Values.Contains($Matches[1]) -or $Remove -contains $Matches[1]) { continue }
      }
      $lines.Add($line)
    }
  }
  foreach ($name in $Values.Keys) {
    $value = [string]$Values[$name]
    if ($value -match '[\r\n]') { throw "Cannot save multiline setting $name." }
    $quote = if (-not $value.Contains("'")) { "'" } elseif (-not $value.Contains('"')) { '"' } else { throw "Setting $name contains both quote types; use a path without quotes." }
    $lines.Add("$name=$quote$value$quote")
  }
  [IO.File]::WriteAllLines($Path, $lines, [Text.UTF8Encoding]::new($false))
}

function Resolve-SetupGh {
  param([switch]$Required)
  $command = Get-Command gh -ErrorAction SilentlyContinue
  if ($command) {
    if ($command.CommandType -eq 'Application') { return $command.Source }
    return $command.Name
  }
  # An installer updates future terminals' PATH, but this launcher may still have the old PATH.
  foreach ($directory in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, (Join-Path $env:LOCALAPPDATA 'Programs'))) {
    if (-not $directory) { continue }
    $candidate = Join-Path $directory 'GitHub CLI/gh.exe'
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
  }
  if ($Required) { throw 'Apple setup needs GitHub CLI: winget install --id GitHub.cli --exact --source winget. Then rerun Configure.cmd. Your local .env is already saved.' }
}

function Invoke-SetupGh {
  param([string[]]$Arguments, [AllowNull()][string]$InputText = $null, [string]$Label = 'GitHub command', [string]$PermissionHint = 'Check token permissions and connectivity.')
  # Credentials arrive through GH_TOKEN and stdin, never command arguments or logs.
  $previousErrorAction = $ErrorActionPreference
  $captured = @(); $commandExit = -1
  try {
    $githubCommand = Resolve-SetupGh -Required
    # Windows PowerShell treats native stderr as errors even when the CLI exits successfully.
    $ErrorActionPreference = 'Continue'
    if ($null -eq $InputText) { $captured = @(& $githubCommand @Arguments 2>&1) }
    else { $captured = @($InputText | & $githubCommand @Arguments 2>&1) }
    $commandExit = $LASTEXITCODE
  } catch { $captured += $_.Exception.Message }
  finally { $ErrorActionPreference = $previousErrorAction }
  if ($commandExit -ne 0) {
    # Extract only a numeric HTTP status; never echo arbitrary CLI output containing credentials.
    $detail = "exit $commandExit"
    if (($captured -join "`n") -match 'HTTP\s+(\d{3})') { $detail = "HTTP $($Matches[1])" }
    throw "GitHub setup failed while $Label ($detail). $PermissionHint Local .env was saved; earlier GitHub updates may have completed."
  }
}

function Set-TestFlightSettings {
  param([string]$Repository, [string]$Game, [System.Collections.IDictionary]$Variables, [System.Collections.IDictionary]$Secrets)
  $environment = "testflight-$Game"
  # Preserve existing environment rules. Create only if absent.
  $headers = @{ Authorization = "Bearer $env:GH_TOKEN"; Accept = 'application/vnd.github+json'; 'X-GitHub-Api-Version' = '2022-11-28' }
  try { $null = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repository/environments/$environment" -Headers $headers }
  catch {
    if ($null -ne $_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 404) {
      Invoke-SetupGh -Arguments @('api', '--method', 'PUT', "repos/$Repository/environments/$environment", '--input', '-') -InputText '{}' -Label "creating environment $environment" -PermissionHint "Create $environment in https://github.com/$Repository/settings/environments, or give the setup token Administration read/write. Uploading its settings also needs Environments read/write."
    } else { throw 'Cannot read the GitHub environment. Check the setup token has repository access and Actions read permission.' }
  }
  foreach ($name in $Variables.Keys) {
    Invoke-SetupGh -Arguments @('variable', 'set', $name, '--repo', $Repository, '--env', $environment) -InputText $Variables[$name] -Label "setting variable $name in $environment" -PermissionHint 'The setup token needs Environments read/write for this repository.'
    Write-Host "Set $environment variable $name."
  }
  foreach ($name in $Secrets.Keys) {
    Invoke-SetupGh -Arguments @('secret', 'set', $name, '--repo', $Repository, '--env', $environment) -InputText $Secrets[$name] -Label "setting secret $name in $environment" -PermissionHint 'The setup token needs Environments read/write for this repository.'
    Write-Host "Set $environment secret $name."
  }
}

if ($LibraryOnly) { return }
$repoRoot = Split-Path $PSScriptRoot -Parent
$envPath = Join-Path $repoRoot '.env'
$saved = Read-EnvSettings $envPath
$settings = [ordered]@{}
Write-Host 'YYEngine TestFlight credential setup. Secret input is hidden.'
Write-Host 'Configure the Discord manager with ./scripts/studio.ps1 -Action Setup.'
$repositoryDefault = if ($saved.GITHUB_REPOSITORY) { $saved.GITHUB_REPOSITORY } else { 'YotamHarris/Y-Y' }
$settings.GITHUB_REPOSITORY = Read-Setting 'GitHub repository (owner/name)' $repositoryDefault -Pattern '^[\w.-]+/[\w.-]+$'
Write-Host 'Coordinator token: this repository, Contents and Actions read/write.'
$settings.GITHUB_TOKEN = Read-Setting 'GitHub coordinator token' $saved.GITHUB_TOKEN -Secret
Write-EnvSettings -Path $envPath -Values $settings
Write-Host "Saved $envPath."

$apple = Read-Setting 'Configure Apple/TestFlight credentials in GitHub now? (y/n)' 'n' -Pattern '^[yn]$'
if ($apple -eq 'y') {
  $null = Resolve-SetupGh -Required
  $games = Get-Content -LiteralPath (Join-Path $repoRoot 'config/games.json') -Raw | ConvertFrom-Json
  $gameNames = @($games.PSObject.Properties.Name)
  do { $game = Read-Setting "Game ($($gameNames -join ', '))" $gameNames[0] } while ($game -cnotin $gameNames)
  $gameConfig = $games.$game
  $variables = [ordered]@{
    APPLE_TEAM_ID = Read-Setting 'Apple team ID' -Pattern '^[A-Z0-9]{10}$'
    BUNDLE_ID = Read-Setting 'Apple app bundle ID' $gameConfig.bundleId -Pattern '^[\w-]+(\.[\w-]+)+$'
    PROFILE_NAME = Read-Setting 'Exact provisioning profile name'
  }
  Write-Host 'Use create-signing.ps1 to create the CSR and .p12. Apple enrollment/app/profile/API key must already exist.'
  $fileValues = @{}
  foreach ($item in @(@('BUILD_CERTIFICATE_BASE64', 'Distribution .p12 file', '.p12'), @('BUILD_PROVISION_PROFILE_BASE64', 'Provisioning .mobileprovision file', '.mobileprovision'), @('ASC_KEY_CONTENT', 'App Store Connect .p8 private key file', '.p8'))) {
    do {
      $path = (Read-Setting $item[1]).Trim('"').Trim("'")
      $valid = (Test-Path -LiteralPath $path -PathType Leaf) -and [IO.Path]::GetExtension($path) -eq $item[2]
      if (-not $valid) { Write-Host "Enter an existing $($item[2]) file path." }
    } while (-not $valid)
    $path = (Resolve-Path -LiteralPath $path).Path
    $fileValues[$item[0]] = if ($item[2] -eq '.p8') { [IO.File]::ReadAllText($path) } else { [Convert]::ToBase64String([IO.File]::ReadAllBytes($path)) }
  }
  if ($fileValues.ASC_KEY_CONTENT -notmatch '-----BEGIN PRIVATE KEY-----') { throw 'The .p8 file is not a PEM private key.' }
  $secrets = [ordered]@{
    BUILD_CERTIFICATE_BASE64 = $fileValues.BUILD_CERTIFICATE_BASE64
    BUILD_PROVISION_PROFILE_BASE64 = $fileValues.BUILD_PROVISION_PROFILE_BASE64
    P12_PASSWORD = Read-Setting 'Password for the .p12 certificate' -Secret
    ASC_KEY_ID = Read-Setting 'App Store Connect API key ID' -Pattern '^[A-Z0-9]{10}$'
    ASC_ISSUER_ID = Read-Setting 'App Store Connect issuer ID' -Pattern '^[a-fA-F0-9]{8}(-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}$'
    ASC_KEY_CONTENT = $fileValues.ASC_KEY_CONTENT
  }
  Write-Host "Uploading Apple settings to $($settings.GITHUB_REPOSITORY), environment testflight-$game."
  Write-Host 'Setup token: Actions read and Environments read/write for this repo. It is not saved.'
  Write-Host "Creating a missing environment also needs Administration read/write. Alternatively create testflight-$game in the repo's Settings > Environments first."
  $setupToken = Read-Setting 'GitHub Apple-setup token (Enter reuses bot token if it has this permission)' $settings.GITHUB_TOKEN -Secret
  $previousToken = $env:GH_TOKEN
  try {
    while ($true) {
      $env:GH_TOKEN = $setupToken
      try {
        Set-TestFlightSettings -Repository $settings.GITHUB_REPOSITORY -Game $game -Variables $variables -Secrets $secrets
        break
      } catch {
        Write-Host $_.Exception.Message -ForegroundColor Yellow
        $retry = Read-Setting 'Retry after fixing GitHub permissions/environment? (y/n)' 'y' -Pattern '^[yn]$'
        if ($retry -eq 'n') { throw 'Apple upload is incomplete. Rerun Configure.cmd when GitHub is ready; local bot settings are saved.' }
        $setupToken = Read-Setting 'Replacement GitHub setup token (Enter keeps current token)' $setupToken -Secret
      }
    }
  } finally {
    $env:GH_TOKEN = $previousToken
    $setupToken = $null; $secrets.Clear(); $fileValues.Clear()
  }
  Write-Host "Apple settings uploaded. Create the internal tester group '$($gameConfig.internalGroup)' in App Store Connect."
}
Write-Host 'Next: ./scripts/studio.ps1 -Action Setup, then ./scripts/start.ps1'
