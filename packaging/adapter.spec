# PyInstaller spec: TokenVault Adapter (single windowed exe, no admin needed).
# Build:  python -m PyInstaller packaging/adapter.spec --noconfirm
# Output: dist/adapter/TokenVaultAdapter.exe  (the Inno template installer embeds it)
from pathlib import Path

ROOT = Path(SPECPATH).parent  # repo root (SPECPATH = this folder)
LOGO = ROOT / "src" / "tokenvault" / "ui" / "assets" / "logo"  # common/brand.py reads these

a = Analysis(
    [str(ROOT / "packaging" / "entry_adapter.py")],
    pathex=[str(ROOT / "src")],  # always the sources of THIS checkout
    datas=[(str(f), "tokenvault/ui/assets/logo") for f in sorted(LOGO.glob("*.png"))],
    hiddenimports=[
        "pystray._win32",       # backend chosen at runtime
        "watchdog.observers.read_directory_changes",
    ],
    excludes=["webview", "reportlab", "tkinter.test", "pytest", "playwright"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="TokenVaultAdapter",
    console=False,          # windowed: no console flash at login; CLI re-attaches to parent
    upx=False,              # UPX triggers antivirus false positives
    runtime_tmpdir=None,
    version=str(ROOT / "packaging" / "version_adapter.txt"),
    icon=str(ROOT / "assets" / "logo" / "tokenvault.ico"),
)
