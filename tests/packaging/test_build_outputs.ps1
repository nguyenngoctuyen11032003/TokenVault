<#
.SYNOPSIS
  Smoke test of the packaged builds (run packaging\build.ps1 first).

.DESCRIPTION
  Checks, in order (each result is PASS / FAIL / SKIP, with the reason for a skip):
   1. build outputs exist and sizes are sane (PyInstaller exes always; the two Inno setup
      files when Inno Setup was available)
   2. adapter exe, run directly with no payload: exits 2 ("no payload")
   3. manager exe  --selftest --require-template  exits 0
   4. a team installer is built through the Manager's packager from the BUILT adapter exe,
      then the adapter `setup --from <team installer> --silent --accept --no-start
      --no-autostart` runs with a temp data dir and temp HOME and must write config.json
      (team, port, fingerprint); no secret in config.json; HKCU Run untouched
   4b. (Inno present, no real adapter installed for this user) team installer built from the
      real Inno template, silent install with /ADAPTERARGS, config.json asserted, uninstall
   5. (installers present) Manager installer silent-installs into a temp
      folder (/VERYSILENT /DIR=, no firewall / WebView2 side effects), `--selftest` of the
      INSTALLED exe returns 0, then it is uninstalled again

  Nothing is signed; Windows SmartScreen / Application Control may block unsigned exes.
  When the OS refuses to start an exe ("Application Control policy has blocked this
  file"), the affected steps are reported as BLOCKED (not PASS) and the script exits 4
  unless -AllowBlocked is given.

.PARAMETER Python
  Interpreter with the project dependencies (default: .venv, else "python").
.PARAMETER AllowBlocked
  Treat BLOCKED steps as skipped (exit 0 if nothing failed).
#>
[CmdletBinding()]
param([string]$Python, [switch]$AllowBlocked)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Dist = Join-Path $Root 'dist'
if (-not $Python) {
  $venv = Join-Path $Root '.venv\Scripts\python.exe'
  $Python = if (Test-Path $venv) { $venv } else { 'python' }
}
$env:PYTHONPATH = Join-Path $Root 'src'

$results = New-Object System.Collections.Generic.List[object]
function Add-Result($name, $status, $detail = '') {
  $results.Add([pscustomobject]@{ Step = $name; Status = $status; Detail = $detail })
  $color = @{ PASS = 'Green'; FAIL = 'Red'; SKIP = 'Yellow'; BLOCKED = 'Magenta' }[$status]
  Write-Host ('{0,-8} {1}  {2}' -f $status, $name, $detail) -ForegroundColor $color
}

# Run an exe and return @{ Code; Out; Blocked }. Blocked = the OS refused to start it.
function Invoke-Exe([string]$Exe, [string[]]$ArgList, [int]$TimeoutSec = 120) {
  $o = [IO.Path]::GetTempFileName(); $e = [IO.Path]::GetTempFileName()
  try {
    $q = $ArgList | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }
    $p = Start-Process -FilePath $Exe -ArgumentList $q -PassThru -NoNewWindow `
      -RedirectStandardOutput $o -RedirectStandardError $e
    $null = $p.Handle  # keep the handle so ExitCode is readable after exit
    if (-not $p.WaitForExit($TimeoutSec * 1000)) { $p.Kill(); return @{ Code = -1; Out = 'timeout'; Blocked = $false } }
    return @{ Code = $p.ExitCode; Out = ((Get-Content $o -Raw) + (Get-Content $e -Raw)); Blocked = $false }
  } catch {
    $msg = $_.Exception.Message
    $blocked = $msg -match 'Application Control|blocked|AppLocker|Device Guard|not allowed'
    return @{ Code = -2; Out = $msg; Blocked = $blocked }
  } finally { Remove-Item $o, $e -ErrorAction SilentlyContinue }
}

$adapterExe = Join-Path $Dist 'adapter\TokenVaultAdapter.exe'
$mgrDir = Join-Path $Dist 'manager\TokenVault-Manager'
$mgrExe = Join-Path $mgrDir 'TokenVault-Manager.exe'
$template = Join-Path $Dist 'adapter-setup-template.exe'
$mgrSetup = Join-Path $Dist 'TokenVault-Manager-Setup.exe'
$saved = @{ UP = $env:USERPROFILE; LA = $env:LOCALAPPDATA }
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('tv-smoke-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force $tmp | Out-Null

try {
  # 1. outputs ---------------------------------------------------------------------------
  foreach ($f in @($adapterExe, $mgrExe, (Join-Path $mgrDir '_internal\tokenvault\ui\index.html'),
                   (Join-Path $mgrDir '_internal\data\pricing.json'),
                   (Join-Path $mgrDir '_internal\tokenvault\manager\schema.sql'))) {
    if (Test-Path $f) { Add-Result "exists $([IO.Path]::GetFileName($f))" PASS ('{0:N1} MB' -f ((Get-Item $f).Length / 1MB)) }
    else { Add-Result "exists $f" FAIL 'missing - run packaging\build.ps1' }
  }
  $haveInno = (Test-Path $template) -and (Test-Path $mgrSetup)
  if ($haveInno) {
    Add-Result 'setup files exist' PASS ('template {0:N1} MB, manager {1:N1} MB' -f ((Get-Item $template).Length / 1MB), ((Get-Item $mgrSetup).Length / 1MB))
  } else {
    Add-Result 'setup files exist' SKIP 'Inno Setup outputs absent (ISCC not installed when build.ps1 ran)'
  }

  # 2. no-payload behaviour ----------------------------------------------------------------
  $r = Invoke-Exe $adapterExe @('setup', '--from', $adapterExe, '--silent', '--accept')
  if ($r.Blocked) { Add-Result 'adapter no-payload exit' BLOCKED $r.Out.Trim() }
  elseif ($r.Code -eq 2) { Add-Result 'adapter no-payload exit' PASS 'exit 2' }
  else { Add-Result 'adapter no-payload exit' FAIL "exit $($r.Code): $($r.Out)" }

  # 3. manager selftest ----------------------------------------------------------------------
  $r = Invoke-Exe $mgrExe @('--selftest', '--require-template')
  if ($r.Blocked) { Add-Result 'manager --selftest --require-template' BLOCKED $r.Out.Trim() }
  elseif ($r.Code -eq 0) { Add-Result 'manager --selftest --require-template' PASS 'exit 0' }
  else { Add-Result 'manager --selftest --require-template' FAIL "exit $($r.Code): $($r.Out)" }

  # 4. team installer + adapter setup in --no-start mode --------------------------------------
  $teamDir = Join-Path $tmp 'team'
  $pk = Invoke-Exe $Python @((Join-Path $PSScriptRoot 'make_team_installer.py'), $adapterExe, $teamDir, 'Smoke Team')
  if ($pk.Code -ne 0) { Add-Result 'packager builds team installer' FAIL "exit $($pk.Code): $($pk.Out)" }
  else {
    $info = ($pk.Out.Trim() -split "`n" | Where-Object { $_ -like '{*' } | Select-Object -Last 1) | ConvertFrom-Json
    Add-Result 'packager builds team installer' PASS ('{0} ({1:N1} MB)' -f (Split-Path $info.path -Leaf), ((Get-Item $info.path).Length / 1MB))
    $home1 = Join-Path $tmp 'home'; $data = Join-Path $tmp 'adapter-data'
    New-Item -ItemType Directory -Force $home1 | Out-Null
    $env:USERPROFILE = $home1; $env:LOCALAPPDATA = Join-Path $home1 'AppData\Local'
    # Run the UNMODIFIED built exe against the team file: this is exactly what the Inno
    # template does ({app}\TokenVaultAdapter.exe setup --from "{srcexe}").
    $runBefore = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -ErrorAction SilentlyContinue).TokenVaultAdapter
    $r = Invoke-Exe $adapterExe @('setup', '--from', $info.path, '--silent', '--accept', '--no-start', '--no-autostart', '--data-dir', $data)
    if ($r.Blocked) { Add-Result 'adapter setup --no-start writes config' BLOCKED $r.Out.Trim() }
    elseif ($r.Code -ne 0) { Add-Result 'adapter setup --no-start writes config' FAIL "exit $($r.Code): $($r.Out)" }
    else {
      $cfgFile = Join-Path $data 'config.json'
      $cfg = if (Test-Path $cfgFile) { Get-Content $cfgFile -Raw | ConvertFrom-Json } else { $null }
      $runAfter = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -ErrorAction SilentlyContinue).TokenVaultAdapter
      if ($null -eq $cfg) { Add-Result 'adapter setup --no-start writes config' FAIL 'config.json missing' }
      elseif ($cfg.team -ne 'Smoke Team' -or $cfg.port -ne $info.port -or $cfg.fingerprint -ne $info.fingerprint) {
        Add-Result 'adapter setup --no-start writes config' FAIL 'config.json does not match the payload'
      } elseif ((Get-Content $cfgFile -Raw) -match 'secret') {
        Add-Result 'adapter setup --no-start writes config' FAIL 'config.json mentions a secret'
      } elseif ($runAfter -ne $runBefore) {
        Add-Result 'adapter setup --no-start writes config' FAIL 'HKCU Run changed despite --no-autostart'
      } else { Add-Result 'adapter setup --no-start writes config' PASS "team=$($cfg.team) port=$($cfg.port)" }
    }
  }

  # 4b. REAL Inno template: team installer from it, silent install, assert config, uninstall ----
  $tplKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{6C1D9B0A-3E5F-4B7A-9D2E-7A41B8F0C5D1}_is1'
  if (-not (Test-Path $template) -or -not $haveInno) { Add-Result 'Inno template installs team config' SKIP 'no Inno template (build.ps1 -DevTemplate only copies the raw exe)' }
  elseif (Test-Path $tplKey) { Add-Result 'Inno template installs team config' SKIP 'a real TokenVault Adapter is installed for this user (same AppId); not touching it' }
  else {
    $pk2 = Invoke-Exe $Python @((Join-Path $PSScriptRoot 'make_team_installer.py'), $template, (Join-Path $tmp 'team2'), 'Inno Team')
    if ($pk2.Code -ne 0) { Add-Result 'Inno template installs team config' FAIL "packager exit $($pk2.Code): $($pk2.Out)" }
    else {
      $i2 = ($pk2.Out.Trim() -split "`n" | Where-Object { $_ -like '{*' } | Select-Object -Last 1) | ConvertFrom-Json
      $d2 = Join-Path $tmp 'inno-data'; $dir2 = Join-Path $tmp 'inno-app'
      $r = Invoke-Exe $i2.path @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/DIR=$dir2",
        "/ADAPTERARGS=--silent --accept --no-start --no-autostart --data-dir $d2")
      $cfgFile = Join-Path $d2 'config.json'
      if ($r.Blocked) { Add-Result 'Inno template installs team config' BLOCKED $r.Out.Trim() }
      elseif ($r.Code -ne 0 -or -not (Test-Path $cfgFile)) { Add-Result 'Inno template installs team config' FAIL "installer exit $($r.Code), config exists: $(Test-Path $cfgFile)" }
      elseif ((Get-Content $cfgFile -Raw | ConvertFrom-Json).team -ne 'Inno Team') { Add-Result 'Inno template installs team config' FAIL 'wrong team in config.json' }
      else { Add-Result 'Inno template installs team config' PASS 'config.json written by the installed adapter' }
      $un = Get-ChildItem $dir2 -Filter 'unins*.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
      if ($un) { Invoke-Exe $un.FullName @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') | Out-Null }
    }
  }

  # 5. Manager installer silent install -------------------------------------------------------
  if (-not (Test-Path $mgrSetup)) { Add-Result 'manager installer silent install + selftest' SKIP 'no Inno Manager installer' }
  else {
    $inst = Join-Path $tmp 'mgr-install'
    $r = Invoke-Exe $mgrSetup @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/DIR=$inst", '/NOFIREWALL=1', '/NOWEBVIEW2=1')
    if ($r.Blocked) { Add-Result 'manager installer silent install + selftest' BLOCKED $r.Out.Trim() }
    elseif ($r.Code -ne 0) { Add-Result 'manager installer silent install + selftest' FAIL "installer exit $($r.Code)" }
    else {
      $s = Invoke-Exe (Join-Path $inst 'TokenVault-Manager.exe') @('--selftest', '--require-template')
      $hasTpl = Test-Path (Join-Path $inst 'adapter-setup-template.exe')
      if ($s.Code -eq 0 -and $hasTpl) { Add-Result 'manager installer silent install + selftest' PASS 'installed selftest exit 0' }
      else { Add-Result 'manager installer silent install + selftest' FAIL "selftest exit $($s.Code), template present: $hasTpl" }
      $un = Get-ChildItem $inst -Filter 'unins*.exe' | Select-Object -First 1
      if ($un) { Invoke-Exe $un.FullName @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') | Out-Null }
    }
  }
} finally {
  $env:USERPROFILE = $saved.UP; $env:LOCALAPPDATA = $saved.LA
  Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ''
$results | Format-Table -AutoSize | Out-String | Write-Host
$fail = @($results | Where-Object Status -eq 'FAIL').Count
$blocked = @($results | Where-Object Status -eq 'BLOCKED').Count
if ($fail -gt 0) { Write-Host "SMOKE: FAIL ($fail failed)" -ForegroundColor Red; exit 1 }
if ($blocked -gt 0 -and -not $AllowBlocked) { Write-Host "SMOKE: INCOMPLETE ($blocked step(s) blocked by Application Control)" -ForegroundColor Magenta; exit 4 }
Write-Host 'SMOKE: OK' -ForegroundColor Green
