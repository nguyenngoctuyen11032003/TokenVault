"""Builds the "no-exe" portable packages (Task 25) from a prepared runtime folder.

For machines whose Application Control refuses new unsigned exes: the package is
a ZIP with the official *embeddable Python* (python.exe / pythonw.exe are
Authenticode-signed by the Python Software Foundation), vendored wheels, the
TokenVault sources, and PowerShell/cmd scripts. Nothing in it is an unsigned exe.

Prepared runtime folder (created by ``build/build-portable.ps1``)::

    <runtime_dir>/runtime/    embeddable python + Lib/site-packages + app/tokenvault
    <runtime_dir>/scripts/    1-CAI-DAT.cmd, install.ps1, 1-CHAY-MANAGER.cmd, ... (optional;
                              falls back to packaging/portable of a source checkout)

Both ZIPs show only what a human clicks at the root; everything technical sits in ONE
folder, ``_he-thong`` ("system", do not touch).

Adapter ZIP (one per team, carries an enrollment credential - treat it like one)::

    DOC-TRUOC.txt  1-CAI-DAT.cmd  2-GO-CAI-DAT.cmd
    _he-thong/runtime/  install.ps1  uninstall.ps1  tokenvault-config.bin

Manager ZIP (generic, no credential)::

    DOC-TRUOC.txt  1-CHAY-MANAGER.cmd  2-CAI-MANAGER-VAO-MAY.cmd  3-GO-CAI-MANAGER.cmd
    _he-thong/runtime/  _he-thong/scripts/ (Install-Manager.ps1, Uninstall-Manager.ps1 and
    the adapter package scripts the Manager needs to build employee ZIPs)

``_he-thong`` is also a valid "runtime dir" (it holds ``runtime/`` and ``scripts/``), so an
extracted Manager ZIP builds employee packages with no further setup.

CLI (used by build-portable.ps1)::

    python -m tokenvault.manager.portable_packager manager-zip RUNTIME_DIR OUT.zip
"""

from __future__ import annotations

import contextlib
import logging
import os
import secrets
import sys
import zipfile
from collections.abc import Callable, Sequence
from pathlib import Path

from tokenvault.common.config_payload import PayloadConfig, append_payload
from tokenvault.manager.enrollment import Enrollment
from tokenvault.manager.packager import (
    BuiltInstaller,
    Packager,
    PackagerError,
    _payload_team,
    safe_team_name,
)
from tokenvault.manager.tls import CertInfo

log = logging.getLogger(__name__)

PAYLOAD_NAME = "tokenvault-config.bin"
_PAYLOAD_STUB = b"TokenVault portable payload carrier\r\n"
SYSTEM_DIR = "_he-thong"
DOC_NAME = "DOC-TRUOC.txt"
# source name (in the scripts dir) -> path inside the ZIP, adapter package
ADAPTER_ROOT = {"1-CAI-DAT.cmd": "1-CAI-DAT.cmd", "2-GO-CAI-DAT.cmd": "2-GO-CAI-DAT.cmd",
                "DOC-TRUOC.employee.txt": DOC_NAME}
ADAPTER_SYSTEM = ("install.ps1", "uninstall.ps1")
ADAPTER_SCRIPTS = (*ADAPTER_ROOT, *ADAPTER_SYSTEM)
# Manager package: clickable root files, and the helper scripts kept under _he-thong/scripts
MANAGER_ROOT = {"1-CHAY-MANAGER.cmd": "1-CHAY-MANAGER.cmd",
                "2-CAI-MANAGER-VAO-MAY.cmd": "2-CAI-MANAGER-VAO-MAY.cmd",
                "3-GO-CAI-MANAGER.cmd": "3-GO-CAI-MANAGER.cmd",
                "DOC-TRUOC.manager.txt": DOC_NAME}
MANAGER_SYSTEM = ("Install-Manager.ps1", "Uninstall-Manager.ps1")
MANAGER_SCRIPTS = (*MANAGER_ROOT, *MANAGER_SYSTEM)
# Packages the Adapter never imports (keeps the per-team ZIP small).
MANAGER_ONLY_DIST = ("webview", "pywebview", "clr_loader", "pythonnet", "proxy_tools",
                     "bottle", "reportlab", "charset_normalizer", "pypdf")
MANAGER_ONLY_APP = ("tokenvault/manager", "tokenvault/ui")
WINDOWS_PORTABLE = "windows-portable"


def _exe_dir() -> Path:
    return Path(sys.executable).resolve().parent


def _embedded_package_root() -> Path | None:
    """Running from the embeddable runtime (``<root>/runtime/app/tokenvault/manager``):
    the folder that holds ``runtime/`` (and ``scripts/``), else None. For an extracted
    Manager ZIP that is ``<zip root>/_he-thong``; for an Inno install ``{app}``."""
    rt = Path(__file__).resolve().parents[3]
    if rt.name.lower() == "runtime" and (rt / "python.exe").is_file():
        return rt.parent
    return None


def default_scripts_dir() -> Path:
    """Where the adapter package scripts live when the runtime folder has no ``scripts/``:
    next to the shipped exe, inside the portable Manager install, or in a source checkout."""
    if getattr(sys, "frozen", False):
        return _exe_dir() / "portable-runtime" / "scripts"
    root = _embedded_package_root()
    if root is not None:
        return root / "scripts"
    return Path(__file__).resolve().parents[3] / "packaging" / "portable"


def default_runtime_dir() -> Path:
    """The prepared runtime folder (``runtime/`` + ``scripts/``), for all three layouts:
    shipped Manager (``<exe dir>/portable-runtime``), portable Manager install/ZIP (the
    folder holding ``runtime/``), source checkout (``templates/portable-runtime``)."""
    env = os.environ.get("TOKENVAULT_PORTABLE_RUNTIME")
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        return _exe_dir() / "portable-runtime"
    root = _embedded_package_root()
    if root is not None:
        return root
    return Path(__file__).resolve().parents[3] / "templates" / "portable-runtime"


def runtime_ready(runtime_dir: Path) -> bool:
    r = Path(runtime_dir) / "runtime"
    return (r / "python.exe").is_file() and (r / "pythonw.exe").is_file()


def _is_manager_only(rel: str) -> bool:
    low = rel.lower()
    for name in MANAGER_ONLY_DIST:
        for base in ("lib/site-packages/",):
            if low.startswith(base):
                head = low[len(base):].split("/", 1)[0]
                if head == name or head.startswith(name + "-") or head.startswith(name + "."):
                    return True
    return any(low.startswith("app/" + p + "/") for p in MANAGER_ONLY_APP)


def _iter_runtime(runtime: Path, *, adapter_only: bool) -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    for p in sorted(runtime.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(runtime).as_posix()
        if adapter_only and _is_manager_only(rel):
            continue
        out.append((p, rel))
    return out


class PortablePackager:
    def __init__(
        self,
        runtime_dir: Path,
        enrollment: Enrollment,
        cert: CertInfo,
        out_dir: Path,
        endpoint: Callable[[], tuple[list[str], int]],
        manager_id: str,
        scripts_dir: Path | None = None,
    ) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.out_dir = Path(out_dir)
        self.scripts_dir = Path(scripts_dir) if scripts_dir else self._find_scripts()
        self.enrollment = enrollment
        self.cert = cert
        self.manager_id = manager_id
        # reuse the validation helpers of the exe packager (same rules, same messages)
        self._base = Packager(Path("unused"), enrollment, cert, out_dir, endpoint, manager_id)

    def _find_scripts(self) -> Path:
        inner = self.runtime_dir / "scripts"
        return inner if inner.is_dir() else default_scripts_dir()

    def _check(self) -> None:
        if not runtime_ready(self.runtime_dir):
            raise PackagerError(
                "The portable runtime is missing: run build\\build-portable.ps1 "
                f"(expected {self.runtime_dir / 'runtime' / 'python.exe'}).")
        missing = [n for n in ADAPTER_SCRIPTS if not (self.scripts_dir / n).is_file()]
        if missing:
            raise PackagerError(f"Portable package scripts are missing: {', '.join(missing)}")

    def _publish(self, tmp: Path, base: str) -> Path:
        n = 1
        while True:
            name = f"{base}.zip" if n == 1 else f"{base} ({n}).zip"
            final = self.out_dir / name
            try:
                os.link(tmp, final)
            except FileExistsError:
                n += 1
                continue
            except OSError:
                if final.exists():
                    n += 1
                    continue
                os.rename(tmp, final)
                return final
            with contextlib.suppress(OSError):
                tmp.unlink()
            return final

    def build_for_team(self, team: str, label: str | None) -> BuiltInstaller:
        if not team.strip():
            raise PackagerError("Team name must not be empty.")
        self._check()
        hosts, port = self._base._validated_endpoint()
        try:
            self.out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise PackagerError(f"Cannot create output folder {self.out_dir}: {e}") from e
        payload_team = _payload_team(team)
        try:
            PayloadConfig(self.manager_id, hosts, port, self.cert.fingerprint_sha256,
                          "x" * 43, payload_team).validate()
        except ValueError as e:
            raise PackagerError(f"Cannot build the package: {e}") from e

        pkg = self.enrollment.create_package(team, label)
        token = secrets.token_hex(8)
        tmp = self.out_dir / f".tvl-{token}.partial"
        stub = self.out_dir / f".tvl-{token}.stub"
        payload_file = self.out_dir / f".tvl-{token}.bin"
        final: Path | None = None
        try:
            cfg = PayloadConfig(self.manager_id, hosts, port, self.cert.fingerprint_sha256,
                                pkg.secret, payload_team)
            stub.write_bytes(_PAYLOAD_STUB)
            append_payload(stub, payload_file, cfg)
            _write_zip(tmp, self.runtime_dir / "runtime", self.scripts_dir, ADAPTER_ROOT,
                       ADAPTER_SYSTEM, adapter_only=True, extra={PAYLOAD_NAME: payload_file})
            final = self._publish(tmp, f"TokenVault-Adapter-{safe_team_name(team)}-portable")
            size = final.stat().st_size
        except BaseException as e:
            try:
                for leftover in (tmp, final):
                    if leftover is not None:
                        with contextlib.suppress(OSError):
                            leftover.unlink()
            finally:
                try:
                    self.enrollment.revoke_package(pkg.package_id)
                except Exception:  # noqa: BLE001
                    log.warning("could not revoke package %s after failed build", pkg.package_id)
            if isinstance(e, (ValueError, OSError, zipfile.BadZipFile)) and not isinstance(
                    e, PackagerError):
                raise PackagerError(f"Cannot build the package: {e}") from e
            raise
        finally:
            for f in (stub, payload_file):
                with contextlib.suppress(OSError):
                    f.unlink()
        log.info("built portable package %s for team %r (package %s)", final.name, team,
                 pkg.package_id)
        return BuiltInstaller(final, final.name, team, pkg.package_id, size)


def _write_zip(dst: Path, runtime: Path, scripts: Path, root_files: dict[str, str],
               system_files: Sequence[str], *, adapter_only: bool,
               extra: dict[str, Path] | None = None,
               nested_scripts: Sequence[str] = ()) -> None:
    """``root_files``: {source name: zip path at the root}; ``system_files`` go to
    ``_he-thong/`` (``_he-thong/scripts/`` for the Manager), ``nested_scripts`` always to
    ``_he-thong/scripts/`` so the extracted Manager can build adapter packages."""
    sysd = SYSTEM_DIR + "/"
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for src_name, arc in root_files.items():
            src = scripts / src_name
            if src.is_file():
                z.write(src, arc)
        for name in system_files:
            src = scripts / name
            if src.is_file():
                z.write(src, sysd + ("scripts/" if nested_scripts else "") + name)
        for name in nested_scripts:
            src = scripts / name
            if src.is_file():
                z.write(src, sysd + "scripts/" + name)
        for name, src in (extra or {}).items():
            z.write(src, sysd + name)
        for p, rel in _iter_runtime(runtime, adapter_only=adapter_only):
            z.write(p, sysd + "runtime/" + rel)


def build_manager_zip(runtime_dir: Path, out_zip: Path, scripts_dir: Path | None = None) -> Path:
    """Generic Manager portable ZIP (no credential inside)."""
    runtime_dir = Path(runtime_dir)
    if not runtime_ready(runtime_dir):
        raise PackagerError(f"The portable runtime is missing under {runtime_dir}")
    sd = Path(scripts_dir) if scripts_dir else (
        runtime_dir / "scripts" if (runtime_dir / "scripts").is_dir() else default_scripts_dir())
    missing = [n for n in (*MANAGER_SCRIPTS, *ADAPTER_SCRIPTS) if not (sd / n).is_file()]
    if missing:
        raise PackagerError(f"Manager package scripts are missing: {', '.join(missing)}")
    out_zip = Path(out_zip)
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_zip.with_name(out_zip.name + ".partial")
    try:
        # the nested copy keeps the adapter sources AND the Manager helpers under scripts/
        _write_zip(tmp, runtime_dir / "runtime", sd, MANAGER_ROOT, MANAGER_SYSTEM,
                   adapter_only=False, nested_scripts=ADAPTER_SCRIPTS)
        os.replace(tmp, out_zip)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()
    return out_zip


def main(argv: Sequence[str]) -> int:
    a = list(argv)
    if len(a) == 3 and a[0] == "manager-zip":
        try:
            p = build_manager_zip(Path(a[1]), Path(a[2]))
        except PackagerError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(p)
        return 0
    print("usage: portable_packager manager-zip RUNTIME_DIR OUT.zip", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
