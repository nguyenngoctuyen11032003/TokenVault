<#
.SYNOPSIS
  Builds the TokenVault installers: adapter -> adapter template -> Manager -> Manager installer.

.DESCRIPTION
  1. PyInstaller: Adapter (onefile, windowed)    -> dist\adapter\TokenVaultAdapter.exe
  2. Inno Setup:  adapter-template.iss           -> dist\adapter-setup-template.exe  (UNSIGNED)
  3. Template placed next to the Manager exe     -> dist\manager\TokenVault-Manager\adapter-setup-template.exe
     (manager\paths.py looks for it in the install folder)
  4. PyInstaller: Manager (onedir, windowed)     -> dist\manager\TokenVault-Manager\
  5. WebView2 bootstrapper (downloaded once into packaging\redist, git-ignored)
  6. Inno Setup:  manager.iss                    -> dist\TokenVault-Manager-Setup.exe

  Steps 2, 5 and 6 need Inno Setup 6.3+ (ISCC.exe). When it is missing the script still
  builds the PyInstaller outputs, says clearly what was skipped and exits with code 3.

.PARAMETER DevTemplate
  Only when Inno Setup is unavailable: copy the raw adapter exe in as the "template" so the
  Manager's --selftest --require-template and the smoke test can run. NOT a shippable
  template (a PyInstaller onefile exe does not survive appended payload bytes).

.PARAMETER Python
  Python with pyinstaller + the project dependencies (default: .venv, else "python").

.PARAMETER SkipWebView2Download
  Do not try to download the WebView2 bootstrapper (the Manager installer then only prints a
  hint when the runtime is missing).

.EXAMPLE
  powershell -File packaging\build.ps1
#>
[CmdletBinding()]
param(
  [switch]$DevTemplate,
  [string]$Python,
  [switch]$SkipWebView2Download
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Pkg = Join-Path $Root 'packaging'
$Dist = Join-Path $Root 'dist'
$Work = Join-Path $Root 'build'
Set-Location $Root

if (-not $Python) {
  $venv = Join-Path $Root '.venv\Scripts\python.exe'
  $Python = if (Test-Path $venv) { $venv } else { 'python' }
}
# Always build from THIS checkout's sources, even if another copy is pip-installed (-e).
$env:PYTHONPATH = Join-Path $Root 'src'

function Invoke-Step([string]$What, [scriptblock]$Cmd) {
  Write-Host "==> $What" -ForegroundColor Cyan
  & $Cmd
  if ($LASTEXITCODE -ne 0) { throw "$What failed (exit $LASTEXITCODE)" }
}

function Find-Iscc {
  $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  $pf86 = ${env:ProgramFiles(x86)}
  foreach ($base in @($pf86, $env:ProgramFiles, "$env:LOCALAPPDATA\Programs")) {
    if (-not $base) { continue }
    $p = Join-Path $base 'Inno Setup 6\ISCC.exe'
    if (Test-Path $p) { return $p }
  }
  return $null
}

$iscc = Find-Iscc
New-Item -ItemType Directory -Force $Dist | Out-Null
# A template left over from an earlier build must never be bundled by mistake.
Remove-Item (Join-Path $Dist 'adapter-setup-template.exe'), (Join-Path $Dist 'TokenVault-Manager-Setup.exe') -ErrorAction SilentlyContinue

# 1. Adapter exe ------------------------------------------------------------------------
Invoke-Step 'PyInstaller: adapter' {
  & $Python -m PyInstaller (Join-Path $Pkg 'adapter.spec') --noconfirm `
    --distpath (Join-Path $Dist 'adapter') --workpath (Join-Path $Work 'pyi-adapter')
}
$adapterExe = Join-Path $Dist 'adapter\TokenVaultAdapter.exe'
if (-not (Test-Path $adapterExe)) { throw "missing $adapterExe" }

# 2. Adapter template installer ---------------------------------------------------------
$template = Join-Path $Dist 'adapter-setup-template.exe'
$skipped = @()
if ($iscc) {
  Invoke-Step 'Inno Setup: adapter template' {
    & $iscc "/DAdapterExe=$adapterExe" "/O$Dist" (Join-Path $Pkg 'adapter-template.iss')
  }
  # OutputBaseFilename is adapter-setup-template -> $template
} elseif ($DevTemplate) {
  Write-Warning 'Inno Setup missing: using the RAW adapter exe as a DEV-ONLY template.'
  Copy-Item $adapterExe $template -Force
  $skipped += 'adapter-template.iss (dev template used instead)'
} else {
  $skipped += 'adapter-template.iss'
}

# 3+4. Manager onedir, template next to the exe -----------------------------------------
Invoke-Step 'PyInstaller: manager' {
  & $Python -m PyInstaller (Join-Path $Pkg 'manager.spec') --noconfirm `
    --distpath (Join-Path $Dist 'manager') --workpath (Join-Path $Work 'pyi-manager')
}
$mgrDir = Join-Path $Dist 'manager\TokenVault-Manager'
if (Test-Path $template) {
  Copy-Item $template (Join-Path $mgrDir 'adapter-setup-template.exe') -Force
} else {
  Write-Warning 'No adapter template: "Create installer" in the Manager will not work.'
}

# 3b. Portable runtime for the "windows-portable" installer target: the Manager looks for
# <exe dir>\portable-runtime (runtime\ + scripts\). Prepared once by build\build-portable.ps1
# (needs internet); without it that installer target reports a clear "runtime missing" error.
$portableSrc = Join-Path $Root 'templates\portable-runtime'
if (Test-Path (Join-Path $portableSrc 'runtime\python.exe')) {
  Copy-Item $portableSrc (Join-Path $mgrDir 'portable-runtime') -Recurse -Force
} else {
  Write-Warning 'templates\portable-runtime is missing (run build\build-portable.ps1 first): this build will NOT offer the windows-portable installer target.'
}

# 5. WebView2 bootstrapper ---------------------------------------------------------------
$redist = Join-Path $Pkg 'redist'
$boot = Join-Path $redist 'MicrosoftEdgeWebview2Setup.exe'
if (-not (Test-Path $boot) -and -not $SkipWebView2Download) {
  try {
    New-Item -ItemType Directory -Force $redist | Out-Null
    Write-Host '==> Downloading the WebView2 bootstrapper (Microsoft)' -ForegroundColor Cyan
    Invoke-WebRequest -UseBasicParsing -OutFile $boot 'https://go.microsoft.com/fwlink/p/?LinkId=2124703'
  } catch {
    Write-Warning "WebView2 bootstrapper download failed: $($_.Exception.Message). The installer will only print a hint if the runtime is missing."
    Remove-Item $boot -ErrorAction SilentlyContinue
  }
}

# Never ship an unverified download: the bootstrapper must be validly signed by Microsoft.
if (Test-Path $boot) {
  $sig = Get-AuthenticodeSignature $boot
  if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
    Write-Warning "WebView2 bootstrapper rejected (signature: $($sig.Status)); not bundling it."
    Remove-Item $boot -Force
  }
}

# 6. Manager installer -------------------------------------------------------------------
if ($iscc) {
  Invoke-Step 'Inno Setup: Manager installer' {
    & $iscc "/DSourceDir=$mgrDir" "/DRedistDir=$redist" "/O$Dist" (Join-Path $Pkg 'manager.iss')
  }
} else {
  $skipped += 'manager.iss'
}

# Summary --------------------------------------------------------------------------------
Write-Host ''
Write-Host 'Outputs:' -ForegroundColor Green
Get-ChildItem $adapterExe, $template, (Join-Path $Dist 'TokenVault-Manager-Setup.exe') -ErrorAction SilentlyContinue |
  ForEach-Object { '  {0}  ({1:N1} MB)' -f $_.FullName, ($_.Length / 1MB) }
'  {0}  (folder)' -f $mgrDir

if ($skipped.Count -gt 0) {
  Write-Host ''
  Write-Warning ('Inno Setup 6 (ISCC.exe) was not found; skipped: ' + ($skipped -join ', '))
  Write-Host 'Install it with:   winget install --id JRSoftware.InnoSetup -e' -ForegroundColor Yellow
  Write-Host 'or from https://jrsoftware.org/isdl.php , then run this script again.' -ForegroundColor Yellow
  exit 3
}
