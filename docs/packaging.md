# Packaging and distribution

Build details live in `packaging/README.md`. Summary and the three delivery flavours follow.

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
* Who it suits: machines with ordinary SmartScreen ("More info" -> "Run anyway").

## B. Windows "no-exe" portable package (Task 25 - in progress, sẽ cập nhật sau khi hoàn thiện)
Status: **planned/being implemented in parallel; not verified here.** From the plan: for machines whose Application Control policy refuses any new unsigned exe.
* ZIP containing the official embeddable Python (its `python.exe`/`pythonw.exe` are signed by the Python Software Foundation), the adapter as `.py`/`.pyc`, vendored wheels, the sidecar `tokenvault-config.bin`, `Install.cmd` + `install.ps1`.
* Install is per user, no admin: copies to `%LOCALAPPDATA%\TokenVault\Adapter`, runs `pythonw.exe -m tokenvault.adapter setup --from <payload>`, HKCU Run starts `pythonw.exe -m tokenvault.adapter run`. `Install.cmd` also runs `Unblock-File`.
* Manager equivalent: `Start-Manager.cmd` + `Install-Manager.ps1`, shortcuts, firewall via one elevated `netsh` step.
* Build helper: `build/build-portable.ps1` downloads the embeddable Python, verifies SHA-256 and the Authenticode publisher, installs wheels with `pip --target`, compiles `.pyc`. "Create installer" will offer target `windows-portable`.
Caveat from the plan: `.pyd` files inside wheels (websockets, cryptography, watchdog, Pillow...) are not signed by Python, so a very strict policy may still block them; this is to be confirmed.

## C. macOS ZIP (Task 24 - in progress, sẽ cập nhật sau khi hoàn thiện)
Status: **planned; real-Mac verification pending.** Windows remains the primary tested platform.
* PyInstaller cannot cross-compile: the Mac template is built on a Mac with `build/build-mac.sh`; the Manager looks for it in its data dir (`templates/adapter-mac.zip`) and otherwise reports `[template_missing]`.
* Output `TokenVault-Adapter-<team>-mac.zip`: unsigned `TokenVault Adapter.app`, sidecar `tokenvault-config.bin`, `Install.command`. Double-click `Install.command`: removes the quarantine attribute, copies the app to `~/Applications`, runs `adapter setup --from <payload>`, registers a LaunchAgent (`vn.ics.tokenvault.adapter`).
* Gatekeeper: on first launch the user may need System Settings -> Privacy & Security -> "Open Anyway".
* Secrets in the Keychain, data in `~/Library/Application Support/TokenVault`, logs in `~/Library/Logs/TokenVault`.

## Distribution without a code-signing certificate
1. Hand files out over a **network share or USB** (or chat file transfer): no Mark-of-the-Web, no SmartScreen prompt.
2. For ZIP/exe that came through a browser or email: `Unblock-File .\file` (or Properties -> Unblock).
3. Optional, for IT: create a self-signed code-signing certificate, push it through **GPO -> Trusted Publishers** (and Trusted Root), then sign what you distribute. Sign the final team installer only after the Manager has appended the payload (or sign the Manager exe), never the template that will still be modified. Free, but optional and not tested by this project.
