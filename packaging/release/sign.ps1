<#
.SYNOPSIS
  Authenticode-signs TokenVault release files (SHA-256 + RFC 3161 timestamp) and verifies them.

.DESCRIPTION
  Credentials (exactly one source; parameters override the environment):
    Thumbprint  certificate in the Windows store (also HSM/token-backed keys whose
                provider exposes the key there):
                  TOKENVAULT_SIGN_THUMBPRINT, TOKENVAULT_SIGN_CERT_STORE=CurrentUser|LocalMachine
    Pfx         PFX file, password ONLY from an environment variable (never a parameter):
                  TOKENVAULT_SIGN_PFX, TOKENVAULT_SIGN_PFX_PASSWORD
                  (publicly trusted code-signing keys must live on an HSM since June 2023,
                  so this mode is for an internal/enterprise CA)
    Artifact    Microsoft Artifact Signing (formerly Trusted Signing) through the signtool dlib:
                  TOKENVAULT_SIGN_ARTIFACT_DLIB      = ...\x64\Azure.CodeSigning.Dlib.dll
                  TOKENVAULT_SIGN_ARTIFACT_METADATA  = metadata.json (Endpoint,
                  CodeSigningAccountName, CertificateProfileName); auth = DefaultAzureCredential
  TOKENVAULT_SIGN_TIMESTAMP_URL: RFC 3161 server of your CA (required for Thumbprint/Pfx;
  Artifact defaults to http://timestamp.acs.microsoft.com, as documented by Microsoft).

  Policy (signing.json): first-party files are signed; files already validly signed by their
  vendor are never touched; unsigned redistributed binaries are signed when
  sign_unsigned_bundled is true; "unsigned_by_design" files (adapter template) are skipped.
  Idempotent: a file already validly signed with the same certificate is skipped.

  No credentials: Development -> UNSIGNED banner, exit 0. Production -> exit 2.

  Exit codes: 0 ok / unsigned-dev, 2 credentials missing or invalid, 3 signtool missing,
  4 signing failed, 5 verification failed, 10 (-CheckCredentials only) none configured in
  Development.

.EXAMPLE
  powershell -File packaging\release\sign.ps1 -Path dist\manager\TokenVault-Manager -WhatIf
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
  [string[]]$Path,
  [ValidateSet('Development', 'Production')][string]$Mode = 'Development',
  [ValidateSet('Auto', 'Thumbprint', 'Pfx', 'Artifact')][string]$Credential = 'Auto',
  [string]$Thumbprint = $env:TOKENVAULT_SIGN_THUMBPRINT,
  [string]$CertStore = $(if ($env:TOKENVAULT_SIGN_CERT_STORE) { $env:TOKENVAULT_SIGN_CERT_STORE } else { 'CurrentUser' }),
  [string]$PfxPath = $env:TOKENVAULT_SIGN_PFX,
  [string]$ArtifactDlib = $env:TOKENVAULT_SIGN_ARTIFACT_DLIB,
  [string]$ArtifactMetadata = $env:TOKENVAULT_SIGN_ARTIFACT_METADATA,
  [string]$TimestampUrl = $env:TOKENVAULT_SIGN_TIMESTAMP_URL,
  [string]$SignTool = $env:TOKENVAULT_SIGNTOOL,
  [string]$ConfigPath,
  [switch]$CheckCredentials
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$ArtifactTimestamp = 'http://timestamp.acs.microsoft.com'
$CodeSigningEku = '1.3.6.1.5.5.7.3.3'

function Stop-With([int]$Code, [string]$Message) {
  if ($Message) { Write-Host "sign.ps1: $Message" -ForegroundColor Red }
  exit $Code
}

function Resolve-Credential {
  $set = @()
  if ($Thumbprint) { $set += 'Thumbprint' }
  if ($PfxPath) { $set += 'Pfx' }
  if ($ArtifactMetadata -or $ArtifactDlib) { $set += 'Artifact' }
  if ($Credential -ne 'Auto') {
    if ($set -notcontains $Credential) { Stop-With 2 "-Credential $Credential selected but its settings are empty." }
    return $Credential
  }
  if ($set.Count -gt 1) { Stop-With 2 ("several credential sources configured (" + ($set -join ', ') + "); pick one with -Credential.") }
  if ($set.Count -eq 0) { return 'None' }
  return $set[0]
}

function Test-CodeSigningCert($Cert, [string]$What) {
  if (-not $Cert) { Stop-With 2 "$What : certificate not found." }
  $ekus = @($Cert.EnhancedKeyUsageList | ForEach-Object { $_.ObjectId })
  if ($ekus.Count -gt 0 -and $ekus -notcontains $CodeSigningEku) { Stop-With 2 "$What : certificate has no Code Signing EKU." }
  if ($Cert.NotAfter -lt (Get-Date)) { Stop-With 2 "$What : certificate expired on $($Cert.NotAfter)." }
}

# Returns @{ Kind; Args (signtool sign args, secrets included); Display (masked); Thumbprint }
function Get-SignSpec([string]$Kind) {
  switch ($Kind) {
    'Thumbprint' {
      $tp = ($Thumbprint -replace '\s', '').ToUpperInvariant()
      if ($CertStore -notin 'CurrentUser', 'LocalMachine') { Stop-With 2 "TOKENVAULT_SIGN_CERT_STORE must be CurrentUser or LocalMachine." }
      $cert = Get-Item -LiteralPath "Cert:\$CertStore\My\$tp" -ErrorAction SilentlyContinue
      Test-CodeSigningCert $cert "thumbprint $tp in Cert:\$CertStore\My"
      if (-not $cert.HasPrivateKey) { Stop-With 2 "thumbprint $tp : no private key available to this user." }
      if (-not $TimestampUrl) { Stop-With 2 'TOKENVAULT_SIGN_TIMESTAMP_URL is required (your CA documents its RFC 3161 URL).' }
      $a = @('/sha1', $tp, '/s', 'My')
      if ($CertStore -eq 'LocalMachine') { $a += '/sm' }
      $a += @('/fd', 'SHA256', '/tr', $TimestampUrl, '/td', 'SHA256')
      return @{ Kind = $Kind; Args = $a; Display = ($a -join ' '); Thumbprint = $tp; Subject = $cert.Subject }
    }
    'Pfx' {
      if (-not (Test-Path -LiteralPath $PfxPath)) { Stop-With 2 "PFX not found: $PfxPath" }
      $pw = $env:TOKENVAULT_SIGN_PFX_PASSWORD
      if (-not $pw) { Stop-With 2 'TOKENVAULT_SIGN_PFX_PASSWORD is not set (the password is only read from the environment).' }
      try {
        $cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2(
          (Resolve-Path -LiteralPath $PfxPath).Path, $pw, 'EphemeralKeySet')
      } catch { Stop-With 2 "PFX could not be opened (wrong password or file): $($_.Exception.InnerException.Message)" }
      Test-CodeSigningCert $cert "PFX $PfxPath"
      if (-not $TimestampUrl) { Stop-With 2 'TOKENVAULT_SIGN_TIMESTAMP_URL is required (your CA documents its RFC 3161 URL).' }
      $a = @('/f', (Resolve-Path -LiteralPath $PfxPath).Path, '/p', $pw, '/fd', 'SHA256', '/tr', $TimestampUrl, '/td', 'SHA256')
      $shown = ($a -join ' ').Replace($pw, '********')
      return @{ Kind = $Kind; Args = $a; Display = $shown; Thumbprint = $cert.Thumbprint; Subject = $cert.Subject }
    }
    'Artifact' {
      if (-not $ArtifactDlib -or -not (Test-Path -LiteralPath $ArtifactDlib)) { Stop-With 2 "TOKENVAULT_SIGN_ARTIFACT_DLIB must point at Azure.CodeSigning.Dlib.dll (got '$ArtifactDlib'). Install: winget install -e --id Microsoft.Azure.ArtifactSigningClientTools" }
      if (-not $ArtifactMetadata -or -not (Test-Path -LiteralPath $ArtifactMetadata)) { Stop-With 2 "TOKENVAULT_SIGN_ARTIFACT_METADATA must point at metadata.json (got '$ArtifactMetadata')." }
      try { $md = Get-Content -Raw -LiteralPath $ArtifactMetadata | ConvertFrom-Json } catch { Stop-With 2 "metadata.json is not valid JSON: $($_.Exception.Message)" }
      foreach ($k in 'Endpoint', 'CodeSigningAccountName', 'CertificateProfileName') {
        if (-not $md.$k) { Stop-With 2 "metadata.json lacks '$k'." }
      }
      if ($md.Endpoint -notmatch '^https://[a-z0-9]+\.codesigning\.azure\.net/?$') {
        Write-Warning "Endpoint '$($md.Endpoint)' does not look like a regional https://<region>.codesigning.azure.net URI; a region mismatch gives 403."
      }
      $ts = if ($TimestampUrl) { $TimestampUrl } else { $ArtifactTimestamp }
      $a = @('/fd', 'SHA256', '/tr', $ts, '/td', 'SHA256', '/dlib', (Resolve-Path -LiteralPath $ArtifactDlib).Path,
             '/dmdf', (Resolve-Path -LiteralPath $ArtifactMetadata).Path)
      # Artifact Signing certificates live ~3 days: idempotence compares the subject.
      $subj = $env:TOKENVAULT_SIGN_EXPECTED_SUBJECT
      return @{ Kind = $Kind; Args = $a; Display = ($a -join ' '); Thumbprint = ''; Subject = $subj }
    }
  }
}

# Same certificate and an intact file hash (trust/chain problems are reported by verify).
function Test-SameSigner($Sig, $Spec) {
  if (-not $Sig.SignerCertificate -or $Sig.Status -in 'NotSigned', 'HashMismatch') { return $false }
  if ($Spec.Thumbprint) { return $Sig.SignerCertificate.Thumbprint -eq $Spec.Thumbprint }
  if ($Spec.Subject) { return $Sig.SignerCertificate.Subject -eq $Spec.Subject }
  return $false
}

# Decide per file: SIGN or SKIP (with reason). Signature is read once.
function Get-Plan($Files, $Config, $Spec) {
  foreach ($f in $Files) {
    $role = Get-FileRole $f.Name $Config
    $sig = Get-AuthenticodeSignature -LiteralPath $f.FullName
    $st = [string]$sig.Status
    $action = 'SKIP'; $why = ''
    if ($role -eq 'unsigned_by_design') { $why = 'unsigned by design (payload is appended later)' }
    elseif ($st -eq 'HashMismatch') { $action = 'ERROR'; $why = 'existing signature does not match the file (modified after signing?)' }
    elseif ($Spec -and (Test-SameSigner $sig $Spec)) { $why = 'already signed with this certificate' }
    elseif ($st -eq 'Valid' -and $role -eq 'bundled') { $why = 'vendor-signed: ' + ($sig.SignerCertificate.Subject -replace '^CN=("?)([^,"]+).*', '$2') }
    elseif ($role -eq 'first_party') { $action = 'SIGN'; $why = "first-party ($st)" }
    elseif ($st -eq 'NotSigned' -and $Config.sign_unsigned_bundled) { $action = 'SIGN'; $why = 'redistributed, no vendor signature' }
    elseif ($st -eq 'NotSigned') { $why = 'redistributed, unsigned (policy: not signed)' }
    else { $why = "vendor signature status $st (left as is)" }
    [pscustomobject]@{ File = $f.FullName; Name = $f.Name; Role = $role; Status = $st; Action = $action; Reason = $why }
  }
}

function Invoke-Verify([string]$Tool, [string]$File) {
  $r = Invoke-Native $Tool @('verify', '/pa', '/v', $File)
  $out = $r.Out
  $okTool = ($r.Code -eq 0)
  $sig = Get-AuthenticodeSignature -LiteralPath $File
  $problems = @()
  if (-not $okTool) { $problems += 'signtool verify /pa failed' }
  if ($sig.Status -ne 'Valid') { $problems += "status $($sig.Status)" }
  if (-not $sig.TimeStamperCertificate) { $problems += 'no timestamp' }
  if ($out -notmatch 'Hash of file \(sha256\)') { $problems += 'file digest is not SHA-256' }
  return $problems
}

# ---------------------------------------------------------------------------------------
$config = Get-ReleaseConfig $ConfigPath
$kind = Resolve-Credential

if ($CheckCredentials) {
  if ($kind -eq 'None') {
    Write-UnsignedBanner $Mode
    if ($Mode -eq 'Production') { Stop-With 2 'Production mode needs signing credentials (see BUILD.md).' }
    exit 10
  }
  $spec = Get-SignSpec $kind
  if (-not (Find-SignTool $SignTool)) { Write-SignToolMissingHint; exit 3 }
  Write-Host "sign.ps1: credentials OK ($kind; $($spec.Subject))" -ForegroundColor Green
  exit 0
}

if (-not $Path) { Stop-With 1 '-Path is required (files or folders to sign).' }
$files = Get-PeCandidates $Path $config
$spec = if ($kind -ne 'None') { Get-SignSpec $kind } else { $null }
$plan = @(Get-Plan $files $config $spec)
$plan | Format-Table @{ n = 'Action'; e = { $_.Action } }, @{ n = 'Role'; e = { $_.Role } }, Name, Reason -AutoSize |
  Out-String -Width 200 | Write-Host

if ($plan | Where-Object Action -eq 'ERROR') { Stop-With 4 'refusing to continue: a file has a broken signature.' }
$toSign = @($plan | Where-Object Action -eq 'SIGN')

if ($kind -eq 'None') {
  Write-UnsignedBanner $Mode
  if ($Mode -eq 'Production') { Stop-With 2 'Production mode needs signing credentials (see BUILD.md).' }
  Write-Host "sign.ps1: $($toSign.Count) file(s) would need a signature; left UNSIGNED (Development)." -ForegroundColor Yellow
  exit 0
}

$tool = Find-SignTool $SignTool
if (-not $tool) { Write-SignToolMissingHint; exit 3 }
Write-Host "sign.ps1: $kind via $tool" -ForegroundColor Cyan
Write-Host "          signtool sign $($spec.Display) <files>" -ForegroundColor DarkGray

$done = @()
foreach ($item in $toSign) {
  if (-not $PSCmdlet.ShouldProcess($item.File, 'Authenticode sign (SHA-256 + RFC 3161 timestamp)')) { continue }
  $r = Invoke-Native $tool (@('sign') + $spec.Args + @($item.File))
  if ($r.Code -ne 0) {
    $out = $r.Out
    if ($spec.Kind -eq 'Pfx') { $out = $out.Replace($env:TOKENVAULT_SIGN_PFX_PASSWORD, '********') }
    Write-Host $out
    Stop-With 4 "signtool sign failed for $($item.File) (exit $($r.Code))."
  }
  $done += $item.File
}

# Also re-verify files skipped as "already signed with this certificate": a retry (ISCC
# retries a failed sign command) must not turn an untrusted/untimestamped signature into OK.
$same = @($plan | Where-Object { $_.Action -eq 'SKIP' -and $_.Reason -like 'already signed*' } | ForEach-Object File)
$bad = 0
foreach ($f in @($done + $same)) {
  $p = @(Invoke-Verify $tool $f)
  if ($p.Count -gt 0) { $bad++; Write-Host ("VERIFY FAIL {0}: {1}" -f $f, ($p -join '; ')) -ForegroundColor Red }
  else { Write-Host "VERIFY OK   $f" -ForegroundColor Green }
}
if ($bad -gt 0) { Stop-With 5 "$bad signature(s) failed verification." }
Write-Host ("sign.ps1: signed {0}, skipped {1}." -f $done.Count, ($plan.Count - $done.Count)) -ForegroundColor Green
exit 0
