"""Builds the per-team employee installer: prebuilt adapter setup exe + config payload.

Every build creates a NEW enrollment package (own revocable secret). The built
file contains that secret: treat it like a credential. The secret is never logged.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tokenvault.common.config_payload import PayloadConfig, append_payload
from tokenvault.manager.enrollment import Enrollment
from tokenvault.manager.tls import CertInfo

log = logging.getLogger(__name__)

MAX_NAME = 40
_RESERVED = {"CON", "PRN", "AUX", "NUL",
             *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class PackagerError(RuntimeError):
    """The installer cannot be built (message is user-presentable)."""


def safe_team_name(team: str) -> str:
    """Windows-filename-safe version of a team name (never empty, <= 40 chars)."""
    out = []
    for c in team:
        if c in " -_." or unicodedata.category(c)[0] in "LN":
            out.append(c)
        else:
            out.append("_")
    s = re.sub(r"_+", "_", "".join(out))[:MAX_NAME].rstrip(". ")
    if not s:
        return "Team"
    if s.split(".", 1)[0].rstrip(" ").upper() in _RESERVED:
        s = s.replace(".", "_")
        s = (s[: MAX_NAME - 1] if len(s) >= MAX_NAME else s) + "_"
    return s


def _payload_team(team: str) -> str:
    """Team label carried in the payload: raw name minus control chars / slashes."""
    s = "".join("_" if (ord(c) < 32 or ord(c) == 127 or c in "/\\") else c for c in team)
    return s.strip()[:MAX_NAME].strip() or "Team"


@dataclass(frozen=True)
class BuiltInstaller:
    path: Path
    file_name: str
    team: str
    package_id: str
    bytes: int


class Packager:
    def __init__(
        self,
        template: Path,
        enrollment: Enrollment,
        cert: CertInfo,
        out_dir: Path,
        endpoint: Callable[[], tuple[list[str], int]],
        manager_id: str,
    ) -> None:
        self.template = Path(template)
        self.enrollment = enrollment
        self.cert = cert
        self.out_dir = Path(out_dir)
        self.endpoint = endpoint
        self.manager_id = manager_id

    def _check_template(self) -> None:
        try:
            ok = self.template.is_file() and self.template.stat().st_size > 0
        except OSError:
            ok = False
        if not ok:
            raise PackagerError(f"Installer template is missing or empty: {self.template}")

    def _claim_path(self, base: str) -> Path:
        """Reserve a not-yet-existing file name in out_dir (numeric suffix, no overwrite)."""
        n = 1
        while True:
            name = f"{base}.exe" if n == 1 else f"{base} ({n}).exe"
            p = self.out_dir / name
            try:
                fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                n += 1
                continue
            except OSError as e:
                raise PackagerError(f"Cannot write to output folder {self.out_dir}: {e}") from e
            os.close(fd)
            return p

    def build_for_team(self, team: str, label: str | None) -> BuiltInstaller:
        self._check_template()
        hosts, port = self.endpoint()
        hosts = [h for h in hosts if h]
        if not hosts:
            raise PackagerError("The Manager has no reachable address to put in the installer.")
        try:
            self.out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise PackagerError(f"Cannot create output folder {self.out_dir}: {e}") from e

        pkg = self.enrollment.create_package(team, label)
        dst: Path | None = None
        try:
            cfg = PayloadConfig(
                manager_id=self.manager_id, hosts=hosts, port=port,
                fingerprint=self.cert.fingerprint_sha256, secret=pkg.secret,
                team=_payload_team(team),
            )
            cfg.validate()
            dst = self._claim_path(f"TokenVault-Adapter-{safe_team_name(team)}")
            append_payload(self.template, dst, cfg)
            size = dst.stat().st_size
        except (ValueError, OSError, PackagerError) as e:
            self.enrollment.revoke_package(pkg.package_id)
            if dst is not None:
                with contextlib.suppress(OSError):
                    dst.unlink()
            if isinstance(e, PackagerError):
                raise
            raise PackagerError(f"Cannot build the installer: {e}") from e
        log.info("built installer %s for team %r (package %s)", dst.name, team, pkg.package_id)
        return BuiltInstaller(dst, dst.name, team, pkg.package_id, size)
