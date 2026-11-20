@echo off
rem TokenVault Adapter - no-exe installer (per user, no admin). Double-click me (1-CAI-DAT.cmd).
rem ASCII only on purpose: cmd.exe mis-parses UTF-8 batch files. Any argument containing
rem -Silent runs without prompts/pauses (IT / automated tests).
title TokenVault Adapter - Cai dat / Install
cd /d "%~dp0"
set "TVQUIET="
echo %* | "%SystemRoot%\System32\find.exe" /i "-Silent" >nul && set "TVQUIET=1"

echo ==============================================================
echo   TokenVault Adapter - Cai dat / Install  (khong can quyen Admin)
echo ==============================================================
echo.

if not exist "%~dp0_he-thong\runtime\pythonw.exe" (
  echo LOI / ERROR: Chua giai nen du goi cai dat / the package is not fully extracted.
  echo.
  echo   1. Chuot phai file .zip  -^>  "Extract All..." / "Giai nen tat ca..."
  echo   2. Mo thu muc vua giai nen, chay lai 1-CAI-DAT.cmd
  echo.
  echo   Dung chay truc tiep ben trong WinRAR / trinh xem ZIP.
  echo   Do not run it from inside WinRAR / a ZIP viewer.
  if not defined TVQUIET pause >nul
  exit /b 1
)

if not defined TVQUIET (
  echo Sap hien cua so "Dong y" liet ke chinh xac du lieu duoc gui.
  echo Hay doc roi bam "Dong y" / "Accept". Mat khoang 10-30 giay.
  echo A consent window will open: read it and press Accept ^(10-30 seconds^).
  echo.
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0_he-thong\install.ps1" %*
set RC=%ERRORLEVEL%

if "%RC%"=="0" (
  echo.
  echo ==============================================================
  echo   DA CAI XONG / INSTALLED.
  echo   Adapter chay nen va tu khoi dong cung Windows.
  echo   Bieu tuong TokenVault nam o khay he thong ^(goc phai thanh tac vu,
  echo   co the trong nut mui ten an^). Co the dong cua so nay.
  echo ==============================================================
  if not defined TVQUIET pause >nul
  exit /b 0
)
if "%RC%"=="3" (
  echo.
  echo Da huy cai dat ^(ban khong dong y^). Khong co gi bi thay doi, khong co du lieu nao duoc gui.
  echo Cancelled: consent declined. Nothing was changed or sent.
  if not defined TVQUIET pause >nul
  exit /b 3
)
if "%RC%"=="10" (
  echo.
  echo Windows khong cho chay thanh phan Python cua goi nay ^(chinh sach bao mat cua may^).
  echo Windows blocked the bundled Python runtime. Please contact your IT / manager.
  if not defined TVQUIET pause >nul
  exit /b 10
)
echo.
echo Cai dat chua hoan tat ^(ma loi %RC%^). Hay chup man hinh cua so nay gui nguoi quan ly.
echo Setup did not finish ^(code %RC%^). Send a screenshot of this window to your manager.
if not defined TVQUIET pause >nul
exit /b %RC%
