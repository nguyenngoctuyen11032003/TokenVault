; TokenVault Manager installer (per user, in %LOCALAPPDATA%\Programs; one UAC prompt).
;
; Application Control / Smart App Control blocks every UNSIGNED .exe (error 4551 "An
; Application Control policy has blocked this file") and there is no code-signing
; certificate. So the installed Manager contains NO unsigned executable of its own: it is
; the official embeddable Python (python.exe / pythonw.exe, Authenticode-signed by the
; Python Software Foundation) + vendored wheels + the TokenVault sources, exactly the folder
; build\build-portable.ps1 prepares in templates\portable-runtime. Everything that used to
; launch TokenVault-Manager.exe now launches
;     {app}\runtime\pythonw.exe -m tokenvault.manager
; (shortcuts, autostart, "run now"), and the one elevated step - the inbound Windows
; Firewall rules (TCP ingest range + UDP discovery) - elevates the signed interpreter:
;     {app}\runtime\python.exe -m tokenvault.manager firewall --ensure --port <base>
; (this Setup exe itself is unsigned too: it only runs where the user can start an unsigned
; Inno installer; otherwise use the portable ZIP, see docs/packaging.md.)
;
; Installed layout:   {app}\runtime\      signed python + Lib\site-packages + app\tokenvault
;                     {app}\scripts\      adapter package scripts (windows-portable target)
;                     {app}\adapter-setup-template.exe   (windows .exe employee target)
;                     {app}\tokenvault.ico, {app}\Open-Firewall.cmd
;
; Compile (build.ps1 does this):
;   ISCC /DRuntimeDir=..\templates\portable-runtime /DTemplateExe=..\dist\adapter-setup-template.exe
;        /DRedistDir=redist /O..\dist manager.iss
; Silent install (smoke test; /NOFIREWALL=1 avoids the UAC prompt):
;   TokenVault-Manager-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /DIR="C:\tmp\tv" /NOFIREWALL=1 /NOWEBVIEW2=1

#ifndef RuntimeDir
  #define RuntimeDir "..\templates\portable-runtime"
#endif
#ifndef TemplateExe
  #define TemplateExe "..\dist\adapter-setup-template.exe"
#endif
#ifndef RedistDir
  #define RedistDir "redist"
#endif
#define AppName "TokenVault Manager"
#define PyW "{app}\runtime\pythonw.exe"
#define PyArgs "-m tokenvault.manager"
#define AppVer "0.1.0"

[Setup]
AppId={{B2F5E7C3-8A14-4D69-A0B7-5E93D1C64F28}
AppName={#AppName}
AppVersion={#AppVer}
AppPublisher=TokenVault
PrivilegesRequired=lowest
DefaultDirName={autopf}\TokenVault Manager
DefaultGroupName=TokenVault
DisableProgramGroupPage=yes
Compression=lzma2
SolidCompression=yes
OutputBaseFilename=TokenVault-Manager-Setup
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\tokenvault.ico
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
CloseApplications=no
WizardStyle=modern
SetupIconFile=..\assets\logo\tokenvault.ico
WizardImageFile=brand\wizard-large.bmp
WizardSmallImageFile=brand\wizard-small.bmp

[Tasks]
Name: "startatlogin"; Description: "Start TokenVault Manager when I sign in to Windows"; Flags: unchecked
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[InstallDelete]
; upgrade in place: a fresh runtime (no stale wheels / sources) and no leftovers of the old
; PyInstaller-based Manager install (its exe is what Application Control blocked)
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\scripts"
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\TokenVault-Manager.exe"

[Files]
Source: "{#RuntimeDir}\runtime\*"; DestDir: "{app}\runtime"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#RuntimeDir}\scripts\*"; DestDir: "{app}\scripts"; Flags: ignoreversion recursesubdirs createallsubdirs
; Employee installer for the "Windows .exe" target (the default target is the portable ZIP).
Source: "{#TemplateExe}"; DestDir: "{app}"; DestName: "adapter-setup-template.exe"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\assets\logo\tokenvault.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "manager-extras\Open-Firewall.cmd"; DestDir: "{app}"; Flags: ignoreversion
; WebView2 Evergreen bootstrapper, only run when the runtime is missing (see [Code]).
Source: "{#RedistDir}\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall skipifsourcedoesntexist

[Icons]
Name: "{autoprograms}\TokenVault Manager"; Filename: "{#PyW}"; Parameters: "{#PyArgs}"; WorkingDir: "{app}"; IconFilename: "{app}\tokenvault.ico"
Name: "{autodesktop}\TokenVault Manager"; Filename: "{#PyW}"; Parameters: "{#PyArgs}"; WorkingDir: "{app}"; IconFilename: "{app}\tokenvault.ico"; Tasks: desktopicon
; One-click retry of the elevated firewall step (UAC) if it was declined / failed at install.
Name: "{autoprograms}\Mở cổng tường lửa cho TokenVault (cần quyền admin)"; Filename: "{sys}\cmd.exe"; Parameters: "/c """"{app}\Open-Firewall.cmd"""""; WorkingDir: "{app}"; IconFilename: "{app}\tokenvault.ico"; Comment: "Open the Windows Firewall ports for TokenVault (administrator approval)"

[Registry]
; Same value name/command as manager/win_autostart.py, so the in-app toggle stays in sync.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "TokenVault Manager"; ValueData: """{#PyW}"" {#PyArgs} --minimized"; Tasks: startatlogin; Flags: uninsdeletevalue

[Run]
Filename: "{#PyW}"; Parameters: "{#PyArgs}"; WorkingDir: "{app}"; Description: "Start TokenVault Manager"; Flags: nowait postinstall skipifsilent runasoriginaluser

[Code]
const
  WEBVIEW2_GUID = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function RuntimePython: String;
begin
  Result := ExpandConstant('{app}\runtime\python.exe');
end;

{ The one-line command an administrator can run later (also what the Start-menu
  shortcut "Mở cổng tường lửa cho TokenVault" does through Open-Firewall.cmd). }
function FirewallCommand(Port: String): String;
begin
  Result := '"' + RuntimePython + '" -m tokenvault.manager firewall --ensure --port ' + Port;
end;

function WebView2Present: Boolean;
var
  V: String;
  Found: Boolean;
begin
  V := '';
  Found := RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\' + WEBVIEW2_GUID, 'pv', V);
  if not Found then
    Found := RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\' + WEBVIEW2_GUID, 'pv', V);
  if not Found then
    Found := RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\' + WEBVIEW2_GUID, 'pv', V);
  Result := Found and (V <> '') and (V <> '0.0.0.0');
end;

{ Remove every rule this product ever created, whatever base port it used (the TCP rule
  name embeds the port range, so a changed port leaves stale rules). Elevated (UAC) signed
  python; a declined prompt just leaves the rules (harmless, listed in docs). }
procedure RemoveFirewallRules;
var
  Rc: Integer;
begin
  if FileExists(RuntimePython) then
    ShellExec('runas', RuntimePython, '-m tokenvault.manager firewall --remove', ExpandConstant('{app}'),
      SW_HIDE, ewWaitUntilTerminated, Rc);
end;

procedure StopManager;
var
  Rc: Integer;
begin
  { an old PyInstaller-based install: its exe image }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM TokenVault-Manager.exe', '', SW_HIDE,
       ewWaitUntilTerminated, Rc);
  { the Manager now runs as pythonw.exe: only the processes started from OUR runtime folder }
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "Get-Process pythonw,python ' +
    '-ErrorAction SilentlyContinue | Where-Object { $_.Path -like ''' +
    ExpandConstant('{app}\runtime\') + '*'' } | Stop-Process -Force -ErrorAction SilentlyContinue"',
    '', SW_HIDE, ewWaitUntilTerminated, Rc);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopManager;  { upgrade in place: files must be replaceable }
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Rc: Integer;
  Boot, Port: String;
  Checked: Boolean;
begin
  if CurStep <> ssPostInstall then Exit;

  { 1. WebView2 runtime (needed for the window). }
  Boot := ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe');
  if (ExpandConstant('{param:NOWEBVIEW2|0}') <> '1') and (not WebView2Present) then
  begin
    if FileExists(Boot) then
      Exec(Boot, '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, Rc);
    if not WebView2Present then
      SuppressibleMsgBox('The Microsoft Edge WebView2 Runtime is not installed. ' +
        'Install it from https://go.microsoft.com/fwlink/p/?LinkId=2124703 before starting ' +
        'TokenVault Manager.', mbInformation, MB_OK, IDOK);
  end;

  { 2. Firewall rules via the Manager's own helper, run by the SIGNED python. An upgrade
    keeps rules that are already in place (also a custom base port chosen earlier); only
    missing ones trigger the elevated step. }
  if ExpandConstant('{param:NOFIREWALL|0}') <> '1' then
  begin
    Port := ExpandConstant('{param:PORT|47800}');
    Checked := Exec(RuntimePython, '-m tokenvault.manager firewall --check --port ' + Port,
                    ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Rc);
    if (not Checked) or (Rc <> 0) then
    begin
      if (not ShellExec('runas', RuntimePython,
                        '-m tokenvault.manager firewall --ensure --port ' + Port,
                        ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Rc)) or (Rc <> 0) then
        SuppressibleMsgBox(
          'Chưa tạo được quy tắc Tường lửa Windows (cần quyền quản trị; hộp thoại UAC bị từ chối ' +
          'hoặc bị chặn), nên máy nhân viên có thể chưa kết nối được tới Manager. Cài đặt vẫn ' +
          'hoàn tất. Để mở cổng sau này: vào Start menu, bấm "Mở cổng tường lửa cho TokenVault ' +
          '(cần quyền admin)" rồi chọn Yes, hoặc chạy lệnh sau trong cửa sổ quản trị:' + #13#10#13#10 +
          FirewallCommand(Port) + #13#10#13#10 +
          'The Windows Firewall rules could not be created (administrator approval is required, ' +
          'the UAC prompt was declined or blocked), so employee PCs may not reach this Manager. ' +
          'Installation is complete. To open the ports later: click "Mở cổng tường lửa cho ' +
          'TokenVault (cần quyền admin)" in the Start menu and choose Yes, or run this command ' +
          'as administrator:' + #13#10#13#10 + FirewallCommand(Port),
          mbError, MB_OK, IDOK);
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    StopManager;
    RemoveFirewallRules;
  end
  else if CurUninstallStep = usPostUninstall then
  begin
    { database, certificates and secrets belong to the user: kept unless they agree }
    if not UninstallSilent then
      if MsgBox('Also delete the Manager data (database, certificate, installers)?',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(ExpandConstant('{userappdata}\TokenVault\Manager'), True, True, True);
  end;
end;
