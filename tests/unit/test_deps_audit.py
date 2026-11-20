"""Dependency audit helper (packaging/release/deps_audit.py)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packaging" / "release"))
import deps_audit as da  # noqa: E402


def test_declared_dependencies_from_pyproject() -> None:
    decl = da.declared(ROOT / "pyproject.toml")
    assert "pywebview" in decl and "reportlab" in decl and "pillow" in decl
    assert "pytest" not in decl  # dev extra only


def test_closure_skips_extras() -> None:
    requires = {"reportlab": ["pillow>=9", "charset-normalizer", 'rlbidi; extra == "bidi"'],
                "pillow": [], "charset-normalizer": []}
    assert da.closure(["reportlab"], requires) == {"reportlab", "pillow", "charset-normalizer"}


def test_check_installed() -> None:
    ok = da.check_installed(["pillow"], [{"name": "Pillow", "version": "13.0.0"}])
    assert ok["status"] == da.PASS and ok["details"] == ["pillow==13.0.0"]
    bad = da.check_installed(["pillow", "reportlab"], [{"name": "Pillow", "version": "1"}])
    assert bad["status"] == da.FAIL and "reportlab" in bad["message"]


def test_bundle_flags_packages_outside_closure(tmp_path: Path) -> None:
    (tmp_path / "PIL").mkdir()
    (tmp_path / "PIL" / "_avif.pyd").write_bytes(b"x" * 2000)
    (tmp_path / "numpy").mkdir()
    (tmp_path / "numpy" / "core.pyd").write_bytes(b"x" * 10)
    (tmp_path / "numpy-2.0.dist-info").mkdir()
    pkg_map = {"PIL": ["Pillow"], "numpy": ["numpy"]}
    extra, heavy = da.check_bundle(tmp_path, {"pillow"}, pkg_map)
    assert extra["status"] == da.WARN
    assert len(extra["details"]) == 1 and extra["details"][0].startswith("numpy (numpy")
    assert heavy["details"][0].startswith("PIL/_avif.pyd")


def test_warn_file_summary(tmp_path: Path) -> None:
    w = tmp_path / "warn-manager.txt"
    w.write_text("missing module named posix - imported by os (conditional)\n"
                 "missing module named AppKit - imported by pystray._darwin (top-level)\n"
                 "missing module named Foundation.x - imported by y (top-level)\n")
    res = da.check_warn_file(w)
    assert res["status"] == da.PASS and "3 unresolved" in res["message"]
    assert res["details"] == ["top-level, unresolved: AppKit"]
