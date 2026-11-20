@echo off
rem TokenVault Manager - run now, nothing is installed (no exe, no admin). ASCII only.
title TokenVault Manager - Chay ngay / Run now
cd /d "%~dp0"
if not exist "%~dp0_he-thong\runtime\pythonw.exe" (
  echo LOI / ERROR: Chua giai nen day du goi ZIP / the package is not fully extracted.
  echo   Chuot phai file .zip -^> "Extract All..." / "Giai nen tat ca...", roi chay lai file nay.
  echo   Right-click the .zip, choose Extract All, then run this file again.
  echo   Dung chay truc tiep ben trong WinRAR / trinh xem ZIP.
  pause
  exit /b 1
)
rem First run: remove the Mark-of-the-Web from the extracted files (once).
if not exist "%~dp0_he-thong\.unblocked" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath '%~dp0.' -Recurse -File -Force | Unblock-File -ErrorAction SilentlyContinue" >nul 2>&1
  echo done> "%~dp0_he-thong\.unblocked"
)
echo Dang mo TokenVault Manager ... / Starting TokenVault Manager ...
start "" "%~dp0_he-thong\runtime\pythonw.exe" -m tokenvault.manager %*
exit /b 0
