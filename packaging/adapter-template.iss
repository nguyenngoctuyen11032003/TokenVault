; TokenVault Adapter - template installer (per user, no admin).
;
; This UNSIGNED exe is the "adapter-setup-template.exe" that the Manager copies and
; appends a config payload to (magic TVLTCFG1, see common/config_payload.py). At run
; time it installs the adapter into %LOCALAPPDATA%\Programs and runs
;     TokenVaultAdapter.exe setup --from "<this installer>"
; which reads the payload from the installer file, shows the consent window, writes the
; config, registers HKCU Run autostart and starts the agent.
;
; Never sign this file: appended payload bytes would invalidate the signature.
;
; Compile:  ISCC /DAdapterExe=..\dist\adapter\TokenVaultAdapter.exe /O..\dist\installer adapter-template.iss
; Testing:  extra adapter arguments can be passed on the installer command line, e.g.
;     TokenVault-Adapter-Dev.exe /VERYSILENT /SUPPRESSMSGBOXES /ADAPTERARGS="--silent --accept --no-start --no-autostart --data-dir C:\tmp\tv"

#ifndef AdapterExe
  #define AdapterExe "..\dist\adapter\TokenVaultAdapter.exe"
#endif
#define AppName "TokenVault Adapter"
#define ExeName "TokenVaultAdapter.exe"
#define AppVer "0.1.0"

[Setup]
; Fixed AppId: a newer installer upgrades in place (same folder, same uninstall entry).
AppId={{6C1D9B0A-3E5F-4B7A-9D2E-7A41B8F0C5D1}
AppName={#AppName}
AppVersion={#AppVer}
AppPublisher=TokenVault
PrivilegesRequired=lowest
DefaultDirName={autopf}\TokenVault Adapter
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
DisableWelcomePage=yes
UsePreviousAppDir=yes
Compression=lzma2
SolidCompression=yes
OutputBaseFilename=adapter-setup-template
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#ExeName}
ArchitecturesInstallIn64BitMode=x64compatible
; The agent is stopped by our own code below (reload event), not by Restart Manager.
CloseApplications=no
RestartApplications=no
WizardStyle=modern

[Files]
Source: "{#AdapterExe}"; DestDir: "{app}"; DestName: "{#ExeName}"; Flags: ignoreversion
; Unpacked to {tmp} early so the consent screen can run before the old install is touched.
Source: "{#AdapterExe}"; DestName: "{#ExeName}"; Flags: dontcopy

[Code]
const
  ExeImage = '{#ExeName}';
  EXIT_NO_PAYLOAD = 2;
  EXIT_DECLINED = 3;
  SYNCHRONIZE = $00100000;
  EVENT_MODIFY_STATE = $0002;

function OpenEventW(Access: Cardinal; Inherit: Integer; Name: String): THandle;
  external 'OpenEventW@kernel32.dll stdcall';
function SetEvent(H: THandle): Integer;
  external 'SetEvent@kernel32.dll stdcall';
function OpenMutexW(Access: Cardinal; Inherit: Integer; Name: String): THandle;
  external 'OpenMutexW@kernel32.dll stdcall';
function CloseHandle(H: THandle): Integer;
  external 'CloseHandle@kernel32.dll stdcall';

{ Same name the agent derives in adapter/singleton.py: sha256(lower(user))[:16]. }
function MutexName: String;
begin
  Result := 'Local\TokenVaultAdapter-' +
    Copy(GetSHA256OfString(Lowercase(GetUserNameString)), 1, 16);
end;

function AgentRunning: Boolean;
var
  H: THandle;
begin
  H := OpenMutexW(SYNCHRONIZE, 0, MutexName);
  Result := H <> 0;
  if Result then CloseHandle(H);
end;

{ Ask this user's agent to exit via its reload event and wait up to 10 s. Whatever happens
  (also when the mutex name did not match, e.g. a non-ASCII user name hashed differently),
  finish by killing our own exe image so the files can be replaced. Rc 128 = no such
  process, which is fine. }
procedure StopAgent;
var
  H: THandle;
  I, Rc: Integer;
begin
  if AgentRunning then
  begin
    H := OpenEventW(EVENT_MODIFY_STATE, 0, MutexName + '-reload');
    if H <> 0 then
    begin
      SetEvent(H);
      CloseHandle(H);
    end;
    for I := 1 to 100 do
    begin
      if not AgentRunning then Break;
      Sleep(100);
    end;
  end;
  if Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM ' + ExeImage, '', SW_HIDE,
          ewWaitUntilTerminated, Rc) then
  begin
    if (Rc <> 0) and (Rc <> 128) then
      Log('taskkill ' + ExeImage + ' returned ' + IntToStr(Rc));
    Sleep(500);
  end;
end;

var
  ConsentGiven: Boolean;

(* Show ONLY the consent screen (new adapter exe unpacked to {tmp}) BEFORE anything of an
  existing install is touched. Declined / no UI: the old agent keeps running untouched. *)
function AskConsentFirst: String;
var
  Rc: Integer;
begin
  Result := '';
  { Test runs (ADAPTERARGS with --silent) never show a window. }
  if Pos('--silent', ExpandConstant('{param:ADAPTERARGS|}')) > 0 then Exit;
  ExtractTemporaryFile(ExeImage);
  if not Exec(ExpandConstant('{tmp}\' + ExeImage),
              'consent --from "' + ExpandConstant('{srcexe}') + '"', '', SW_SHOWNORMAL,
              ewWaitUntilTerminated, Rc) then
  begin
    { Exec failed: Rc is the Windows error code (5 = access denied, 1260 = blocked by an
      application-control / Smart App Control policy, 193 = not a valid program). }
    Log('consent exec failed: ' + IntToStr(Rc) + ' ' + SysErrorMessage(Rc));
    Result := 'Windows would not start the TokenVault Adapter program (error ' + IntToStr(Rc) +
              ': ' + SysErrorMessage(Rc) + ').' + #13#10 + #13#10 +
              'This computer probably blocks unsigned programs (Smart App Control / ' +
              'Application Control / antivirus). Nothing was changed.' + #13#10 + #13#10 +
              'Ask your manager for the "no .exe" package (Windows - goi khong can exe) ' +
              'and run Install.cmd from it.';
    Exit;
  end;
  if Rc = 0 then
    ConsentGiven := True
  else if Rc = EXIT_DECLINED then
    Result := 'Installation cancelled: you declined the consent screen. Nothing was changed ' +
              'and nothing was sent.'
  else if Rc = EXIT_NO_PAYLOAD then
    Result := 'This file has no TokenVault configuration, so it cannot be set up. Ask your ' +
              'manager for your team''s installer.'
  else
    Result := 'The consent screen could not be shown (code ' + IntToStr(Rc) +
              '). Nothing was changed.';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := AskConsentFirst;
  if Result <> '' then Exit;
  StopAgent;   { files must be replaceable }
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Rc: Integer;
  Args: String;
begin
  if CurStep <> ssPostInstall then Exit;
  Args := 'setup --from "' + ExpandConstant('{srcexe}') + '" ' +
          ExpandConstant('{param:ADAPTERARGS|}');
  if ConsentGiven then Args := Args + ' --silent --accept';  { already consented above }
  { The adapter shows its own consent window, writes the config, registers autostart
    and (re)starts the agent. }
  if not Exec(ExpandConstant('{app}\{#ExeName}'), Args, '', SW_SHOWNORMAL,
              ewWaitUntilTerminated, Rc) then
  begin
    SuppressibleMsgBox('Windows would not start the TokenVault Adapter (error ' + IntToStr(Rc) +
      ': ' + SysErrorMessage(Rc) + '). This computer probably blocks unsigned programs. ' +
      'Uninstall this entry and ask your manager for the "no .exe" package.',
      mbError, MB_OK, IDOK);
    Exit;
  end;
  if Rc = EXIT_NO_PAYLOAD then
    SuppressibleMsgBox('This file has no TokenVault configuration, so it cannot be set up. ' +
      'Ask your manager for your team''s installer (made with "Create installer" in the ' +
      'TokenVault Manager) and run that file.', mbError, MB_OK, IDOK)
  else if (Rc <> 0) and (Rc <> EXIT_DECLINED) then
    SuppressibleMsgBox('The TokenVault Adapter could not be set up (code ' + IntToStr(Rc) +
      '). Ask your administrator for a new installer.', mbError, MB_OK, IDOK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    StopAgent;
    { autostart entry written by the adapter (adapter/autostart.py) }
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'TokenVaultAdapter');
  end
  else if CurUninstallStep = usPostUninstall then
  begin
    { keep config/outbox unless the user agrees; silent uninstall keeps them }
    if not UninstallSilent then
      if MsgBox('Also delete the TokenVault Adapter data (settings, unsent queue)?',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(ExpandConstant('{localappdata}\TokenVault\Adapter'), True, True, True);
  end;
end;
