param([string]$CertificatePath, [string]$OutputDirectory = (Join-Path (Split-Path $PSScriptRoot -Parent) '.yy/signing'))
$ErrorActionPreference = 'Stop'
$opensslPath = 'C:/Program Files/Git/usr/bin/openssl.exe'
if (-not (Test-Path -LiteralPath $opensslPath)) { $opensslPath = 'openssl' }
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$keyPath = Join-Path $OutputDirectory 'distribution.key'
$csrPath = Join-Path $OutputDirectory 'distribution.csr'
if (-not $CertificatePath) {
  if (Test-Path -LiteralPath $keyPath) { throw 'Private key already exists; reuse the CSR or choose a new directory.' }
  & $opensslPath req -new -newkey rsa:2048 -nodes -keyout $keyPath -out $csrPath -subj '/CN=YYEngine Distribution'
  if ($LASTEXITCODE -ne 0) { throw 'CSR generation failed' }
  Write-Host "Upload $csrPath in Apple Developer Certificates to create an Apple Distribution certificate. Download the .cer and rerun with -CertificatePath."
} else {
  if (-not (Test-Path -LiteralPath $keyPath)) { throw 'Generate the CSR in this directory first.' }
  $pemPath = Join-Path $OutputDirectory 'distribution.pem'
  & $opensslPath x509 -inform DER -in (Resolve-Path -LiteralPath $CertificatePath).Path -out $pemPath
  if ($LASTEXITCODE -ne 0) { throw 'Certificate conversion failed' }
  $securePassword = Read-Host 'Password for distribution.p12' -AsSecureString
  $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
  try {
    $env:YY_SIGNING_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    & $opensslPath pkcs12 -export -inkey $keyPath -in $pemPath -out (Join-Path $OutputDirectory 'distribution.p12') -passout env:YY_SIGNING_PASSWORD
    if ($LASTEXITCODE -ne 0) { throw 'Signing bundle creation failed' }
  } finally {
    Remove-Item Env:YY_SIGNING_PASSWORD -ErrorAction SilentlyContinue
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
  }
  Write-Host 'Signing bundle created. Store the .p12 as BUILD_CERTIFICATE_BASE64 in GitHub secrets; store its password as P12_PASSWORD.'
}
