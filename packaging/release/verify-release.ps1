<#
.SYNOPSIS
  Release verification: signature/version facts for every PE + the Release Security Report.

.DESCRIPTION
  Walks -Path folders (e.g. the release folder AND the staged Manager folder that was packed
  into the installer), records for each PE file: Get-AuthenticodeSignature status, signer,
  chain, timestamp, `signtool verify /pa /v` output (digest algorithm) when signtool exists,
  and the version resource. Then runs release_report.py, which checks
    - signatures (Production: every first-party and redistributed PE must be validly signed,
      timestamped, SHA-256; Development: the same findings are WARN)
    - ProductVersion / Inno AppVersion == pyproject version, publisher metadata
    - Inno [Setup] sanity (AppId, AppPublisher, PrivilegesRequired, ...)
    - no disguised executables, scripts outside the allow-list
  and writes release-report.md / release-report.json (+ SHA256SUMS.txt for -Artifact files)
  into -OutDir.

  Static only: nothing is executed (new unsigned exes cannot run on a Smart App Control PC).

  Exit: 0 PASS/WARN, 1 FAIL, 2 usage error.

.EXAMPLE
  powershell -File packaging\release\verify-release.ps1 -Path dist -OutDir dist\report
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string[]]$Path,
  [string[]]$Label,
  [string]$OutDir,
  [string[]]$Artifact,
  [ValidateSet('Development', 'Production')][string]$Mode = 'Development',
  [string[]]$Iss,
  [string]$Pyproject,
  [string[]]$Extra,
  [hashtable]$Tools = @{},
  [string]$Python,
  [string]$SignTool = $env:TOKENVAULT_SIGNTOOL
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $Pyproject) { $Pyproject = Join-Path $Root 'pyproject.toml' }
if (-not $Iss) { $Iss = @((Join-Path $Root 'packaging\manager.iss'), (Join-Path $Root 'packaging\adapter-template.iss')) }
if (-not $OutDir) { $OutDir = $Path[0] }
if (-not $Python) {
  $venv = Join-Path $Root '.venv\Scripts\python.exe'
  $Python = if (Test-Path $venv) { $venv } else { 'python' }
}
$config = Get-ReleaseConfig
$tool = Find-SignTool $SignTool

$roots = @(); $files = @()
for ($i = 0; $i -lt $Path.Count; $i++) {
  $base = (Resolve-Path -LiteralPath $Path[$i]).Path.TrimEnd('\')
  $lbl = if ($Label -and $Label.Count -gt $i) { $Label[$i] } else { Split-Path -Leaf $base }
  $roots += [ordered]@{ label = $lbl; path = $base }
  $pes = Get-PeCandidates @($base) $config
  Write-Host ("verify: {0} -> {1} PE file(s)" -f $base, $pes.Count) -ForegroundColor Cyan
  foreach ($f in $pes) {
    $sig = Get-SignatureFacts $f.FullName
    $st = ''
    if ($tool -and $sig.status -ne 'NotSigned') {
      $st = (Invoke-Native $tool @('verify', '/pa', '/v', $f.FullName)).Out
    }
    $files += [ordered]@{
      path = $lbl + '/' + $f.FullName.Substring($base.Length + 1).Replace('\', '/')
      signature = $sig; version = Get-VersionFacts $f.FullName; signtool = $st
    }
  }
}

$Tools['signtool'] = if ($tool) { "$tool ($((Get-Item $tool).VersionInfo.FileVersion))" } else { 'not found' }
$Tools['powershell'] = $PSVersionTable.PSVersion.ToString()
$Tools['os'] = [Environment]::OSVersion.VersionString
New-Item -ItemType Directory -Force $OutDir | Out-Null
$factsPath = Join-Path $OutDir 'release-facts.json'
[ordered]@{ roots = $roots; files = $files; tools = $Tools } |
  ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $factsPath -Encoding UTF8

$pyArgs = @((Join-Path $PSScriptRoot 'release_report.py'), '--facts', $factsPath, '--pyproject', $Pyproject,
            '--mode', $Mode, '--out-dir', $OutDir)
foreach ($i in $Iss) { if (Test-Path $i) { $pyArgs += @('--iss', $i) } }
foreach ($a in @($Artifact)) { if ($a) { $pyArgs += @('--artifact', $a) } }
foreach ($e in @($Extra)) { if ($e) { $pyArgs += @('--extra', $e) } }
& $Python @pyArgs
$rc = $LASTEXITCODE
Remove-Item -LiteralPath $factsPath -ErrorAction SilentlyContinue
exit $rc
