@echo off
rem TokenVault Manager - uninstall the installed copy (shortcuts, autostart, firewall rules, runtime).
rem Works from the extracted ZIP and from the copy installed next to the program (Windows
rem Settings > Installed apps). Asks whether to delete the saved statistics (default: keep).
rem ASCII only on purpose. -Silent (IT / tests) skips the question and the pause; -RemoveData /
rem -KeepData answer the question. Everything after setlocal is ONE block, parsed before it
rem runs: the file may delete itself.
setlocal EnableDelayedExpansion
title TokenVault Manager - Go cai dat / Uninstall
set "TVQUIET="
echo %* | "%SystemRoot%\System32\find.exe" /i "-Silent" >nul && set "TVQUIET=1"
set "TVPS=%~dp0_he-thong\scripts\Uninstall-Manager.ps1"
if not exist "%TVPS%" set "TVPS=%~dp0scripts\Uninstall-Manager.ps1"
rem the current folder must not be the install dir (it would lock it)
cd /d "%TEMP%"
(
  echo ==============================================================
  echo   TokenVault Manager - Go cai dat / Uninstall
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
    echo Go cai dat chua hoan tat hoan toan ^(ma loi !RC!^). Xem muc CHUA XOA o tren.
    echo Uninstall did not finish completely ^(code !RC!^).
  )
  if not defined TVQUIET pause >nul
  exit /b !RC!
)
