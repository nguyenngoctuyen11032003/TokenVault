# Packaging

Build machine: Windows 10/11 x64, Python 3.11+ with the project and `pyinstaller`
installed, and [Inno Setup 6.3 or newer](https://jrsoftware.org/isdl.php)
(`winget install --id JRSoftware.InnoSetup -e`).

```powershell
powershell -File packaging\build.ps1              # full build
powershell -File tests\packaging\test_build_outputs.ps1   # smoke test
```

`build.ps1` order: adapter (PyInstaller onefile) -> `adapter-setup-template.exe` (Inno,
per user, unsigned) -> Manager (PyInstaller onedir, template placed next to the exe) ->
WebView2 bootstrapper (downloaded to `packaging/redist/`, git-ignored) ->
`TokenVault-Manager-Setup.exe` (Inno). Outputs are in `dist/`; work files in `build/`.
Without ISCC the script builds the PyInstaller outputs, prints an install hint and exits 3.

| File | Purpose |
| --- | --- |
| `adapter.spec`, `manager.spec` | PyInstaller specs (`entry_*.py` are the entry points) |
| `adapter-template.iss` | per-user installer; runs `TokenVaultAdapter.exe setup --from "{srcexe}"`; upgrade stops the agent via its reload event (taskkill of its own exe as fallback); uninstall removes the HKCU Run value |
| `manager.iss` | per-user installer (`PrivilegesRequired=lowest`); only the firewall step is elevated (`ShellExec runas` of `TokenVault-Manager.exe firewall --ensure`, skipped on upgrade when the rules already exist); WebView2 check/bootstrapper; uninstall removes every `TokenVault-Manager-*` rule, whatever the base port |

Rules that matter:

* The template must stay **unsigned**: the Manager appends the config payload (`TVLTCFG1`)
  to a copy of it, which would invalidate a signature. Nothing here is signed; SmartScreen
  will warn on first run.
* The built team installer contains an enrollment secret: treat it like a credential.
* Installer test switches: template `/ADAPTERARGS="--silent --accept --no-start --no-autostart --data-dir X"`;
  Manager `/NOFIREWALL=1 /NOWEBVIEW2=1 /PORT=47800`.
* PDF reports use TTFs from the Windows fonts folder (Segoe UI/Arial/...); none are bundled.

## No-exe portable packages (works without a code-signing certificate)

Some machines run Application Control / Smart App Control and refuse every new unsigned exe
(not just a SmartScreen warning). For them TokenVault ships ZIPs that contain **no unsigned exe**:
the official embeddable Python (python.exe / pythonw.exe are Authenticode-signed by the Python
Software Foundation), vendored wheels, TokenVault's Python source and `.cmd`/`.ps1` scripts.

* Prepare once: `build\build-portable.ps1` (downloads python-3.12.10-embed-amd64.zip, verifies its
  SHA-256 and the Authenticode publisher, pip-installs cp312-win_amd64 wheels with `--target`,
  copies tkinter for the consent screen, compiles, writes `templates\portable-runtime\`, and
  builds `dist\TokenVault-Manager-portable.zip`).
* Per team: Manager -> create installer with target `windows-portable`
  (`Api.create_installer(team, label, "windows-portable")`) -> `TokenVault-Adapter-<team>-portable.zip`.
  Employee: extract, double-click `Install.cmd` (per user, no admin, consent screen as usual).
  Remove: `Uninstall.cmd`.
* Manager: extract `TokenVault-Manager-portable.zip`; `Start-Manager.cmd` runs it in place,
  `Install-Manager.ps1` adds Start-menu/Desktop shortcuts and one elevated firewall step.
* Check the runtime: `runtime\python.exe -m tokenvault.adapter --selftest` (or
  `-m tokenvault.manager --selftest`) prints a `runtime:` line with the signature status.
* Hand the ZIP out via a network share, USB stick or chat file transfer (no Mark-of-the-Web, so no
  SmartScreen). A ZIP received through a browser/e-mail is unblocked by the scripts (`Unblock-File`).
* Optional free path for IT: a self-signed code-signing certificate distributed through GPO
  "Trusted Publishers" / "Trusted Root" (not required by anything here).
* Note: the first launch of a freshly copied pythonw.exe can take about two minutes on machines
  with strong real-time scanning (install.ps1 simply waits); the wheels' native modules
  (cryptography, Pillow, ...) are not Authenticode-signed - a policy that blocks unsigned DLLs
  would also block those (it did not on the development machine).
