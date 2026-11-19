@echo off
rem TokenVault Adapter - no-exe installer (per user, no admin). Double-click me.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set RC=%ERRORLEVEL%
if not "%RC%"=="0" (
  echo.
  echo Setup did not finish ^(code %RC%^). Press any key to close.
  pause >nul
)
exit /b %RC%
