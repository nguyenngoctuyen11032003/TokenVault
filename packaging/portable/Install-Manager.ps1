<#
  TokenVault Manager - "no-exe" portable install (per user; one elevated step for
  the firewall).

  * copies the signed embeddable Python runtime + TokenVault to
    %LOCALAPPDATA%\Programs\TokenVault Manager\runtime
  * Start menu (and Desktop) shortcut -> pythonw.exe -m tokenvault.manager
    (falls back to a .cmd launcher when shortcuts cannot be created)
  * firewall rules (inbound TCP 47800-47820, UDP 47801, local subnet) through the
    Manager's own helper:  python.exe -m tokenvault.manager firewall --ensure
    -> exactly ONE UAC prompt; skip with -SkipFirewall (adapters then cannot connect
    until an administrator runs it)
  * checks that the Microsoft Edge WebView2 runtime (needed for the Manager window)
    is present and tells you where to get it if not.

  Test switches: -InstallDir, -StartMenuDir, -DesktopDir, -NoDesktop, -SkipFirewall,
  -SkipSignatureCheck. Exit codes: 0 ok, 1 package/copy problem, 6 firewall step failed or
  cancelled (installation itself is complete).
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'Programs\TokenVault Manager'),
    [string]$StartMenuDir = (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'),
    [string]$DesktopDir = [Environment]::GetFolderPath('Desktop'),
    [int]$Port = 47800,
    [switch]$NoDesktop,
    [switch]$SkipFirewall,
    [switch]$SkipSignatureCheck,
    [switch]$NoStart,
    [string]$RegistryRoot = 'HKCU:\Software',   # tests use a throwaway sub key
    [switch]$Silent   # accepted for IT / tests (the .cmd wrapper then does not pause)
)
$ErrorActionPreference = 'Stop'
# Lives in <zip root>\_he-thong\scripts\ ; the runtime is its sibling <zip root>\_he-thong\runtime
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$sys = Split-Path -Parent $here
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

try {
    Get-ChildItem -LiteralPath (Split-Path -Parent $sys) -Recurse -File -Force -ErrorAction SilentlyContinue |
        ForEach-Object { try { Unblock-File -LiteralPath $_.FullName -ErrorAction SilentlyContinue } catch {} }
} catch {}

$srcRuntime = Join-Path $sys 'runtime'
if (-not (Test-Path -LiteralPath (Join-Path $srcRuntime 'pythonw.exe'))) { Fail 1 '_he-thong\runtime\pythonw.exe not found. Extract the whole ZIP first (right-click the .zip, Extract All).' }

if (-not $SkipSignatureCheck) {
    $sig = Get-AuthenticodeSignature -LiteralPath (Join-Path $srcRuntime 'python.exe')
    $cn = ''
    if ($sig.SignerCertificate) { $cn = $sig.SignerCertificate.Subject }
    if ($sig.Status -ne 'Valid' -or $cn -notlike '*Python Software Foundation*') {
        Write-Host "WARNING: python.exe signature is '$($sig.Status)' ($cn). Application Control may refuse to run it." -ForegroundColor Yellow
    } else { Say 'Runtime signature: valid (Python Software Foundation).' }
}

# WebView2 (Evergreen) presence: machine-wide (WOW6432Node) or per-user.
$wv2 = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
$wv2Found = $false
foreach ($k in @("HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$wv2", "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$wv2", "HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$wv2")) {
    try { $v = (Get-ItemProperty -LiteralPath $k -Name pv -ErrorAction Stop).pv; if ($v -and $v -ne '0.0.0.0') { $wv2Found = $true; Say "WebView2 runtime $v found." } } catch {}
}
if (-not $wv2Found) {
    Write-Host 'WARNING: the Microsoft Edge WebView2 runtime was not found. The Manager window needs it.' -ForegroundColor Yellow
    Write-Host '         Get the free "Evergreen Standalone Installer" from https://developer.microsoft.com/microsoft-edge/webview2/ (Windows 11 already includes it).' -ForegroundColor Yellow
}

$destRuntime = Join-Path $InstallDir 'runtime'
try {
    $prefix = ($destRuntime.TrimEnd('\') + '\').ToLowerInvariant()
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.ToLowerInvariant().StartsWith($prefix) } |
        ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {} }
    Start-Sleep -Milliseconds 700
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    if (Test-Path -LiteralPath $destRuntime) { Remove-Item -LiteralPath $destRuntime -Recurse -Force }
    Say "Copying to $destRuntime ..."
    Copy-Item -LiteralPath $srcRuntime -Destination $destRuntime -Recurse -Force
} catch { Fail 1 "cannot copy to ${InstallDir}: $($_.Exception.Message)" }
# adapter package scripts: the installed Manager needs them to build "windows-portable" packages
$srcScripts = $here
$destScripts = Join-Path $InstallDir 'scripts'
if (Test-Path -LiteralPath $srcScripts) {
    try {
        if (Test-Path -LiteralPath $destScripts) { Remove-Item -LiteralPath $destScripts -Recurse -Force }
        Copy-Item -LiteralPath $srcScripts -Destination $destScripts -Recurse -Force
    } catch { Write-Host "WARNING: could not copy the adapter package scripts: $($_.Exception.Message)" -ForegroundColor Yellow }
}
$pyw = Join-Path $destRuntime 'pythonw.exe'
$py = Join-Path $destRuntime 'python.exe'

# Brand icon shipped inside the copied app folder; the python icon only if it is missing.
$ico = Join-Path $destRuntime 'app\tokenvault\ui\assets\logo\tokenvault.ico'
$iconLocation = if (Test-Path -LiteralPath $ico) { $ico } else { "$pyw,0" }

function New-Launcher($dir, $name) {
    New-Item -ItemType Directory -Force -Path $dir -ErrorAction Stop | Out-Null
    $lnk = Join-Path $dir "$name.lnk"
    try {
        $sh = New-Object -ComObject WScript.Shell
        $s = $sh.CreateShortcut($lnk)
        $s.TargetPath = $pyw
        $s.Arguments = '-m tokenvault.manager'
        $s.WorkingDirectory = $destRuntime
        $s.IconLocation = $iconLocation
        $s.Description = 'TokenVault Manager'
        $s.Save()
        return $lnk
    } catch {
        $cmd = Join-Path $dir "$name.cmd"
        Set-Content -LiteralPath $cmd -Encoding ASCII -Value "@echo off`r`nstart `"`" `"$pyw`" -m tokenvault.manager"
        return $cmd
    }
}
function Try-Launcher($dir) { try { return (New-Launcher $dir 'TokenVault Manager') } catch { return $null } }
# the uninstaller next to the program (Windows Settings > Installed apps calls it)
$goSrc = Join-Path (Split-Path -Parent $sys) '3-GO-CAI-MANAGER.cmd'
$goDst = Join-Path $InstallDir '3-GO-CAI-MANAGER.cmd'
if (Test-Path -LiteralPath $goSrc) { Copy-Item -LiteralPath $goSrc -Destination $goDst -Force }
try {
    if (Test-Path -LiteralPath $goDst) {
        Register-Uninstall 'TokenVaultManager' 'TokenVault Manager' ('cmd.exe /c ""' + $goDst + '""') $ico $InstallDir
    }
} catch { Write-Host "WARNING: could not register in Settings > Installed apps: $($_.Exception.Message)" -ForegroundColor Yellow }
$made = @()
$startLnk = Try-Launcher $StartMenuDir
if ($startLnk) { $made += $startLnk }
if (-not $NoDesktop) {
    # Desktop may be redirected (OneDrive): $DesktopDir comes from GetFolderPath('Desktop').
    $deskLnk = Try-Launcher $DesktopDir
    if (-not $deskLnk -and -not $PSBoundParameters.ContainsKey('DesktopDir')) {
        $deskLnk = Try-Launcher ([Environment]::GetFolderPath('CommonDesktopDirectory'))  # Public desktop, last resort
    }
    if ($deskLnk) { $made += $deskLnk } else { Write-Host 'WARNING: could not create the Desktop shortcut.' -ForegroundColor Yellow }
}
if (-not $startLnk) { Write-Host 'WARNING: could not create the Start menu shortcut.' -ForegroundColor Yellow }
$made | ForEach-Object { Say "Shortcut: $_" }
# Tell Explorer right away so the icons show up without a restart / icon-cache refresh.
try {
    Add-Type -Namespace TV -Name Shell -MemberDefinition '[System.Runtime.InteropServices.DllImport("shell32.dll")] public static extern void SHChangeNotify(int e, uint f, System.IntPtr a, System.IntPtr b);'
    [TV.Shell]::SHChangeNotify(0x08000000, 0, [IntPtr]::Zero, [IntPtr]::Zero)  # SHCNE_ASSOCCHANGED
} catch {}

$rc = 0
if (-not $SkipFirewall) {
    Say 'Adding firewall rules (one Windows permission prompt) ...'
    try {
        $fw = Start-Process -FilePath $py -ArgumentList "-m tokenvault.manager firewall --ensure --port $Port" -Verb RunAs -Wait -PassThru -WindowStyle Hidden
        if ($fw.ExitCode -eq 0) { Say 'Firewall rules added.' }
        else { Write-Host "WARNING: firewall step exited with code $($fw.ExitCode). Adapters on other PCs may not connect." -ForegroundColor Yellow; $rc = 6 }
    } catch {
        Write-Host 'WARNING: the firewall step was cancelled or blocked. Adapters on other PCs cannot connect until an administrator runs:' -ForegroundColor Yellow
        Write-Host "         `"$py`" -m tokenvault.manager firewall --ensure --port $Port" -ForegroundColor Yellow
        $rc = 6
    }
}
Say 'TokenVault Manager installed. Start it from the Start menu.'
if (-not $NoStart -and -not $Silent) {
    try { Start-Process -FilePath $pyw -ArgumentList '-m tokenvault.manager' -WorkingDirectory $destRuntime } catch {}
}
exit $rc
