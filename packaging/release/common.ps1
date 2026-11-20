# Shared helpers for packaging\release\*.ps1 (dot-source: . "$PSScriptRoot\common.ps1").
# No side effects on load.

$script:ReleaseDir = $PSScriptRoot

function Get-ReleaseConfig([string]$Path) {
  if (-not $Path) { $Path = Join-Path $script:ReleaseDir 'signing.json' }
  if (-not (Test-Path $Path)) { throw "signing policy not found: $Path" }
  return Get-Content -Raw -Encoding UTF8 $Path | ConvertFrom-Json
}

function Test-NameMatch([string]$Name, $Patterns) {
  foreach ($p in @($Patterns)) {
    if ($p -and ($Name -like $p)) { return $true }   # -like is case-insensitive
  }
  return $false
}

function Get-UnsignedByDesignNames($Config) {
  if (-not $Config.unsigned_by_design) { return @() }
  return @($Config.unsigned_by_design.PSObject.Properties | ForEach-Object { $_.Name })
}

# 'first_party' | 'unsigned_by_design' | 'bundled'
function Get-FileRole([string]$Name, $Config) {
  if (Test-NameMatch $Name (Get-UnsignedByDesignNames $Config)) { return 'unsigned_by_design' }
  if (Test-NameMatch $Name $Config.first_party) { return 'first_party' }
  return 'bundled'
}

# Every file below the given files/folders whose extension is a PE extension of the policy.
function Get-PeCandidates([string[]]$Paths, $Config) {
  $exts = @($Config.pe_extensions | ForEach-Object { $_.ToLowerInvariant() })
  $out = New-Object System.Collections.Generic.List[System.IO.FileInfo]
  foreach ($p in $Paths) {
    if (-not $p) { continue }
    if (-not (Test-Path -LiteralPath $p)) { throw "path not found: $p" }
    $item = Get-Item -LiteralPath $p
    if ($item.PSIsContainer) {
      Get-ChildItem -LiteralPath $item.FullName -Recurse -File |
        Where-Object { $exts -contains $_.Extension.ToLowerInvariant() } |
        Sort-Object FullName | ForEach-Object { $out.Add($_) }
    } else {
      $out.Add($item)
    }
  }
  return ,$out
}

# signtool.exe: explicit path > PATH > newest Windows SDK (x64) > App Certification Kit.
function Find-SignTool([string]$Explicit) {
  if ($Explicit) {
    if (Test-Path -LiteralPath $Explicit) { return (Resolve-Path -LiteralPath $Explicit).Path }
    throw "signtool.exe not found at '$Explicit' (TOKENVAULT_SIGNTOOL / -SignTool)."
  }
  $cmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  $kits = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10'
  $bin = Join-Path $kits 'bin'
  if (Test-Path $bin) {
    $cands = Get-ChildItem $bin -Directory -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -match '^\d+\.\d+\.\d+\.\d+$' } |
      Sort-Object { [version]$_.Name } -Descending
    foreach ($c in $cands) {
      $p = Join-Path $c.FullName 'x64\signtool.exe'
      if (Test-Path $p) { return $p }
    }
  }
  $ack = Join-Path $kits 'App Certification Kit\signtool.exe'
  if (Test-Path $ack) { return $ack }
  return $null
}

# Runs a native tool and captures stdout+stderr as text without turning stderr lines into
# terminating errors (callers run with $ErrorActionPreference = 'Stop').
function Invoke-Native([string]$Exe, [string[]]$Arguments) {
  $ErrorActionPreference = 'Continue'
  $text = & $Exe @Arguments 2>&1 | ForEach-Object { "$_" } | Out-String
  return [pscustomobject]@{ Code = $LASTEXITCODE; Out = $text }
}

function Write-SignToolMissingHint {
  Write-Host 'signtool.exe was not found.' -ForegroundColor Red
  Write-Host '  Install the Windows SDK (component "Windows SDK Signing Tools for Desktop Apps"):' -ForegroundColor Yellow
  Write-Host '    https://developer.microsoft.com/windows/downloads/windows-sdk/' -ForegroundColor Yellow
  Write-Host '  or extract the NuGet package Microsoft.Windows.SDK.BuildTools and point' -ForegroundColor Yellow
  Write-Host '  TOKENVAULT_SIGNTOOL (or -SignTool) at its bin\<version>\x64\signtool.exe.' -ForegroundColor Yellow
}

function Write-UnsignedBanner([string]$Mode) {
  $bar = '=' * 78
  $color = if ($Mode -eq 'Production') { 'Red' } else { 'Yellow' }
  Write-Host ''
  Write-Host $bar -ForegroundColor $color
  Write-Host '  UNSIGNED build - no code-signing credentials are configured' -ForegroundColor $color
  Write-Host '  BAN DUNG CHUA KY SO (unsigned)' -ForegroundColor $color
  Write-Host $bar -ForegroundColor $color
  Write-Host '  * Windows 11 with Smart App Control ON will BLOCK these .exe files' -ForegroundColor $color
  Write-Host '    (error 4551, no "Run anyway"). Use the no-exe portable package there.' -ForegroundColor $color
  Write-Host '  * Elsewhere SmartScreen shows "Windows protected your PC" (More info -> Run anyway).' -ForegroundColor $color
  Write-Host '  * Configure TOKENVAULT_SIGN_* (see BUILD.md) and use -Mode Production to release signed.' -ForegroundColor $color
  Write-Host $bar -ForegroundColor $color
  Write-Host ''
}

# Facts about one file's Authenticode signature (no signtool needed).
function Get-SignatureFacts([string]$Path) {
  $s = Get-AuthenticodeSignature -LiteralPath $Path
  $f = [ordered]@{
    status = [string]$s.Status; status_message = [string]$s.StatusMessage
    signer_subject = ''; signer_issuer = ''; signer_thumbprint = ''; signer_not_after = ''
    timestamped = $false; timestamp_subject = ''; chain = @()
  }
  if ($s.SignerCertificate) {
    $c = $s.SignerCertificate
    $f.signer_subject = $c.Subject; $f.signer_issuer = $c.Issuer
    $f.signer_thumbprint = $c.Thumbprint; $f.signer_not_after = $c.NotAfter.ToString('o')
    $chain = New-Object System.Security.Cryptography.X509Certificates.X509Chain
    $chain.ChainPolicy.RevocationMode = 'NoCheck'   # offline-safe; Status above is the authority
    [void]$chain.Build($c)
    $f.chain = @($chain.ChainElements | ForEach-Object { $_.Certificate.Subject })
  }
  if ($s.TimeStamperCertificate) {
    $f.timestamped = $true; $f.timestamp_subject = $s.TimeStamperCertificate.Subject
  }
  return $f
}

function Get-VersionFacts([string]$Path) {
  $v = (Get-Item -LiteralPath $Path).VersionInfo
  return [ordered]@{
    CompanyName = [string]$v.CompanyName; FileDescription = [string]$v.FileDescription
    ProductName = [string]$v.ProductName; ProductVersion = [string]$v.ProductVersion
    FileVersion = [string]$v.FileVersion; LegalCopyright = [string]$v.LegalCopyright
    OriginalFilename = [string]$v.OriginalFilename; InternalName = [string]$v.InternalName
  }
}
