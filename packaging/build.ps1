<#
.SYNOPSIS
  Builds the TokenVault installers: adapter -> adapter template -> portable runtime -> Manager installer.

.DESCRIPTION
  The Manager installer ships NO unsigned exe of its own (Smart App Control / Application
  Control blocks those, error 4551): it installs the portable runtime - the Authenticode-signed
  embeddable Python + wheels + TokenVault sources - and starts the Manager with
  runtime\pythonw.exe -m tokenvault.manager (see manager.iss).

  1. PyInstaller: Adapter (onefile, windowed)    -> dist\adapter\TokenVaultAdapter.exe
  2. Inno Setup:  adapter-template.iss           -> dist\adapter-setup-template.exe  (UNSIGNED)
  3. Portable runtime: templates\portable-runtime is built with build\build-portable.ps1 when it
     is missing (needs internet once; offline: -EmbedZip / -WheelDir are passed through), and
     its TokenVault sources are always refreshed from this checkout (-RefreshOnly)
  4. WebView2 bootstrapper (downloaded once into packaging\redist, git-ignored)
  5. Inno Setup:  manager.iss                    -> dist\TokenVault-Manager-Setup.exe
  (optional, -WithManagerExe: PyInstaller Manager onedir -> dist\manager\TokenVault-Manager\,
  for the smoke tests only; it is NOT part of the installer)

  Steps 2, 4 and 5 need Inno Setup 6.3+ (ISCC.exe). When it is missing the script still
  builds what it can, says clearly what was skipped and exits with code 3.

.PARAMETER DevTemplate
  Only when Inno Setup is unavailable: copy the raw adapter exe in as the "template" so the
  Manager's --selftest --require-template and the smoke test can run. NOT a shippable
  template (a PyInstaller onefile exe does not survive appended payload bytes).

.PARAMETER Python
  Python with pyinstaller + the project dependencies (default: .venv, else "python").

.PARAMETER PortableRuntime
  Folder with the prepared portable runtime (default <repo>\templates\portable-runtime).

.PARAMETER EmbedZip
  Offline build of the portable runtime: local python-3.12.x-embed-amd64.zip (passed to
  build-portable.ps1; only used when the runtime has to be built).

.PARAMETER WheelDir
  Offline build of the portable runtime: folder of cp312-win_amd64 wheels (idem).

.PARAMETER WithManagerExe
  Also build the PyInstaller Manager exe (dist\manager\TokenVault-Manager) - not shipped.

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
  [string]$DistDir,   # output folder (default <repo>\dist); use another one while a built Manager is running
  [switch]$SkipWebView2Download,
  [string]$PortableRuntime,
  [string]$EmbedZip,
  [string]$WheelDir,
  [switch]$WithManagerExe
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Pkg = Join-Path $Root 'packaging'
$Dist = if ($DistDir) { $DistDir } else { Join-Path $Root 'dist' }
$Work = Join-Path $Root 'build'
if (-not $PortableRuntime) { $PortableRuntime = Join-Path $Root 'templates\portable-runtime' }
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

# 3. Portable runtime (signed python + wheels + sources): the content of the Manager installer.
$mgrDir = Join-Path $Dist 'manager\TokenVault-Manager'
$bp = Join-Path $Root 'build\build-portable.ps1'
if (-not (Test-Path (Join-Path $PortableRuntime 'runtime\python.exe'))) {
  Invoke-Step 'Portable runtime: build (build-portable.ps1)' {
    $a = @{ Python = $Python; OutDir = $PortableRuntime; NoManagerZip = $true }
    if ($EmbedZip) { $a.EmbedZip = $EmbedZip }
    if ($WheelDir) { $a.WheelDir = $WheelDir }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $bp @(($a.GetEnumerator() | ForEach-Object {
      if ($_.Value -is [bool]) { "-$($_.Key)" } else { "-$($_.Key)"; "$($_.Value)" } }))
  }
}
# Never ship stale sources: always re-copy src\tokenvault + scripts into the runtime (offline, fast).
Invoke-Step 'Portable runtime: refresh sources' {
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $bp -Python $Python -OutDir $PortableRuntime -RefreshOnly
}
$pyExe = Join-Path $PortableRuntime 'runtime\python.exe'
$sig = Get-AuthenticodeSignature $pyExe
if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
  throw "The runtime's python.exe is not validly signed by the Python Software Foundation ($($sig.Status)): the installed Manager would be blocked by Application Control."
}

# 3b. (optional, not shipped) PyInstaller Manager exe for the smoke tests.
if ($WithManagerExe) {
  Invoke-Step 'PyInstaller: manager (not part of the installer)' {
    & $Python -m PyInstaller (Join-Path $Pkg 'manager.spec') --noconfirm `
      --distpath (Join-Path $Dist 'manager') --workpath (Join-Path $Work 'pyi-manager')
  }
  if (Test-Path $template) { Copy-Item $template (Join-Path $mgrDir 'adapter-setup-template.exe') -Force }
}
if (-not (Test-Path $template)) {
  Write-Warning 'No adapter template: the "Windows .exe" employee target of the Manager will not work (the portable ZIP target does).'
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
    & $iscc "/DRuntimeDir=$PortableRuntime" "/DTemplateExe=$template" "/DRedistDir=$redist" "/O$Dist" (Join-Path $Pkg 'manager.iss')
  }
} else {
  $skipped += 'manager.iss'
}

# Summary --------------------------------------------------------------------------------
Write-Host ''
Write-Host 'Outputs:' -ForegroundColor Green
Get-ChildItem $adapterExe, $template, (Join-Path $Dist 'TokenVault-Manager-Setup.exe') -ErrorAction SilentlyContinue |
  ForEach-Object { '  {0}  ({1:N1} MB)' -f $_.FullName, ($_.Length / 1MB) }
if ($WithManagerExe) { '  {0}  (folder, smoke tests only)' -f $mgrDir }

if ($skipped.Count -gt 0) {
  Write-Host ''
  Write-Warning ('Inno Setup 6 (ISCC.exe) was not found; skipped: ' + ($skipped -join ', '))
  Write-Host 'Install it with:   winget install --id JRSoftware.InnoSetup -e' -ForegroundColor Yellow
  Write-Host 'or from https://jrsoftware.org/isdl.php , then run this script again.' -ForegroundColor Yellow
  exit 3
}
