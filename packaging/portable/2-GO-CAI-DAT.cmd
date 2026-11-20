@echo off
rem TokenVault Adapter - uninstall (per user, no admin). Double-click me (2-GO-CAI-DAT.cmd).
rem Also installed next to the program so Windows Settings > Installed apps can call it.
rem ASCII only on purpose. -Silent / -NoAutostart (IT / tests) skip the question and the pause;
rem -RemoveData wipes the local data too, -KeepData keeps it without asking.
rem Everything after setlocal is ONE block, parsed before it runs: the file may delete itself.
setlocal EnableDelayedExpansion
title TokenVault Adapter - Go cai dat / Uninstall
cd /d "%~dp0"
set "TVQUIET="
echo %* | "%SystemRoot%\System32\find.exe" /i "-Silent" >nul && set "TVQUIET=1"
echo %* | "%SystemRoot%\System32\find.exe" /i "-NoAutostart" >nul && set "TVQUIET=1"
set "TVPS=%~dp0_he-thong\uninstall.ps1"
if not exist "%TVPS%" set "TVPS=%~dp0uninstall.ps1"
(
  echo ==============================================================
  echo   TokenVault Adapter - Go cai dat / Uninstall
  echo ==============================================================
  if not exist "!TVPS!" (
    echo LOI / ERROR: Khong thay file go cai dat ^(thieu thu muc _he-thong^). Hay giai nen day du goi ZIP.
    echo The uninstaller is missing: extract the whole ZIP first.
    if not defined TVQUIET pause >nul
    exit /b 1
  )
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "!TVPS!" %*
  set RC=!ERRORLEVEL!
  echo.
  if "!RC!"=="0" (
    echo DA GO CAI DAT / REMOVED. Xem bang ket qua o tren.
  ) else (
    echo Go cai dat chua hoan tat hoan toan ^(ma loi !RC!^). Xem muc CHUA XOA o tren, hoac gui anh chup man hinh cho nguoi quan ly.
    echo Uninstall did not finish completely ^(code !RC!^).
  )
  if not defined TVQUIET pause >nul
  exit /b !RC!
)
