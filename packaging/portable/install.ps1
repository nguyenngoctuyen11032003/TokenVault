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
    [switch]$SkipSignatureCheck,
    [string]$RegistryRoot = 'HKCU:\Software'   # tests use a throwaway sub key
)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Payload) { $Payload = Join-Path $here 'tokenvault-config.bin' }

function Say($m) { Write-Host $m }

# Settings > Installed apps registration (HKCU, no admin); removed again by the uninstaller.
function Register-Uninstall($name, $display, $unCmd, $icon, $loc) {
    $ver = ''
    foreach ($vf in @((Join-Path $here 'version.txt'), (Join-Path (Split-Path -Parent $here) 'version.txt'))) {
        if (-not $ver -and (Test-Path -LiteralPath $vf)) { $ver = (Get-Content -LiteralPath $vf -TotalCount 1).Trim() }
    }
    $k = "$RegistryRoot\Microsoft\Windows\CurrentVersion\Uninstall\$name"
    New-Item -Path $k -Force | Out-Null
    Set-ItemProperty -Path $k -Name 'DisplayName' -Value $display
    if ($ver) { Set-ItemProperty -Path $k -Name 'DisplayVersion' -Value $ver }
    Set-ItemProperty -Path $k -Name 'Publisher' -Value 'TokenVault'
    if ($icon -and (Test-Path -LiteralPath $icon)) { Set-ItemProperty -Path $k -Name 'DisplayIcon' -Value $icon }
    Set-ItemProperty -Path $k -Name 'InstallLocation' -Value $loc
    Set-ItemProperty -Path $k -Name 'UninstallString' -Value $unCmd
    Set-ItemProperty -Path $k -Name 'NoModify' -Value 1 -Type DWord
    Set-ItemProperty -Path $k -Name 'NoRepair' -Value 1 -Type DWord
}
function Fail($code, $m) { Write-Host "ERROR: $m" -ForegroundColor Red; exit $code }

# 1. Mark-of-the-Web: a ZIP downloaded in a browser tags every extracted file.
try {
    Get-ChildItem -LiteralPath (Split-Path -Parent $here) -Recurse -File -Force -ErrorAction SilentlyContinue |
        ForEach-Object { try { Unblock-File -LiteralPath $_.FullName -ErrorAction SilentlyContinue } catch {} }
} catch {}

$srcRuntime = Join-Path $here 'runtime'
$srcPy = Join-Path $srcRuntime 'pythonw.exe'
if (-not (Test-Path -LiteralPath $srcPy)) { Fail 1 "runtime\pythonw.exe not found in _he-thong. Extract the whole ZIP first (right-click the .zip, Extract All)." }
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
    0 {
        # the uninstaller next to the program + Settings > Installed apps entry
        try {
            Copy-Item -LiteralPath (Join-Path $here 'uninstall.ps1') -Destination (Join-Path $InstallDir 'uninstall.ps1') -Force
            $vf = Join-Path $here 'version.txt'
            if (Test-Path -LiteralPath $vf) { Copy-Item -LiteralPath $vf -Destination (Join-Path $InstallDir 'version.txt') -Force }
            $goSrc = Join-Path (Split-Path -Parent $here) '2-GO-CAI-DAT.cmd'
            $goDst = Join-Path $InstallDir '2-GO-CAI-DAT.cmd'
            if (Test-Path -LiteralPath $goSrc) {
                Copy-Item -LiteralPath $goSrc -Destination $goDst -Force
                Register-Uninstall 'TokenVaultAdapter' 'TokenVault Adapter' ('cmd.exe /c ""' + $goDst + '""') (Join-Path $destRuntime 'app\tokenvault\ui\assets\logo\tokenvault.ico') $InstallDir
            }
        } catch { Write-Host "WARNING: could not register in Settings > Installed apps: $($_.Exception.Message)" -ForegroundColor Yellow }
        Say 'TokenVault Adapter installed and running (tray icon).'; exit 0
    }
    2 { Fail 2 'The package carries no valid configuration payload.' }
    3 { Say 'Installation cancelled: you declined the consent screen. Nothing was sent.'; exit 3 }
    4 { Fail 4 'The consent screen could not be shown.' }
    5 { Fail 5 'Windows DPAPI could not protect the secret; nothing was written.' }
    default { Fail 10 "setup exited with code $($p.ExitCode). See %LOCALAPPDATA%\TokenVault\Adapter\adapter.log" }
}
