@echo off
rem TokenVault Manager - run without installing (no exe, no admin).
rem First run: remove the Mark-of-the-Web from the extracted files (once).
if not exist "%~dp0.unblocked" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath '%~dp0.' -Recurse -File -Force | Unblock-File -ErrorAction SilentlyContinue" >nul 2>&1
  echo done> "%~dp0.unblocked"
)
if not exist "%~dp0runtime\pythonw.exe" (
  echo runtime\pythonw.exe not found. Extract the whole ZIP first.
  pause
  exit /b 1
)
start "" "%~dp0runtime\pythonw.exe" -m tokenvault.manager %*
exit /b 0
