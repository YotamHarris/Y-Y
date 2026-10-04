param(
    [string]$SenderEmail = 'yotam.harris@gmail.com',
    [string]$Distro = 'Ubuntu'
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'configure.ps1') -LibraryOnly
$multicaRoot = Split-Path $PSScriptRoot -Parent
$multicaEnvPath = Join-Path $multicaRoot '.yy/multica/.env'
if (!(Test-Path -LiteralPath $multicaEnvPath)) { throw 'Run scripts/multica.ps1 -Action Setup first.' }
if ($SenderEmail -notmatch '^[^\s,@=]+@[^\s,@=]+\.[^\s,@=]+$') { throw 'Enter a valid Gmail sender address.' }

Write-Host "Configure Gmail delivery from $SenderEmail"
Write-Host 'Create a Google app password named Multica at https://myaccount.google.com/apppasswords'
Write-Host 'Google requires 2-Step Verification. Enter the app password here, not your normal Google password.'
$gmailAppPassword = $null
try {
    while ($true) {
        $securePassword = Read-Host 'Google app password (hidden input)' -AsSecureString
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
        try { $gmailAppPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) -replace '\s', '' }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer); $securePassword.Dispose() }
        if ($gmailAppPassword -match '^[a-zA-Z0-9]{16}$') { break }
        $gmailAppPassword = $null
        Write-Host 'Expected the 16-character app password shown by Google. Please try again.'
    }
    $emailSettings = [ordered]@{
        SMTP_HOST = 'smtp.gmail.com'
        SMTP_PORT = '465'
        SMTP_USERNAME = $SenderEmail
        SMTP_PASSWORD = $gmailAppPassword
        SMTP_FROM_EMAIL = $SenderEmail
        SMTP_TLS = 'implicit'
    }
    # Reuse the existing local-secret writer; retain unrelated bot/server settings.
    Write-EnvSettings -Path $multicaEnvPath -Values $emailSettings -Remove @('RESEND_API_KEY', 'RESEND_FROM_EMAIL')
} finally {
    $gmailAppPassword = $null
    if ($emailSettings) { $emailSettings.Clear() }
}

# Configure preserves the running public tunnel and its URL.
& (Join-Path $PSScriptRoot 'multica.ps1') -Action Configure -Distro $Distro
$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        $health = Invoke-RestMethod 'http://localhost:3072/healthz' -TimeoutSec 5
        if ($health.status -eq 'ok') { $ready = $true; break }
    } catch {}
    Start-Sleep -Seconds 1
}
if (!$ready) { throw 'Email settings saved, but the server did not become healthy. Check scripts/multica.ps1 -Action Status.' }

$recipient = 'yotam.harris@gmail.com'
Write-Host "Requesting a verification email to $recipient to check delivery."
try {
    $null = Invoke-RestMethod 'http://localhost:3072/auth/send-code' -Method Post -ContentType 'application/json' -Body (@{email=$recipient} | ConvertTo-Json) -TimeoutSec 30
    Write-Host "The server accepted the send. Check $recipient (including Spam) for the Multica login code."
    Write-Host 'Email delivery is confirmed only after the message arrives.'
} catch {
    $status = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 }
    if ($status -eq 429) {
        Write-Host 'Gmail configured. A code was requested recently; wait one minute, then request another on the login page.'
    } else {
        # Do not print arbitrary exception bodies, SMTP authentication details, or secrets.
        throw "Settings saved, but sending failed (HTTP $status). Check the app password and request a fresh code after correcting it."
    }
}
