from __future__ import annotations

import logging
import sys

import pytest

from tokenvault.common import dpapi


@pytest.mark.skipif(sys.platform != "win32", reason="real DPAPI needs Windows")
def test_real_dpapi_round_trip_and_hides_plaintext() -> None:
    blob = dpapi.protect(b"top-secret-value")
    assert blob.startswith(dpapi.DPAPI_PREFIX)
    assert b"top-secret-value" not in blob
    assert dpapi.is_protected(blob)
    assert dpapi.unprotect(blob) == b"top-secret-value"


def test_non_windows_fails_closed_unless_plain_allowed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(dpapi.sys, "platform", "linux")
    with pytest.raises(dpapi.DpapiError):
        dpapi.protect(b"abc")
    with caplog.at_level(logging.WARNING, logger="tokenvault.common.dpapi"):
        blob = dpapi.protect(b"abc", allow_plain=True)
    assert blob == dpapi.PLAIN_PREFIX + b"abc"
    assert not dpapi.is_protected(blob)
    assert "NOT protected" in caplog.text
    assert dpapi.unprotect(blob) == b"abc"


def test_dpapi_failure_fails_closed_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(data: bytes) -> bytes:
        raise OSError("no dpapi here")

    monkeypatch.setattr(dpapi.sys, "platform", "win32")
    monkeypatch.setattr(dpapi, "_dpapi_protect", boom)
    with pytest.raises(dpapi.DpapiError):
        dpapi.protect(b"xyz")


def test_dpapi_failure_falls_back_only_with_explicit_flag(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(data: bytes) -> bytes:
        raise OSError("no dpapi here")

    monkeypatch.setattr(dpapi.sys, "platform", "win32")
    monkeypatch.setattr(dpapi, "_dpapi_protect", boom)
    monkeypatch.setattr(dpapi, "ALLOW_PLAIN", True)
    with caplog.at_level(logging.WARNING, logger="tokenvault.common.dpapi"):
        blob = dpapi.protect(b"xyz")
    assert blob == dpapi.PLAIN_PREFIX + b"xyz"
    assert "NOT protected" in caplog.text


def test_unprotect_rejects_unknown_and_undecryptable(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError):
        dpapi.unprotect(b"garbage")

    def refuse(data: bytes) -> bytes:
        raise OSError("wrong user")

    monkeypatch.setattr(dpapi, "_dpapi_unprotect", refuse)
    with pytest.raises(ValueError):
        dpapi.unprotect(dpapi.DPAPI_PREFIX + b"\x01\x02")


def test_blobs_interchangeable_with_manager_secrets() -> None:
    from tokenvault.manager import secrets as mgr

    assert dpapi.DPAPI_PREFIX == mgr.DPAPI_PREFIX
    assert dpapi.PLAIN_PREFIX == mgr.PLAIN_PREFIX
    assert dpapi.unprotect(mgr.protect(b"shared")) == b"shared"
    assert mgr.unprotect(dpapi.protect(b"shared2")) == b"shared2"
