@echo off
rem TokenVault Manager - optional install (Start menu / Desktop shortcut + firewall). ASCII only.
rem Extra arguments are passed to Install-Manager.ps1 (e.g. -SkipFirewall, -NoDesktop). With
rem -Silent / -SkipFirewall (IT / tests) there is no pause.
title TokenVault Manager - Cai vao may / Install
cd /d "%~dp0"
set "TVQUIET="
echo %* | "%SystemRoot%\System32\find.exe" /i "-Silent" >nul && set "TVQUIET=1"
echo %* | "%SystemRoot%\System32\find.exe" /i "-SkipFirewall" >nul && set "TVQUIET=1"

echo ==============================================================
echo   TokenVault Manager - Cai vao may / Install on this PC
echo ==============================================================
echo   Tao bieu tuong o Start menu + Desktop va mo cong ket noi (tuong lua).
echo   Windows se hoi quyen Quan tri ^(Admin^) MOT lan cho phan tuong lua:
echo   hay bam "Yes". Neu khong can may khac ket noi vao, co the bam "No".
echo   Creates Start menu + Desktop shortcuts and the firewall rules. Windows asks
echo   for administrator permission ONCE for the firewall step: press Yes.
echo.

if not exist "%~dp0_he-thong\scripts\Install-Manager.ps1" (
  echo LOI / ERROR: Chua giai nen day du goi ZIP / the package is not fully extracted.
  echo   Chuot phai file .zip -^> "Extract All...", roi chay lai file nay.
  if not defined TVQUIET pause >nul
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0_he-thong\scripts\Install-Manager.ps1" %*
set RC=%ERRORLEVEL%
echo.
if "%RC%"=="0" (
  echo ==============================================================
  echo   DA CAI XONG / INSTALLED. Manager dang mo.
  echo   Bieu tuong "TokenVault Manager": tren man hinh Desktop va trong menu Start
  echo   ^(go "TokenVault Manager" vao o tim kiem cua Windows^).
  echo   Dong cua so Manager chi THU NHO xuong khay he thong ^(bieu tuong cung nut mui ten ^^ ^).
  echo   Co the xoa thu muc giai nen nay: ban da cai nam o %%LOCALAPPDATA%%\Programs\TokenVault Manager.
  echo ==============================================================
) else if "%RC%"=="6" (
  echo ==============================================================
  echo   DA CAI XONG ^(da co bieu tuong Desktop + Start menu^), nhung buoc TUONG LUA chua xong ^(ban bam No hoac bi chan^).
  echo   May khac se chua ket noi duoc cho den khi quan tri vien chay lai file nay.
  echo   Installed, but the firewall step was cancelled or failed: other PCs cannot
  echo   connect until an administrator runs this file again.
  echo ==============================================================
) else (
  echo Cai dat chua hoan tat ^(ma loi %RC%^). Hay chup man hinh cua so nay de duoc ho tro.
  echo Setup did not finish ^(code %RC%^).
)
if not defined TVQUIET pause >nul
exit /b %RC%
