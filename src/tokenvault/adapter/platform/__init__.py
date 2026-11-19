"""Platform seam for the Adapter.

Windows is the primary platform and its code stays in the original modules
(``autostart``, ``singleton``, ``common.dpapi`` ...). The macOS implementation
lives in :mod:`tokenvault.adapter.platform.mac`; the original modules dispatch to
it when :func:`is_mac` is true. Nothing here imports a win32-only or a mac-only
module at import time, so every module imports on every OS.
"""

from __future__ import annotations

import sys


def is_mac(platform: str | None = None) -> bool:
    return (platform or sys.platform) == "darwin"
