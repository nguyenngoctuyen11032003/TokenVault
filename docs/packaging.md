# Packaging and distribution

Build details live in `packaging/README.md`. Summary and the three delivery flavours follow.

## Which package for which machine
| Situation on the employee machine | Package | Why |
|---|---|---|
| Ordinary Windows 10/11; SmartScreen shows "Windows protected your PC" | **exe** (`TokenVault-Adapter-<team>.exe`) | Click **More info** -> **Run anyway**. One file, registers an uninstaller in Apps & Features. |
| Application Control (WDAC/AppLocker) or **Smart App Control** refuses to run the unsigned exe at all (no "Run anyway" button, or it silently does not start) | **portable** (`TokenVault-Adapter-<team>-portable.zip`) | Contains no unsigned exe: it runs the Python Software Foundation-signed `python.exe`/`pythonw.exe` plus readable TokenVault source. See the caveat in section B about unsigned `.pyd` files. |
| macOS 11+ | **mac** (`TokenVault-Adapter-<team>-mac.zip`) | Unsigned app + `Install.command`; see section C. |
| Not sure | Try the exe first; if Windows blocks it with no "Run anyway", send the portable ZIP | Each build is its own revocable package, so both can exist for one team. |

Plain SmartScreen is only a warning: use the exe. Application Control / Smart App Control is a block: use the portable ZIP.

Avoid the Mark-of-the-Web: hand every file out via a **network share, USB stick or chat file transfer**, not a browser download or e-mail attachment. A browser/e-mail download adds the "from the internet" mark, which triggers SmartScreen for the exe. In the portable ZIP, `Install.cmd` removes the mark itself (`Unblock-File`); for the exe use right-click -> Properties -> **Unblock**, or `Unblock-File .\file.exe`.

In the Manager, page **Tạo bộ cài** has a selector with the three targets: `windows` (default), `windows-portable`, `mac` (`Api.create_installer(team, label, target)`).

## A. Windows exe installers (available)
Build machine: Windows 10/11 x64, Python 3.11+ with the project and `pyinstaller`, Inno Setup 6.3+.
```powershell
powershell -File packaging\build.ps1
powershell -File tests\packaging\test_build_outputs.ps1   # smoke test
```
Order: adapter (PyInstaller onefile) -> `adapter-setup-template.exe` (Inno, per user) -> Manager (PyInstaller onedir) -> WebView2 bootstrapper -> `TokenVault-Manager-Setup.exe`. Outputs in `dist/`. Without ISCC the script builds the PyInstaller outputs and exits 3.
* The template **must stay unsigned**: the Manager appends the config payload (`TVLTCFG1`) to a copy, which would invalidate a signature.
* The generated team installer holds an enrollment secret: treat it like a credential.
* Manager installer switches: `/NOFIREWALL=1 /NOWEBVIEW2=1 /PORT=47800`. Adapter template: `/ADAPTERARGS="--silent --accept --no-start --no-autostart --data-dir X"`.

## B. Windows "no-exe" portable package (Task 25, merged)
For machines whose Application Control policy refuses any new unsigned exe. More detail: `packaging/README.md`, `packaging/portable/`.
* **Prepare once** (Windows, internet needed): `powershell -File build\build-portable.ps1`. It downloads the official `python-3.12.10-embed-amd64.zip`, checks a pinned SHA-256 and the Authenticode publisher "Python Software Foundation" (python.exe, pythonw.exe, python312.dll), installs cp312-win_amd64 wheels with `pip --target`, copies tkinter (consent screen), compiles `.pyc`, runs the adapter/Manager self-tests and writes the folder **`templates\portable-runtime\`** (in a source checkout: under the repo; a frozen Manager looks next to its exe; override with env `TOKENVAULT_PORTABLE_RUNTIME`). It also builds `dist\TokenVault-Manager-portable.zip`. While that folder is missing, creating a `windows-portable` installer fails with `[template_missing]` and the UI tells the owner to run this script.
* **Per team:** target `windows-portable` -> `TokenVault-Adapter-<team>-portable.zip` (about 35-40 MB; holds the team enrollment credential in `tokenvault-config.bin`, same handling as the exe).
* **Employee:** extract the whole ZIP, double-click `Install.cmd` (per user, **no admin**, `Unblock-File` automatic), read the consent screen, accept. It verifies the Python signature, copies the runtime to `%LOCALAPPDATA%\TokenVault\Adapter\runtime`, runs `pythonw.exe -m tokenvault.adapter setup --from <payload>` and registers the per-user autostart. Remove: `Uninstall.cmd` (`-RemoveData` also deletes collected data).
* **Manager without the exe installer:** extract `TokenVault-Manager-portable.zip` (no credential inside). `Start-Manager.cmd` runs it in place; `Install-Manager.ps1` (right-click -> Run with PowerShell) adds Start menu/Desktop shortcuts and ONE elevated step that creates the firewall rules; `Uninstall-Manager.ps1` removes them. Needs the WebView2 runtime (built into Windows 11).
* **Check:** `runtime\python.exe -m tokenvault.adapter --selftest` (or `-m tokenvault.manager --selftest`) prints a `runtime:` line with signature status and dependencies.
* **Verified (developer PC, Task 25):** real runtime build, ZIP created through the Api, extract + `Install.cmd` (silent), device enrolled and connected, reinstall over a running agent, `Uninstall.cmd`, Manager selftest and a real WebView2 window, shortcuts.
* **NOT verified:** a machine where Application Control / Smart App Control really blocks unsigned code; the interactive consent window (bypassed with `--silent --accept`); the elevated firewall step; the HKCU Run value on the real registry.
* **Caveats:** the wheels' native modules (`.pyd`/`.dll` of cryptography, Pillow, websockets, ...; 18 found) are not Authenticode-signed. A policy that blocks unsigned DLLs, not only unsigned exe files, will also block them and `Install.cmd` reports an error. The first start of a freshly copied `pythonw.exe` took about two minutes on the dev PC (real-time scan); the installer just waits.

## C. macOS ZIP (Task 24, merged; real-Mac verification PENDING)
Windows is the tested platform. The macOS code is unit-tested with injected fakes on Windows; every item in the "macOS" section of `docs/e2e-checklist.md` still has to be ticked on a real Mac (Apple Silicon and Intel, macOS 11+).
* **Prepare once, on a Mac:** `build/build-mac.sh` (PyInstaller cannot cross-compile; it uses an isolated venv) -> `dist-mac/adapter-mac.zip`. Copy it to `<Manager data folder>\templates\adapter-mac.zip` (default `%APPDATA%\TokenVault\Manager\templates`). Without it, creating a `mac` installer fails with `[template_missing]` and a Vietnamese "build on a Mac" message that shows the expected path.
* **Output:** `TokenVault-Adapter-<team>-mac.zip` with the unsigned (ad-hoc signed) `TokenVault Adapter.app`, `Install.command`, `Uninstall.command` and the sidecar `tokenvault-config.bin` (enrollment credential).
* **Install:** unzip, right-click `Install.command` -> Open the first time (on macOS 15 use System Settings -> Privacy & Security -> "Open Anyway" if that route is not offered). The script clears the quarantine flag on its own files, stages the app, shows the **consent dialog before changing anything** (declining leaves an existing install untouched), then replaces the app in `~/Applications`, runs `setup` and registers the LaunchAgent `vn.ics.tokenvault.adapter` (starts at login, restarted by launchd after a crash). No admin rights.
* **Menu-bar indicator is mandatory:** the agent shows a menu-bar icon (menu: status line, "Dữ liệu gửi đi là gì?", open log folder, About; no pause, hide or Quit). If the icon cannot be created the agent **refuses to collect**, shows a notification, writes state `no_indicator` (visible in `status`) and exits 0 so launchd does not respawn-loop.
* **Secrets** go to the macOS **Keychain** (service `vn.ics.tokenvault.adapter`); config/key files hold only `TVK1:` markers. Data: `~/Library/Application Support/TokenVault`; log: `~/Library/Logs/TokenVault/adapter.log`.
* **Uninstall:** right-click `Uninstall.command` -> Open (asks y/N). It boots out the LaunchAgent and removes the plist, app, Keychain items, data and logs.
* **Known limits:** `tokenvault-config.bin` stays next to the unzipped files after install (delete it by hand); items created via the `security` tool are readable by any process of the same user (comparable to DPAPI user scope). Keychain behaviour, launchctl timing, consent dialog size, SIGUSR1 reload while the menu-bar loop runs, FSEvents and Gatekeeper on Sonoma/Sequoia can only be checked on a real Mac.

## Distribution without a code-signing certificate
1. Hand files out over a **network share or USB** (or chat file transfer): no Mark-of-the-Web, no SmartScreen prompt.
2. For ZIP/exe that came through a browser or email: `Unblock-File .\file` (or Properties -> Unblock).
3. Optional, for IT: create a self-signed code-signing certificate, push it through **GPO -> Trusted Publishers** (and Trusted Root), then sign what you distribute. Sign the final team installer only after the Manager has appended the payload (or sign the Manager exe), never the template that will still be modified. Free, but optional and not tested by this project.
