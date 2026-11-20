@echo off
rem TokenVault Manager - open the Windows Firewall ports (needs administrator approval).
rem Elevates the Authenticode-signed runtime\python.exe (NOT an unsigned exe, which Smart App
rem Control / Application Control may block). Usage: Open-Firewall.cmd [base port, default 47800]
rem Mo cong tuong lua cho TokenVault (can quyen admin). Chay lai bang cach nhap dup file nay.
setlocal
set "PORT=%~1"
if "%PORT%"=="" set "PORT=47800"
set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" (
  echo Khong thay / not found: %PY%
  pause
  exit /b 1
)
echo Se hien hop thoai xin quyen quan tri (UAC) - hay bam Yes / Co.
echo A Windows permission (UAC) prompt will appear - please click Yes.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "try { $p = Start-Process -FilePath '%PY%' -ArgumentList '-m tokenvault.manager firewall --ensure --port %PORT%' -Verb RunAs -Wait -PassThru -WindowStyle Hidden; exit $p.ExitCode } catch { exit 1223 }"
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" (
  echo.
  echo Da mo cong tuong lua cho TokenVault. / Firewall rules for TokenVault are in place.
) else (
  echo.
  echo KHONG tao duoc quy tac tuong lua ^(ma %RC%; 1223 = ban da tu choi UAC^).
  echo The firewall rules could not be created ^(code %RC%; 1223 = UAC was declined^).
  echo Chay lai file nay va bam Yes, hoac chay lenh sau trong cua so quan tri:
  echo Run this file again and click Yes, or run in an administrator window:
  echo   "%PY%" -m tokenvault.manager firewall --ensure --port %PORT%
)
echo.
pause
exit /b %RC%
