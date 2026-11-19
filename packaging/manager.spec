# PyInstaller spec: TokenVault Manager (onedir, windowed).
# Build:  python -m PyInstaller packaging/manager.spec --noconfirm
# Output: dist/manager/TokenVault-Manager/  (Inno wraps this folder; build.ps1 copies
#         adapter-setup-template.exe next to the exe, which is where the Manager looks).
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"

datas = [
    (str(SRC / "tokenvault" / "ui"), "tokenvault/ui"),                 # HTML/CSS/JS + echarts
    (str(SRC / "tokenvault" / "manager" / "schema.sql"), "tokenvault/manager"),  # store.py
    (str(ROOT / "data" / "pricing.json"), "data"),                     # paths.pricing_path()
]
# reportlab ships its own font/resource files; PDF text uses TTFs from the Windows
# fonts folder (Segoe UI/Arial/...), see reports/fonts.py - none are bundled here.
datas += collect_data_files("reportlab")
datas += collect_data_files("webview")  # js helpers + WebView2 loader DLLs

a = Analysis(
    [str(ROOT / "packaging" / "entry_manager.py")],
    pathex=[str(SRC)],
    datas=datas,
    hiddenimports=[
        *collect_submodules("tokenvault.manager.reports"),
        "pystray._win32",
        "webview.platforms.edgechromium",  # WebView2 backend (pythonnet / clr_loader)
        "clr_loader",
    ],
    excludes=["tkinter", "pytest", "playwright", "pypdf", "pypdfium2", "mypy", "ruff"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="TokenVault-Manager",
    console=False, upx=False,
    version=str(ROOT / "packaging" / "version_manager.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="TokenVault-Manager", upx=False)
