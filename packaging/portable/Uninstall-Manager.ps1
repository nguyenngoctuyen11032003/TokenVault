<#
  TokenVault Manager - clean uninstall of the portable install (per user).

  Removes: the running Manager (only pythonw.exe / python.exe whose path is under the
  install dir), Desktop + Start-menu shortcuts (.lnk and .cmd), the HKCU Run autostart value,
  the HKCU Uninstall registration (Settings > Installed apps), the Windows Firewall rules
  TokenVault-Manager-* (needs administrator: ONE UAC prompt) and the installed runtime folder.
  The statistics the Manager stored (%APPDATA%\TokenVault\Manager: token history,
  certificate, team enrollment codes) are KEPT unless you answer C / pass -RemoveData.

  Switches: -RemoveData / -KeepData (no question), -Silent (no question, keep data; IT / tests),
  -SkipFirewall, -InstallDir, -StartMenuDir, -DesktopDir, -DataDir, and
  -RegistryRoot (default HKCU:\Software; tests use a throwaway sub key).
  Messages are ASCII on purpose (the console code page garbles accents).
  Exit codes: 0 done (firewall rules left behind are harmless and only reported), 1 something
  else could not be removed (see the checklist).
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'Programs\TokenVault Manager'),
    [string]$StartMenuDir = (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'),
    [string]$DesktopDir = [Environment]::GetFolderPath('Desktop'),
    [string]$DataDir = (Join-Path $env:APPDATA 'TokenVault\Manager'),
    [string]$RegistryRoot = 'HKCU:\Software',
    [switch]$RemoveData,
    [switch]$KeepData,
    [switch]$Silent,
    [switch]$SkipFirewall,
    [switch]$FromRegistry
)
$ErrorActionPreference = 'Continue'
$removed = New-Object System.Collections.ArrayList
$kept = New-Object System.Collections.ArrayList
$failed = New-Object System.Collections.ArrayList
$script:hard = 0   # failures other than the (harmless) firewall rules
function Say($m) { Write-Host $m }
function Remove-Tree($p) {
    for ($i = 0; $i -lt 6; $i++) {
        if (-not (Test-Path -LiteralPath $p)) { return $true }
        Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue
        if (-not (Test-Path -LiteralPath $p)) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return (-not (Test-Path -LiteralPath $p))
}
function Full($p) { try { return [IO.Path]::GetFullPath($p).TrimEnd('\') } catch { return $p } }
$InstallDir = Full $InstallDir
$runKey = "$RegistryRoot\Microsoft\Windows\CurrentVersion\Run"
$uninstKey = "$RegistryRoot\Microsoft\Windows\CurrentVersion\Uninstall\TokenVaultManager"

Say '=== Go cai dat TokenVault Manager ==='
# 0. keep / delete the statistics?
$wipe = $false
if ($RemoveData) { $wipe = $true }
elseif (-not $KeepData -and -not $Silent) {
    Say ''
    $a = Read-Host 'Xoa ca du lieu thong ke da luu (lich su token, chung chi, ma dang ky nhom)? [C/K] (Enter = K, giu lai)'
    if ($a -match '^\s*[CcYy]') { $wipe = $true }
}

# 1. stop the running Manager (and its tray icon): ONLY processes under the install dir
$prefix = ((Join-Path $InstallDir 'runtime').TrimEnd('\') + '\').ToLowerInvariant()
$procs = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath.ToLowerInvariant().StartsWith($prefix) })
foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
if ($procs.Count -gt 0) { [void]$removed.Add("Da tat Manager dang chay ($($procs.Count) tien trinh)"); Start-Sleep -Milliseconds 800 }

# 2. firewall rules (administrator): elevate the signed python of the install
if (-not $SkipFirewall) {
    $rules = @()
    try { $rules = @(Get-NetFirewallRule -DisplayName 'TokenVault-Manager-*' -ErrorAction SilentlyContinue) } catch {}
    $py = Join-Path $InstallDir 'runtime\python.exe'
    if ($rules.Count -gt 0) {
        $cmdline = "`"$py`" -m tokenvault.manager firewall --remove"
        if (Test-Path -LiteralPath $py) {
            Say 'Dang xoa luat tuong lua (Windows se hoi quyen Admin MOT lan: hay bam Yes) ...'
            try {
                $fw = Start-Process -FilePath $py -ArgumentList '-m tokenvault.manager firewall --remove' -Verb RunAs -Wait -PassThru -WindowStyle Hidden
                if ($fw.ExitCode -eq 0) { [void]$removed.Add('Da xoa luat tuong lua TokenVault-Manager-*') }
                else { [void]$failed.Add("Luat tuong lua: buoc xoa tra ma loi $($fw.ExitCode). Co the xoa sau bang lenh (Admin): $cmdline") }
            } catch {
                [void]$failed.Add("Luat tuong lua CHUA xoa (ban tu choi hoac bi chan). Chung vo hai, de nguyen cung duoc. Muon xoa, chay lenh nay bang quyen Admin: $cmdline")
            }
        } else { [void]$failed.Add('Luat tuong lua CHUA xoa (khong tim thay runtime de chay lenh). Chung vo hai.') }
    } else { [void]$kept.Add('Khong co luat tuong lua TokenVault-Manager-* de xoa') }
}

# 3. shortcuts (.lnk and .cmd launchers) on the Desktop, Public desktop and Start menu
$gone = 0
foreach ($d in @($StartMenuDir, $DesktopDir, [Environment]::GetFolderPath('CommonDesktopDirectory'))) {
    foreach ($e in 'lnk', 'cmd') {
        $f = Join-Path $d "TokenVault Manager.$e"
        if (Test-Path -LiteralPath $f) { Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue; if (-not (Test-Path -LiteralPath $f)) { $gone++ } else { $script:hard++; [void]$failed.Add("Khong xoa duoc $f") } }
    }
}
if ($gone -gt 0) { [void]$removed.Add("Da xoa $gone bieu tuong (Desktop / Start menu)") }

# 4. autostart + Settings > Installed apps registration
try {
    if (Get-ItemProperty -LiteralPath $runKey -Name 'TokenVault Manager' -ErrorAction SilentlyContinue) {
        Remove-ItemProperty -LiteralPath $runKey -Name 'TokenVault Manager' -ErrorAction Stop
        [void]$removed.Add('Da bo muc tu khoi dong cung Windows')
    }
} catch { $script:hard++; [void]$failed.Add("Khong bo duoc muc tu khoi dong: $($_.Exception.Message)") }
if (Test-Path -LiteralPath $uninstKey) {
    Remove-Item -LiteralPath $uninstKey -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $uninstKey) { $script:hard++; [void]$failed.Add('Khong xoa duoc muc trong Settings > Installed apps') }
    else { [void]$removed.Add('Da xoa muc trong Settings > Installed apps') }
}

# 5. the installed copy (runtime first; the folder holding the running uninstaller last)
if (Test-Path -LiteralPath $InstallDir) {
    foreach ($sub in 'runtime', 'scripts') { [void](Remove-Tree (Join-Path $InstallDir $sub)) }
    if (Remove-Tree $InstallDir) { [void]$removed.Add("Da xoa thu muc cai dat $InstallDir") }
    else {
        # a file is still in use (usually the .cmd that started this script): finish it detached
        Start-Process -FilePath "$env:SystemRoot\System32\cmd.exe" -WindowStyle Hidden -ArgumentList '/c', ('ping -n 4 127.0.0.1 >nul & rd /s /q "' + $InstallDir + '"')
        [void]$removed.Add("Thu muc cai dat $InstallDir se tu xoa sau vai giay")
    }
} else { [void]$kept.Add('Khong con thu muc cai dat') }

# 6. the statistics
if (Test-Path -LiteralPath $DataDir) {
    if ($wipe) {
        if (Remove-Tree $DataDir) {
            [void]$removed.Add("Da xoa du lieu thong ke $DataDir")
            $par = Split-Path -Parent $DataDir
            if ((Test-Path -LiteralPath $par) -and -not (Get-ChildItem -LiteralPath $par -Force -ErrorAction SilentlyContinue)) { Remove-Item -LiteralPath $par -Force -ErrorAction SilentlyContinue }
        } else { $script:hard++; [void]$failed.Add("Khong xoa het duoc du lieu $DataDir") }
    } else { [void]$kept.Add("Giu lai du lieu thong ke: $DataDir (xoa tay hoac chay lai voi -RemoveData)") }
}

Say ''
Say '--- Ket qua / Summary ---'
foreach ($m in $removed) { Say "  [DA XOA]   $m" }
foreach ($m in $kept) { Say "  [GIU LAI]  $m" }
foreach ($m in $failed) { Write-Host "  [CHUA XOA] $m" -ForegroundColor Yellow }
Say 'TokenVault Manager da duoc go cai dat.'
if ($script:hard -gt 0) { exit 1 }
exit 0
