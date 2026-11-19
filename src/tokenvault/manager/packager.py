"""Builds the per-team employee installer: prebuilt adapter setup exe + config payload.

Every build creates a NEW enrollment package (own revocable secret).

README: the built installer file CONTAINS an enrollment credential. Handle it
like a credential (share only with the intended team); it works only against the
pinned Manager and the package can be revoked. The secret is never logged.
"""

from __future__ import annotations

import contextlib
import logging
import os
import secrets
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tokenvault.common.config_payload import (
    MAX_HOSTS,
    PayloadConfig,
    append_payload,
    valid_host,
)
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
    team = unicodedata.normalize("NFC", team).strip()
    out: list[str] = []
    for c in team:
        if c in " -_" or unicodedata.category(c)[0] in "LNM":
            out.append(c)
        elif not out or out[-1] != "_":
            out.append("_")
    s = "".join(out)
    while "__" in s:
        s = s.replace("__", "_")
    s = s[:MAX_NAME].rstrip(". ")
    if not s:
        return "Team"
    # Defensive: '.' is already replaced, so only a bare reserved device name can remain.
    if s.upper() in _RESERVED:
        s += "_"
    return s


def _payload_team(team: str) -> str:
    """Team label carried in the payload: raw name minus control chars / slashes."""
    team = unicodedata.normalize("NFC", team)
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

    def _publish(self, tmp: Path, base: str) -> Path:
        """Move ``tmp`` to a not-yet-existing final name (numeric suffix, never overwrite)."""
        n = 1
        while True:
            name = f"{base}.exe" if n == 1 else f"{base} ({n}).exe"
            final = self.out_dir / name
            try:
                os.link(tmp, final)  # atomic, fails if ``final`` exists
            except FileExistsError:
                n += 1
                continue
            except OSError:
                # hard links unsupported: Windows rename also refuses to overwrite
                if final.exists():
                    n += 1
                    continue
                os.rename(tmp, final)
                return final
            with contextlib.suppress(OSError):
                tmp.unlink()
            return final

    def _validated_endpoint(self) -> tuple[list[str], int]:
        hosts, port = self.endpoint()
        seen: set[str] = set()
        clean: list[str] = []
        for h in hosts:
            if not valid_host(h):
                raise PackagerError(f"Manager address {h!r} is not a valid IP or host name.")
            if h.lower() not in seen:
                seen.add(h.lower())
                clean.append(h.lower())
        if not clean:
            raise PackagerError("The Manager has no reachable address to put in the installer.")
        if len(clean) > MAX_HOSTS:
            clean = clean[:MAX_HOSTS]
        if type(port) is not int or not 1 <= port <= 65535:
            raise PackagerError(f"The Manager port {port!r} is not valid.")
        return clean, port

    def build_for_team(self, team: str, label: str | None) -> BuiltInstaller:
        if not team.strip():
            raise PackagerError("Team name must not be empty.")
        self._check_template()
        hosts, port = self._validated_endpoint()
        try:
            self.out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise PackagerError(f"Cannot create output folder {self.out_dir}: {e}") from e
        payload_team = _payload_team(team)
        try:  # fail before any package exists
            PayloadConfig(self.manager_id, hosts, port, self.cert.fingerprint_sha256,
                          "x" * 43, payload_team).validate()
        except ValueError as e:
            raise PackagerError(f"Cannot build the installer: {e}") from e

        pkg = self.enrollment.create_package(team, label)
        tmp = self.out_dir / f".tvl-{secrets.token_hex(8)}.partial"
        final: Path | None = None
        try:
            cfg = PayloadConfig(self.manager_id, hosts, port, self.cert.fingerprint_sha256,
                                pkg.secret, payload_team)
            append_payload(self.template, tmp, cfg)
            final = self._publish(tmp, f"TokenVault-Adapter-{safe_team_name(team)}")
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
            if isinstance(e, (ValueError, OSError)) and not isinstance(e, PackagerError):
                raise PackagerError(f"Cannot build the installer: {e}") from e
            raise
        log.info("built installer %s for team %r (package %s)", final.name, team, pkg.package_id)
        return BuiltInstaller(final, final.name, team, pkg.package_id, size)
