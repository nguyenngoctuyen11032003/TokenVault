@echo off
rem TokenVault Manager - remove the installed copy + shortcuts. ASCII only.
rem The Manager data (%APPDATA%\TokenVault\Manager) and the firewall rules are kept.
rem With -Silent (IT / tests) there is no pause.
title TokenVault Manager - Go cai dat / Uninstall
cd /d "%~dp0"
set "TVQUIET="
echo %* | "%SystemRoot%\System32\find.exe" /i "-Silent" >nul && set "TVQUIET=1"

echo ==============================================================
echo   TokenVault Manager - Go cai dat / Uninstall
echo ==============================================================
if not exist "%~dp0_he-thong\scripts\Uninstall-Manager.ps1" (
  echo LOI / ERROR: Khong thay thu muc _he-thong. Hay giai nen day du goi ZIP.
  echo The _he-thong folder is missing: extract the whole ZIP first.
  if not defined TVQUIET pause >nul
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0_he-thong\scripts\Uninstall-Manager.ps1" %*
set RC=%ERRORLEVEL%
echo.
if "%RC%"=="0" (
  echo DA GO CAI DAT / REMOVED. Du lieu cua Manager van con nguyen ^(khong bi xoa^).
  echo Manager data was kept.
) else (
  echo Go cai dat chua hoan tat ^(ma loi %RC%^). / Uninstall did not finish ^(code %RC%^).
)
if not defined TVQUIET pause >nul
exit /b %RC%
