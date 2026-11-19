; TokenVault Manager installer (per user, in %LOCALAPPDATA%\Programs; one UAC prompt).
;
; The installer itself runs unelevated, so the files, Start Menu entry, autostart and the
; Manager's data (%APPDATA%) all belong to the signed-in user. Admin rights are needed for
; ONE thing: the inbound Windows Firewall rules (TCP ingest range + UDP discovery). That
; step - and the matching removal on uninstall - is run elevated (ShellExec runas) as the
; Manager's own helper
;     TokenVault-Manager.exe firewall --ensure --port <base>
;
; Compile (build.ps1 does this):
;   ISCC /DSourceDir=..\dist\manager\TokenVault-Manager /DRedistDir=redist /O..\dist\installer manager.iss
; SourceDir must already contain adapter-setup-template.exe (build.ps1 puts it there).
; Silent install (smoke test; /NOFIREWALL=1 avoids the UAC prompt):
;   TokenVault-Manager-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /DIR="C:\tmp\tv" /NOFIREWALL=1 /NOWEBVIEW2=1

#ifndef SourceDir
  #define SourceDir "..\dist\manager\TokenVault-Manager"
#endif
#ifndef RedistDir
  #define RedistDir "redist"
#endif
#define AppName "TokenVault Manager"
#define ExeName "TokenVault-Manager.exe"
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
UninstallDisplayIcon={app}\{#ExeName}
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
CloseApplications=no
WizardStyle=modern

[Tasks]
Name: "startatlogin"; Description: "Start TokenVault Manager when I sign in to Windows"; Flags: unchecked
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; WebView2 Evergreen bootstrapper, only run when the runtime is missing (see [Code]).
Source: "{#RedistDir}\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall skipifsourcedoesntexist

[Icons]
Name: "{autoprograms}\TokenVault Manager"; Filename: "{app}\{#ExeName}"
Name: "{autodesktop}\TokenVault Manager"; Filename: "{app}\{#ExeName}"; Tasks: desktopicon

[Registry]
; Same value name/command as manager/win_autostart.py, so the in-app toggle stays in sync.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "TokenVault Manager"; ValueData: """{app}\{#ExeName}"" --minimized"; Tasks: startatlogin; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#ExeName}"; Description: "Start TokenVault Manager"; Flags: nowait postinstall skipifsilent runasoriginaluser

[Code]
const
  WEBVIEW2_GUID = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

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
  name embeds the port range, so a changed port leaves stale rules). Elevated: UAC prompt. }
procedure RemoveFirewallRules;
var
  Rc: Integer;
begin
  ShellExec('runas', ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "Get-NetFirewallRule ' +
    '-DisplayName ''TokenVault-Manager-*'' -ErrorAction SilentlyContinue | ' +
    'Remove-NetFirewallRule -ErrorAction SilentlyContinue"',
    '', SW_HIDE, ewWaitUntilTerminated, Rc);
end;

procedure StopManager;
var
  Rc: Integer;
begin
  { our own exe image only }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#ExeName}', '', SW_HIDE,
       ewWaitUntilTerminated, Rc);
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

  { 2. Firewall rules via the Manager's own helper. An upgrade keeps rules that are already
    in place (also a custom base port chosen earlier); only missing ones trigger the
    elevated step. }
  if ExpandConstant('{param:NOFIREWALL|0}') <> '1' then
  begin
    Port := ExpandConstant('{param:PORT|47800}');
    if (not Exec(ExpandConstant('{app}\{#ExeName}'), 'firewall --check --port ' + Port, '',
                 SW_HIDE, ewWaitUntilTerminated, Rc)) or (Rc <> 0) then
    begin
      if (not ShellExec('runas', ExpandConstant('{app}\{#ExeName}'),
                        'firewall --ensure --port ' + Port, '', SW_HIDE,
                        ewWaitUntilTerminated, Rc)) or (Rc <> 0) then
        SuppressibleMsgBox('The Windows Firewall rules could not be created (administrator ' +
          'approval is required), so employee PCs may not reach this Manager. Run "' +
          ExpandConstant('{app}\{#ExeName}') + '" firewall --ensure as administrator later.',
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
