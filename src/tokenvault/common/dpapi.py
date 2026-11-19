"""Protect small secrets at rest with Windows DPAPI (current-user scope).

Blobs are self-describing:

* ``TVD1:`` + DPAPI ciphertext - protected; only the same Windows user on the
  same machine can unprotect it.
* ``TVP1:`` + plain bytes - **NOT protected**. Written ONLY when the caller
  explicitly allows it (``allow_plain=True`` or module flag ``ALLOW_PLAIN``,
  meant for non-Windows test runs); every such write logs a warning. Otherwise a
  DPAPI failure raises :class:`DpapiError` (fail closed: no plaintext secret on
  disk).

``unprotect`` accepts both and raises ``ValueError`` on anything else (or when
DPAPI refuses to decrypt, e.g. another user's blob).

The Manager uses this same module (package secrets, TLS key password), so there is
a single implementation and no plaintext fallback outside explicit test opt-in.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from typing import Any

log = logging.getLogger(__name__)

DPAPI_PREFIX = b"TVD1:"
PLAIN_PREFIX = b"TVP1:"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1
_ENTROPY = b"TokenVault.v1"
ALLOW_PLAIN = False  # tests / non-Windows dev only


class DpapiError(OSError):
    """DPAPI could not protect the data and the plaintext fallback is not allowed."""


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> tuple[_Blob, Any]:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _call(fn_name: str, data: bytes) -> bytes:
    win_dll: Any = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise OSError("DPAPI needs Windows")
    crypt32 = win_dll("crypt32", use_last_error=True)
    kernel32 = win_dll("kernel32", use_last_error=True)
    fn = getattr(crypt32, fn_name)
    fn.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    src, _keep = _blob(data)
    ent, _keep2 = _blob(_ENTROPY)
    out = _Blob()
    ok = fn(
        ctypes.byref(src),
        None,
        ctypes.byref(ent),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out),
    )
    if not ok:
        raise OSError(f"{fn_name} failed (error {ctypes.get_last_error() or 'unknown'})")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def _dpapi_protect(data: bytes) -> bytes:
    return _call("CryptProtectData", data)


def _dpapi_unprotect(data: bytes) -> bytes:
    return _call("CryptUnprotectData", data)


def protect(data: bytes, *, allow_plain: bool | None = None) -> bytes:
    """Return a blob of ``data`` protected for the current Windows user.

    Raises :class:`DpapiError` if DPAPI is unavailable/failed, unless the plain
    fallback is explicitly allowed.
    """
    plain_ok = ALLOW_PLAIN if allow_plain is None else allow_plain
    if sys.platform == "win32":
        try:
            return DPAPI_PREFIX + _dpapi_protect(data)
        except Exception as e:  # any DPAPI failure
            if not plain_ok:
                raise DpapiError(f"DPAPI failed: {e}") from e
            log.warning("DPAPI unavailable (%s); secret stored NOT protected", e)
    else:
        if not plain_ok:
            raise DpapiError("DPAPI needs Windows")
        log.warning("not on Windows; secret stored NOT protected (plain fallback)")
    return PLAIN_PREFIX + data


def unprotect(data: bytes) -> bytes:
    """Inverse of :func:`protect`. Raises ``ValueError`` if the blob is unusable."""
    if data.startswith(PLAIN_PREFIX):
        return data[len(PLAIN_PREFIX) :]
    if data.startswith(DPAPI_PREFIX):
        try:
            return _dpapi_unprotect(data[len(DPAPI_PREFIX) :])
        except OSError as e:
            raise ValueError(f"cannot unprotect secret: {e}") from e
    raise ValueError("unknown secret blob format")


def is_protected(data: bytes) -> bool:
    """True if ``data`` is a DPAPI blob (not the plain fallback)."""
    return data.startswith(DPAPI_PREFIX)
