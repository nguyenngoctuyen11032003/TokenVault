<#
  TokenVault Adapter - clean uninstall of the portable install (per user, no admin).

  Removes: the running agent (only pythonw.exe / python.exe under the install dir), the HKCU Run
  autostart value TokenVaultAdapter, the HKCU Uninstall registration (Settings > Installed apps)
  and the installed runtime. The local data (config.json, outbox.db, device.key, enroll.secret
  and the DPAPI-protected secrets, adapter.log in %LOCALAPPDATA%\TokenVault\Adapter) is KEPT
  unless you answer C / pass -RemoveData, which wipes the whole folder.
  Removing the Adapter does not delete the device on the Manager: revoke / reset it there.

  Switches: -RemoveData / -KeepData (no question), -Silent (no question, keep data; IT / tests),
  -NoAutostart (test hook: do not touch the Run value), -InstallDir and
  -RegistryRoot (default HKCU:\Software; tests use a throwaway sub key).
  Messages are ASCII on purpose (the console code page garbles accents).
  Exit codes: 0 done, 1 something could not be removed (see the checklist).
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'TokenVault\Adapter'),
    [string]$RegistryRoot = 'HKCU:\Software',
    [switch]$RemoveData,
    [switch]$KeepData,
    [switch]$NoAutostart,
    [switch]$Silent,
    [switch]$FromRegistry
)
$ErrorActionPreference = 'Continue'
$removed = New-Object System.Collections.ArrayList
$kept = New-Object System.Collections.ArrayList
$failed = New-Object System.Collections.ArrayList
$script:hard = 0
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
$uninstKey = "$RegistryRoot\Microsoft\Windows\CurrentVersion\Uninstall\TokenVaultAdapter"

Say '=== Go cai dat TokenVault Adapter ==='
$wipe = $false
if ($RemoveData) { $wipe = $true }
elseif (-not $KeepData -and -not $Silent) {
    Say ''
    $a = Read-Host 'Xoa ca du lieu cuc bo (cau hinh, hang doi gui, khoa thiet bi, ma bi mat da ma hoa DPAPI)? [C/K] (Enter = K, giu lai)'
    if ($a -match '^\s*[CcYy]') { $wipe = $true }
}

# 1. stop the agent: ONLY processes under the install dir
$prefix = ((Join-Path $InstallDir 'runtime').TrimEnd('\') + '\').ToLowerInvariant()
$procs = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath.ToLowerInvariant().StartsWith($prefix) })
foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
if ($procs.Count -gt 0) { [void]$removed.Add("Da tat Adapter dang chay ($($procs.Count) tien trinh)"); Start-Sleep -Milliseconds 800 }

# 2. autostart + Settings > Installed apps registration
if (-not $NoAutostart) {
    try {
        if (Get-ItemProperty -LiteralPath $runKey -Name 'TokenVaultAdapter' -ErrorAction SilentlyContinue) {
            Remove-ItemProperty -LiteralPath $runKey -Name 'TokenVaultAdapter' -ErrorAction Stop
            [void]$removed.Add('Da bo muc tu khoi dong cung Windows')
        }
    } catch { $script:hard++; [void]$failed.Add("Khong bo duoc muc tu khoi dong: $($_.Exception.Message)") }
}
if (Test-Path -LiteralPath $uninstKey) {
    Remove-Item -LiteralPath $uninstKey -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $uninstKey) { $script:hard++; [void]$failed.Add('Khong xoa duoc muc trong Settings > Installed apps') }
    else { [void]$removed.Add('Da xoa muc trong Settings > Installed apps') }
}

# 3. the installed runtime (+ uninstaller copy). The .cmd that may be running us now goes last:
#    a detached cmd removes it (and, with -RemoveData, the folder) a few seconds later.
if (Test-Path -LiteralPath $InstallDir) {
    $wrapper = '2-GO-CAI-DAT.cmd'
    $rest = @(Get-ChildItem -LiteralPath $InstallDir -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne $wrapper })
    if ($wipe) {
        foreach ($c in $rest) { [void](Remove-Tree $c.FullName) }
        [void]$removed.Add("Da xoa TOAN BO du lieu cuc bo va chuong trinh: $InstallDir")
    } else {
        if (Remove-Tree (Join-Path $InstallDir 'runtime')) { [void]$removed.Add('Da xoa chuong trinh (runtime)') }
        else { $script:hard++; [void]$failed.Add('Khong xoa het duoc thu muc runtime (dang bi dung?)') }
        foreach ($f in 'uninstall.ps1', 'version.txt', 'tokenvault.ico') {
            $fp = Join-Path $InstallDir $f
            if (Test-Path -LiteralPath $fp) { Remove-Item -LiteralPath $fp -Force -ErrorAction SilentlyContinue }
        }
        [void]$kept.Add("Giu lai du lieu cuc bo: $InstallDir (cau hinh, hang doi, khoa thiet bi; xoa tay hoac chay lai voi -RemoveData)")
    }
    $wp = Join-Path $InstallDir $wrapper
    if (Test-Path -LiteralPath $wp) {
        $tail = if ($wipe) { 'rd /s /q "' + $InstallDir + '"' } else { 'del /f /q "' + $wp + '"' }
        Start-Process -FilePath "$env:SystemRoot\System32\cmd.exe" -WindowStyle Hidden -ArgumentList '/c', ('ping -n 4 127.0.0.1 >nul & ' + $tail)
    } elseif ($wipe) { [void](Remove-Tree $InstallDir) }
} else { [void]$kept.Add('Khong con thu muc cai dat') }

Say ''
Say '--- Ket qua / Summary ---'
foreach ($m in $removed) { Say "  [DA XOA]   $m" }
foreach ($m in $kept) { Say "  [GIU LAI]  $m" }
foreach ($m in $failed) { Write-Host "  [CHUA XOA] $m" -ForegroundColor Yellow }
Say 'Luu y: go Adapter KHONG xoa thiet bi tren Manager; nguoi quan ly co the thu hoi / dat lai thiet bi o do.'
Say 'TokenVault Adapter removed.'
if ($script:hard -gt 0) { exit 1 }
exit 0
