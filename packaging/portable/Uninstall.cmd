@echo off
rem TokenVault Adapter - remove (per user, no admin).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %*
exit /b %ERRORLEVEL%
