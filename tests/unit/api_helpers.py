"""Shared fixtures/helpers for the Api unit tests (test_api*.py)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tokenvault.manager.demo_data import seed_now
from tokenvault.manager.firewall import FirewallStatus
from tokenvault.manager.lifecycle import Manager, ManagerConfig
from tokenvault.manager.paths import pricing_path

PREFIX = "window.tv && window.tv.onLive && window.tv.onLive("
FW_OK = FirewallStatus(True, True, True, "ok")
FW_MISSING = FirewallStatus(False, False, True, "TCP rule missing")


def make_cfg(root: Path, **kw: Any) -> ManagerConfig:
    tpl = root / "adapter-setup-template.exe"
    if not tpl.exists():
        tpl.write_bytes(b"MZ" + b"\0" * 4096)
    base: dict[str, Any] = dict(
        data_dir=root / "data", template=tpl, pricing=pricing_path(), ingest=True,
        ingest_host="127.0.0.1", ingest_port=0, discovery=False, tz_offset_minutes=420,
        firewall_check=lambda port: FW_OK, presence_interval=0.2,
        hosts_provider=lambda: ["192.168.1.20", "pc-quanly"], reveal=None, autostart=None,
    )
    base.update(kw)
    return ManagerConfig(**base)


@pytest.fixture
def manager(tmp_path: Path) -> Iterator[Manager]:
    m = Manager(make_cfg(tmp_path))
    m.start()
    yield m
    m.stop()


@pytest.fixture
def demo(manager: Manager) -> Manager:
    seed_now(manager.store, manager.enrollment, manager.tz_offset_minutes)
    return manager


def call_error(fn: Any, *args: Any) -> str:
    with pytest.raises(Exception) as ei:  # noqa: PT011 - pywebview forwards plain Exceptions
        fn(*args)
    assert type(ei.value) is Exception
    return str(ei.value)


