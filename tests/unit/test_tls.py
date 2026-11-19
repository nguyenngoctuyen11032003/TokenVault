from __future__ import annotations

import hashlib
import logging
import socket
import ssl
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from tokenvault.manager import secrets as tv_secrets
from tokenvault.manager.tls import (
    CertInfo,
    _write_new_certificate,
    ensure_certificate,
    load_server_context,
)


def _serve_once(ctx: ssl.SSLContext) -> tuple[int, threading.Thread]:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    port = srv.getsockname()[1]

    def run() -> None:
        with srv:
            conn, _ = srv.accept()
            try:
                with ctx.wrap_socket(conn, server_side=True) as tls:
                    tls.recv(1)
            except (OSError, ssl.SSLError):
                pass

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return port, t


def test_fingerprint_matches_served_cert(tmp_path: Path) -> None:
    info = ensure_certificate(tmp_path)
    cert = x509.load_pem_x509_certificate(info.cert_path.read_bytes())
    der = cert.public_bytes(serialization.Encoding.DER)
    assert info.fingerprint_sha256 == hashlib.sha256(der).hexdigest()
    assert len(info.fingerprint_sha256) == 64 and info.fingerprint_sha256.islower()

    port, t = _serve_once(load_server_context(info))
    cctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    cctx.check_hostname = False
    cctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection(("127.0.0.1", port), timeout=10) as raw:
        with cctx.wrap_socket(raw) as tls:
            served = tls.getpeercert(binary_form=True)
            assert served is not None
            assert hashlib.sha256(served).hexdigest() == info.fingerprint_sha256
            assert tls.version() in ("TLSv1.2", "TLSv1.3")
            tls.send(b"x")
    t.join(10)


def test_server_context_requires_tls12(tmp_path: Path) -> None:
    ctx = load_server_context(ensure_certificate(tmp_path))
    assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_certificate_reused_when_present(tmp_path: Path) -> None:
    a = ensure_certificate(tmp_path)
    b = ensure_certificate(tmp_path)
    assert a == b


def test_certificate_regenerated_when_unreadable(tmp_path: Path) -> None:
    a = ensure_certificate(tmp_path)
    a.cert_path.write_bytes(b"garbage")
    b = ensure_certificate(tmp_path)
    assert b.fingerprint_sha256 != a.fingerprint_sha256
    load_server_context(b)


def test_certificate_regenerated_when_key_missing(tmp_path: Path) -> None:
    a = ensure_certificate(tmp_path)
    a.key_path.unlink()
    b = ensure_certificate(tmp_path)
    assert b.fingerprint_sha256 != a.fingerprint_sha256


def test_certificate_regenerated_near_expiry(tmp_path: Path) -> None:
    old = _write_new_certificate(tmp_path, valid_for=timedelta(days=5))
    new = ensure_certificate(tmp_path)
    assert new.fingerprint_sha256 != old.fingerprint_sha256


def test_certificate_validity_and_san(tmp_path: Path) -> None:
    info = ensure_certificate(tmp_path)
    cert = x509.load_pem_x509_certificate(info.cert_path.read_bytes())
    life = cert.not_valid_after_utc - cert.not_valid_before_utc
    assert life >= timedelta(days=3650)
    assert cert.not_valid_after_utc > datetime.now(UTC) + timedelta(days=3600)
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    dns = san.get_values_for_type(x509.DNSName)
    ips = {str(i) for i in san.get_values_for_type(x509.IPAddress)}
    assert "localhost" in dns
    assert socket.gethostname() in dns
    assert "127.0.0.1" in ips


def test_private_key_encrypted_at_rest(tmp_path: Path) -> None:
    info = ensure_certificate(tmp_path)
    assert b"ENCRYPTED PRIVATE KEY" in info.key_path.read_bytes()


def test_certinfo_is_a_plain_value() -> None:
    c = CertInfo(Path("a"), Path("b"), "ff")
    assert c.fingerprint_sha256 == "ff"


# ------------------------------------------------------------------ secrets


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
def test_dpapi_roundtrip_is_not_plaintext() -> None:
    blob = tv_secrets.protect(b"top-secret-value")
    assert b"top-secret-value" not in blob
    assert tv_secrets.unprotect(blob) == b"top-secret-value"
    assert tv_secrets.is_protected(blob)


def test_fallback_is_flagged_and_roundtrips(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(_data: bytes) -> bytes:
        raise OSError("no dpapi")

    monkeypatch.setattr(tv_secrets, "_dpapi_protect", boom)
    with caplog.at_level(logging.WARNING, logger="tokenvault.manager.secrets"):
        blob = tv_secrets.protect(b"abc")
    assert blob.startswith(tv_secrets.PLAIN_PREFIX)
    assert not tv_secrets.is_protected(blob)
    assert any("NOT protected" in r.getMessage() for r in caplog.records)
    assert tv_secrets.unprotect(blob) == b"abc"


def test_non_windows_uses_flagged_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tv_secrets.sys, "platform", "linux")
    blob = tv_secrets.protect(b"xyz")
    assert blob.startswith(tv_secrets.PLAIN_PREFIX)
    assert tv_secrets.unprotect(blob) == b"xyz"


def test_unprotect_rejects_unknown_format() -> None:
    with pytest.raises(ValueError):
        tv_secrets.unprotect(b"something else")
