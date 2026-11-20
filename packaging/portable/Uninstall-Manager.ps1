<#
  Removes the portable Manager install (runtime copy + shortcuts). The Manager's data
  (%APPDATA%\TokenVault\Manager) and firewall rules are left alone.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'Programs\TokenVault Manager'),
    [string]$StartMenuDir = (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'),
    [string]$DesktopDir = [Environment]::GetFolderPath('Desktop'),
    [switch]$Silent   # accepted for IT / tests (the .cmd wrapper then does not pause)
)
$ErrorActionPreference = 'Continue'
$destRuntime = Join-Path $InstallDir 'runtime'
$prefix = ($destRuntime.TrimEnd('\') + '\').ToLowerInvariant()
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath.ToLowerInvariant().StartsWith($prefix) } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 700
foreach ($d in @($StartMenuDir, $DesktopDir, [Environment]::GetFolderPath('CommonDesktopDirectory'))) {
    foreach ($e in 'lnk', 'cmd') { Remove-Item -LiteralPath (Join-Path $d "TokenVault Manager.$e") -Force -ErrorAction SilentlyContinue }
}
if (Test-Path -LiteralPath $InstallDir) { Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction SilentlyContinue }
Write-Host 'TokenVault Manager removed.'
exit 0
