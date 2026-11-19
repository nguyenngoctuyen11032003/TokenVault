"""Manager TLS identity: a self-signed certificate whose SHA-256 is pinned by adapters.

Choices
-------
* **ECDSA P-256** key (fast to generate, small, supported by every TLS 1.2+
  stack). Validity 10 years; regenerated only when the files are missing,
  unreadable, inconsistent or within 30 days of expiry. Regenerating changes
  the fingerprint, which orphans every installer already handed out, so it is
  deliberately rare and logged as a warning.
* SAN: machine hostname, ``localhost``, ``127.0.0.1`` and the LAN IPv4
  addresses seen at generation time. Adapters pin the fingerprint and do not
  check host names, so a later IP change does NOT trigger regeneration.
* The private key is written as encrypted PKCS#8 (``BestAvailableEncryption``)
  under a random password; the password is stored next to it protected with
  DPAPI (:mod:`tokenvault.manager.secrets`). A copied key file alone is
  therefore useless on another account/machine. The file mode is also
  restricted best-effort (on Windows this only clears write bits; the
  per-user profile ACL is the real file guard).
"""

from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import logging
import os
import secrets
import socket
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from tokenvault.manager import secrets as tv_secrets

log = logging.getLogger(__name__)

CERT_FILE = "manager-cert.pem"
KEY_FILE = "manager-key.pem"
KEY_PASS_SUFFIX = ".pass"  # DPAPI-protected key password: <key_path>.pass
VALIDITY = timedelta(days=3650)
RENEW_BEFORE = timedelta(days=30)


@dataclass(frozen=True)
class CertInfo:
    cert_path: Path
    key_path: Path
    fingerprint_sha256: str  # lowercase hex SHA-256 of the DER certificate


def fingerprint_of_der(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def short_fp(fp: str) -> str:
    """Truncated fingerprint for logs."""
    return fp[:12] + "..."


def _pass_path(key_path: Path) -> Path:
    return key_path.with_name(key_path.name + KEY_PASS_SUFFIX)


def _lan_ipv4() -> set[str]:
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(str(info[4][0]))
    except OSError:
        pass
    try:  # primary outbound interface; a UDP "connect" sends no packet
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.add(str(s.getsockname()[0]))
    except OSError:
        pass
    ips.discard("0.0.0.0")
    return ips


def _san() -> x509.SubjectAlternativeName:
    names: list[x509.GeneralName] = []
    host = socket.gethostname()
    for dns in dict.fromkeys([host, "localhost"]):
        if dns:
            names.append(x509.DNSName(dns))
    for ip in sorted({"127.0.0.1", *_lan_ipv4()}):
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            continue
    return x509.SubjectAlternativeName(names)


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _restrict(path: Path) -> None:
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def _write_new_certificate(directory: Path, valid_for: timedelta = VALIDITY) -> CertInfo:
    directory.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "TokenVault Manager"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "TokenVault"),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + valid_for)
        .add_extension(_san(), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    password = secrets.token_urlsafe(32).encode()
    key_path = directory / KEY_FILE
    cert_path = directory / CERT_FILE
    _atomic_write(_pass_path(key_path), tv_secrets.protect(password))
    _atomic_write(
        key_path,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(password),
        ),
    )
    _restrict(key_path)
    _restrict(_pass_path(key_path))
    _atomic_write(cert_path, cert.public_bytes(serialization.Encoding.PEM))
    fp = fingerprint_of_der(cert.public_bytes(serialization.Encoding.DER))
    log.warning("generated a new Manager certificate, fingerprint %s", short_fp(fp))
    return CertInfo(cert_path, key_path, fp)


def _key_password(key_path: Path) -> bytes:
    return tv_secrets.unprotect(_pass_path(key_path).read_bytes())


def _load_existing(directory: Path) -> CertInfo:
    cert_path = directory / CERT_FILE
    key_path = directory / KEY_FILE
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    key = serialization.load_pem_private_key(
        key_path.read_bytes(), password=_key_password(key_path)
    )
    pub = serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    if key.public_key().public_bytes(*pub) != cert.public_key().public_bytes(*pub):
        raise ValueError("private key does not match certificate")
    if cert.not_valid_after_utc - datetime.now(UTC) < RENEW_BEFORE:
        raise ValueError("certificate expires soon")
    return CertInfo(
        cert_path, key_path, fingerprint_of_der(cert.public_bytes(serialization.Encoding.DER))
    )


def ensure_certificate(directory: Path) -> CertInfo:
    """Load the Manager certificate from ``directory``, creating it if needed."""
    directory = Path(directory)
    if (directory / CERT_FILE).exists() or (directory / KEY_FILE).exists():
        try:
            return _load_existing(directory)
        except Exception as e:  # unreadable/inconsistent/expiring -> regenerate
            log.warning("Manager certificate unusable (%s); regenerating", e)
    return _write_new_certificate(directory)


def load_server_context(cert: CertInfo) -> ssl.SSLContext:
    """TLS >= 1.2 server context serving ``cert`` (key password via DPAPI)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    password = _key_password(cert.key_path)
    ctx.load_cert_chain(str(cert.cert_path), str(cert.key_path), password=lambda: password)
    return ctx
