<#
  Removes the portable Adapter: stops the agent, deletes the HKCU Run value
  TokenVaultAdapter and the runtime folder. Collected data / config stay unless
  -RemoveData is given. -NoAutostart skips the registry (test hook).
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'TokenVault\Adapter'),
    [switch]$RemoveData,
    [switch]$NoAutostart,
    [switch]$Silent   # accepted for IT / tests (the .cmd wrapper then does not pause)
)
$ErrorActionPreference = 'Continue'
$destRuntime = Join-Path $InstallDir 'runtime'
$prefix = ($destRuntime.TrimEnd('\') + '\').ToLowerInvariant()
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath.ToLowerInvariant().StartsWith($prefix) } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 700
if (-not $NoAutostart) {
    Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'TokenVaultAdapter' -ErrorAction SilentlyContinue
}
if (Test-Path -LiteralPath $destRuntime) { Remove-Item -LiteralPath $destRuntime -Recurse -Force -ErrorAction SilentlyContinue }
if ($RemoveData -and (Test-Path -LiteralPath $InstallDir)) { Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction SilentlyContinue }
Write-Host 'TokenVault Adapter removed.'
exit 0
