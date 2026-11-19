# Packaging

Build machine: Windows 10/11 x64, Python 3.11+ with the project and `pyinstaller`
installed, and [Inno Setup 6](https://jrsoftware.org/isdl.php)
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
| `manager.iss` | per-machine installer (admin only for firewall rules via `TokenVault-Manager.exe firewall --ensure`); WebView2 check/bootstrapper; uninstall removes every `TokenVault-Manager-*` rule, whatever the base port |

Rules that matter:

* The template must stay **unsigned**: the Manager appends the config payload (`TVLTCFG1`)
  to a copy of it, which would invalidate a signature. Nothing here is signed; SmartScreen
  will warn on first run.
* The built team installer contains an enrollment secret: treat it like a credential.
* Installer test switches: template `/ADAPTERARGS="--silent --accept --no-start --no-autostart --data-dir X"`;
  Manager `/NOFIREWALL=1 /NOWEBVIEW2=1 /PORT=47800`.
* PDF reports use TTFs from the Windows fonts folder (Segoe UI/Arial/...); none are bundled.
