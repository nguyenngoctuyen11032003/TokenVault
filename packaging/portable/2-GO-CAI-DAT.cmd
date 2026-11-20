@echo off
rem TokenVault Adapter - remove (per user, no admin). Double-click me (2-GO-CAI-DAT.cmd).
rem ASCII only on purpose. -Silent / -NoAutostart (IT / tests) skip the final pause.
title TokenVault Adapter - Go cai dat / Uninstall
cd /d "%~dp0"
set "TVQUIET="
echo %* | "%SystemRoot%\System32\find.exe" /i "-Silent" >nul && set "TVQUIET=1"
echo %* | "%SystemRoot%\System32\find.exe" /i "-NoAutostart" >nul && set "TVQUIET=1"

echo ==============================================================
echo   TokenVault Adapter - Go cai dat / Uninstall
echo ==============================================================
echo.
if not exist "%~dp0_he-thong\uninstall.ps1" (
  echo LOI / ERROR: Khong thay thu muc _he-thong. Hay giai nen day du goi ZIP.
  echo The _he-thong folder is missing: extract the whole ZIP first.
  if not defined TVQUIET pause >nul
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0_he-thong\uninstall.ps1" %*
set RC=%ERRORLEVEL%
echo.
if "%RC%"=="0" (
  echo DA GO CAI DAT / REMOVED. Du lieu da thu thap duoc giu lai.
  echo Collected data was kept. To delete it too, run this file with -RemoveData.
) else (
  echo Go cai dat chua hoan tat ^(ma loi %RC%^). Hay gui anh chup man hinh cho nguoi quan ly.
  echo Uninstall did not finish ^(code %RC%^).
)
if not defined TVQUIET pause >nul
exit /b %RC%
