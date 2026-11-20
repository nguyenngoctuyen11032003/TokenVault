<#
  TokenVault Adapter - "no-exe" portable installer (per user, NO admin rights).

  Copies the signed embeddable Python runtime + TokenVault to
  %LOCALAPPDATA%\TokenVault\Adapter\runtime, then runs
      pythonw.exe -m tokenvault.adapter setup --from <payload>
  (the Adapter shows its consent screen, stores the enrollment secret with DPAPI,
  registers HKCU\...\Run -> pythonw.exe -m tokenvault.adapter.main run, starts the agent).

  Test / IT switches: -InstallDir, -DataDir, -ClaudeDir, -NoAutostart, -NoStart,
  -Silent -Accept (no consent window; only when the user already consented elsewhere).
  Exit codes: 0 ok, 1 package problem, 2 no payload, 3 consent declined,
  4 consent could not be shown, 5 DPAPI error, 10 runtime blocked / could not start.
#>
[CmdletBinding()]
param(
    [string]$Payload,
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'TokenVault\Adapter'),
    [string]$DataDir,
    [string]$ClaudeDir,
    [switch]$NoAutostart,
    [switch]$NoStart,
    [switch]$Silent,
    [switch]$Accept,
    [switch]$SkipSignatureCheck
)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Payload) { $Payload = Join-Path $here 'tokenvault-config.bin' }

function Say($m) { Write-Host $m }
function Fail($code, $m) { Write-Host "ERROR: $m" -ForegroundColor Red; exit $code }

# 1. Mark-of-the-Web: a ZIP downloaded in a browser tags every extracted file.
try {
    Get-ChildItem -LiteralPath $here -Recurse -File -Force -ErrorAction SilentlyContinue |
        ForEach-Object { try { Unblock-File -LiteralPath $_.FullName -ErrorAction SilentlyContinue } catch {} }
} catch {}

$srcRuntime = Join-Path $here 'runtime'
$srcPy = Join-Path $srcRuntime 'pythonw.exe'
if (-not (Test-Path -LiteralPath $srcPy)) { Fail 1 "runtime\pythonw.exe not found next to this script. Extract the whole ZIP first." }
if (-not (Test-Path -LiteralPath $Payload)) { Fail 2 "payload not found: $Payload" }

# 2. The interpreter must be the one signed by the Python Software Foundation.
if (-not $SkipSignatureCheck) {
    $sig = Get-AuthenticodeSignature -LiteralPath (Join-Path $srcRuntime 'python.exe')
    $cn = ''
    if ($sig.SignerCertificate) { $cn = $sig.SignerCertificate.Subject }
    if ($sig.Status -ne 'Valid' -or $cn -notlike '*Python Software Foundation*') {
        Write-Host "WARNING: python.exe signature is '$($sig.Status)' ($cn). Application Control may refuse to run it." -ForegroundColor Yellow
    } else { Say 'Runtime signature: valid (Python Software Foundation).' }
}

# 2b. Consent FIRST, from the freshly extracted runtime, before the old agent is stopped or any
# file is replaced: declining leaves an existing install running untouched.
$consented = $false
if (-not $Silent) {
    $cargs = @('-m', 'tokenvault.adapter', 'consent', '--from', ('"' + (Resolve-Path -LiteralPath $Payload).Path + '"'))
    try {
        $c = Start-Process -FilePath $srcPy -ArgumentList ($cargs -join ' ') -WorkingDirectory $srcRuntime -PassThru -WindowStyle Hidden
        while (-not $c.HasExited) { Start-Sleep -Milliseconds 300 }
    } catch { Fail 10 "Windows refused to start $srcPy : $($_.Exception.Message)" }
    switch ($c.ExitCode) {
        0 { $consented = $true }
        2 { Fail 2 'The package carries no valid configuration payload.' }
        3 { Say 'Installation cancelled: you declined the consent screen. Nothing was changed and nothing was sent.'; exit 3 }
        4 { Fail 4 'The consent screen could not be shown. Nothing was changed.' }
        default { Fail 10 "consent exited with code $($c.ExitCode)." }
    }
}

# 3. Stop an agent started from a previous install (its files would be locked).
$destRuntime = Join-Path $InstallDir 'runtime'
try {
    $prefix = ($destRuntime.TrimEnd('\') + '\').ToLowerInvariant()
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.ToLowerInvariant().StartsWith($prefix) } |
        ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {} }
    Start-Sleep -Milliseconds 700
} catch {}

# 4. Copy the runtime (per user folder; replaces an older copy).
try {
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    if (Test-Path -LiteralPath $destRuntime) { Remove-Item -LiteralPath $destRuntime -Recurse -Force }
    Say "Copying to $destRuntime ..."
    Copy-Item -LiteralPath $srcRuntime -Destination $destRuntime -Recurse -Force
} catch { Fail 1 "cannot copy to ${InstallDir}: $($_.Exception.Message)" }
$py = Join-Path $destRuntime 'pythonw.exe'

# 5. Adapter setup (consent screen, DPAPI, config.json, HKCU Run, start).
$argList = @('-m', 'tokenvault.adapter', 'setup', '--from', ('"' + (Resolve-Path -LiteralPath $Payload).Path + '"'))
if ($DataDir) { New-Item -ItemType Directory -Force -Path $DataDir | Out-Null; $argList += @('--data-dir', ('"' + (Resolve-Path -LiteralPath $DataDir).Path + '"')) }
if ($ClaudeDir) { $argList += @('--claude-dir', ('"' + $ClaudeDir + '"')) }
if ($NoAutostart) { $argList += '--no-autostart' }
if ($NoStart) { $argList += '--no-start' }
if ($Silent -or $consented) { $argList += '--silent' }
if ($Accept -or $consented) { $argList += '--accept' }  # consent was already given above
Say 'Starting setup ...'
try {
    $p = Start-Process -FilePath $py -ArgumentList ($argList -join ' ') -WorkingDirectory $destRuntime -PassThru -WindowStyle Hidden
    # NOT Start-Process -Wait / WaitForExit(): they can also block on the detached agent that setup starts.
    while (-not $p.HasExited) { Start-Sleep -Milliseconds 300 }
} catch { Fail 10 "Windows refused to start $py : $($_.Exception.Message)" }
switch ($p.ExitCode) {
    0 { Say 'TokenVault Adapter installed and running (tray icon).'; exit 0 }
    2 { Fail 2 'The package carries no valid configuration payload.' }
    3 { Say 'Installation cancelled: you declined the consent screen. Nothing was sent.'; exit 3 }
    4 { Fail 4 'The consent screen could not be shown.' }
    5 { Fail 5 'Windows DPAPI could not protect the secret; nothing was written.' }
    default { Fail 10 "setup exited with code $($p.ExitCode). See %LOCALAPPDATA%\TokenVault\Adapter\adapter.log" }
}
