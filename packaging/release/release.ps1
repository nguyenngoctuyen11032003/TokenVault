<#
.SYNOPSIS
  TokenVault release pipeline: clean build -> dependency audit -> (signing) -> installer ->
  verification -> release\vX.Y.Z\ with SHA256SUMS.txt and the Release Security Report.

.DESCRIPTION
  -Mode Development (default): unsigned builds are allowed; the report says UNSIGNED loudly.
  -Mode Production: signing credentials are required up front (see BUILD.md); any unsigned
   first-party or redistributed PE, a missing timestamp or a non-SHA-256 digest fails.

  Steps
    1. preflight: version from pyproject, tools, credentials (Production fails fast)
    2. clean: build\pyi-*, build\release-staging, release\vX.Y.Z
    3. dependency audit (pip list / pip check, optional pip-audit with -Online)
    4. packaging\build.ps1 -Sign (signs adapter exe before the template is compiled, the
       Manager folder before Inno packs it, and Setup + uninstaller through ISCC /S)
    5. bundle audit (PyInstaller _internal + warn files)
    6. copy the deliverable(s) into release\vX.Y.Z\, safety sign pass on them
    7. verify-release.ps1 over release\vX.Y.Z (artifacts) + staged Manager/adapter folders
    8. remove staging (keep with -KeepStaging)

  Reproducibility: SOURCE_DATE_EPOCH = last commit time and PYTHONHASHSEED=0 are exported;
  Python honours both for .pyc; PyInstaller/Inno honouring them is not verified. Tool
  versions are recorded in the report.

  Exit: 0 PASS/WARN, 1 report FAIL, 2 credentials missing (Production), 3 build failed.
#>
[CmdletBinding()]
param(
  [ValidateSet('Development', 'Production')][string]$Mode = 'Development',
  [string]$Python,
  [switch]$Online,
  [switch]$KeepStaging,
  [switch]$SkipWebView2Download
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $Root
if (-not $Python) {
  $venv = Join-Path $Root '.venv\Scripts\python.exe'
  $Python = if (Test-Path $venv) { $venv } else { 'python' }
}
$env:PYTHONPATH = Join-Path $Root 'src'
$sign = Join-Path $PSScriptRoot 'sign.ps1'

function Step([string]$What) { Write-Host ''; Write-Host "### $What" -ForegroundColor Cyan }

# 1. preflight ---------------------------------------------------------------------------
Step "preflight ($Mode)"
$version = (& $Python -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])").Trim()
if ($LASTEXITCODE -ne 0 -or -not $version) { throw 'cannot read the version from pyproject.toml' }
$relDir = Join-Path $Root "release\v$version"
$staging = Join-Path $Root 'build\release-staging'
& $sign -Mode $Mode -CheckCredentials
$cred = $LASTEXITCODE
if ($cred -eq 2 -or $cred -eq 3) { Write-Host "release: signing preflight failed (exit $cred)." -ForegroundColor Red; exit 2 }
$signed = ($cred -eq 0)

$tools = @{}
$tools['python'] = (& $Python -c "import sys;print(sys.version.split()[0])").Trim()
$tools['pyinstaller'] = (& $Python -m PyInstaller --version).Trim()
$tools['pip'] = ((& $Python -m pip --version) -split ' ')[1]
$commit = (git rev-parse --short HEAD 2>$null)
$dirty = if (git status --porcelain 2>$null) { ' (working tree has uncommitted changes)' } else { '' }
$tools['git'] = "$commit$dirty"
$epoch = (git log -1 --format=%ct 2>$null)
if ($epoch) { $env:SOURCE_DATE_EPOCH = $epoch.Trim(); $tools['SOURCE_DATE_EPOCH'] = $env:SOURCE_DATE_EPOCH }
$env:PYTHONHASHSEED = '0'; $tools['PYTHONHASHSEED'] = '0'
$tools['signing'] = if ($signed) { 'credentials configured' } else { 'UNSIGNED (no credentials)' }
Write-Host "version $version -> $relDir"

# 2. clean -------------------------------------------------------------------------------
Step 'clean work folders'
foreach ($d in @((Join-Path $Root 'build\pyi-adapter'), (Join-Path $Root 'build\pyi-manager'), $staging, $relDir)) {
  if (Test-Path $d) { Remove-Item -Recurse -Force $d }
}
New-Item -ItemType Directory -Force $relDir, $staging | Out-Null

# 3. dependency audit --------------------------------------------------------------------
Step 'dependency audit'
$depsJson = Join-Path $staging 'deps.json'
$auditArgs = @((Join-Path $PSScriptRoot 'deps_audit.py'), '--pyproject', 'pyproject.toml', '--out', $depsJson)
if ($Online) { $auditArgs += '--online' }
& $Python @auditArgs
if ($LASTEXITCODE -ne 0) { Write-Warning 'dependency audit reported FAIL (see the report).' }

# 4. build (+ signing hooks) -------------------------------------------------------------
Step 'build (PyInstaller + Inno Setup, signing hooks)'
$dist = Join-Path $staging 'dist'
$buildLog = Join-Path $staging 'build.log'
$bArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $Root 'packaging\build.ps1'),
           '-DistDir', $dist, '-Python', $Python, '-Sign', '-SignMode', $Mode)
if ($SkipWebView2Download) { $bArgs += '-SkipWebView2Download' }
# child process: build.ps1 throws/exits on its own; its exit code is what counts
$ErrorActionPreference = 'Continue'
& powershell.exe @bArgs 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $buildLog | Out-Host
$brc = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
$isccVer = Select-String -Path $buildLog -Pattern 'Compiler engine version:\s*(.+)$' | Select-Object -First 1
$tools['inno_setup'] = if ($isccVer) { $isccVer.Matches[0].Groups[1].Value.Trim() } else { 'not used' }
$setup = Join-Path $dist 'TokenVault-Manager-Setup.exe'
if ($brc -ne 0 -or -not (Test-Path $setup)) {
  Write-Host "release: build failed or produced no installer (exit $brc); see $buildLog" -ForegroundColor Red
  exit 3
}

# 5. bundle audit ------------------------------------------------------------------------
Step 'bundle audit'
$mgrDir = Join-Path $dist 'manager\TokenVault-Manager'
$warnFiles = @(Get-ChildItem (Join-Path $Root 'build\pyi-*') -Recurse -Filter 'warn-*.txt' | ForEach-Object FullName)
# Task 29: the installed Manager is the signed portable runtime, not a PyInstaller folder;
# the frozen Manager folder only exists when build.ps1 ran with -WithManagerExe.
if (Test-Path (Join-Path $mgrDir '_internal')) { $auditArgs += @('--bundle-dir', (Join-Path $mgrDir '_internal')) }
foreach ($w in $warnFiles) { $auditArgs += @('--warn-file', $w) }
& $Python @auditArgs
if ($LASTEXITCODE -ne 0) { Write-Warning 'dependency/bundle audit reported FAIL.' }

# 6. release folder ----------------------------------------------------------------------
Step "release folder $relDir"
Copy-Item $setup $relDir
$artifacts = @(Join-Path $relDir 'TokenVault-Manager-Setup.exe')
& $sign -Mode $Mode -Path $artifacts   # idempotent: skips what ISCC already signed
if ($LASTEXITCODE -ne 0) { Write-Host "release: signing the artifacts failed (exit $LASTEXITCODE)." -ForegroundColor Red; exit 1 }

# 7. verification + report ---------------------------------------------------------------
Step 'verify + Release Security Report'
# The Manager folder is exactly what Inno packed (incl. adapter-setup-template.exe).
$vPaths = @($relDir); $vLabels = @("release/v$version")
if (Test-Path $mgrDir) {
  $vPaths += $mgrDir; $vLabels += 'package/TokenVault-Manager'
} else {
  $rtDir = Join-Path $Root 'templates\portable-runtime'   # what Inno installs as the Manager
  if (Test-Path $rtDir) { $vPaths += $rtDir; $vLabels += 'package/portable-runtime' }
}
if (Test-Path (Join-Path $dist 'adapter')) { $vPaths += (Join-Path $dist 'adapter'); $vLabels += 'package/adapter' }
$vArgs = @{
  Path = $vPaths
  Label = $vLabels
  OutDir = $relDir; Artifact = $artifacts; Mode = $Mode; Extra = @($depsJson); Tools = $tools
  Python = $Python
}
& (Join-Path $PSScriptRoot 'verify-release.ps1') @vArgs
$vrc = $LASTEXITCODE
Copy-Item $buildLog (Join-Path $relDir 'build.log')

# 8. cleanup -----------------------------------------------------------------------------
if (-not $KeepStaging) { Remove-Item -Recurse -Force $staging }
Write-Host ''
Write-Host "release: v$version ($Mode) -> $relDir" -ForegroundColor Green
Get-ChildItem $relDir | ForEach-Object { '  {0,-34} {1,12:N0} bytes' -f $_.Name, $_.Length }
if (-not $signed) { Write-UnsignedBanner $Mode }
exit $vrc
