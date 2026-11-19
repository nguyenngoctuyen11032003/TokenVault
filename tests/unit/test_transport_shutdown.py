"""Transport must stop (not spin) when the interpreter is shutting down."""

from __future__ import annotations

import time

import pytest

from tokenvault.adapter.transport import Endpoint, Transport
from tokenvault.common.protocol import Hello

FP = "ab" * 32


class EmptyOutbox:
    def pending(self, limit: int) -> list[object]:
        return []

    def ack(self, sent: object) -> int:
        return 0


def _hello() -> Hello:
    return Hello("dev", "s" * 40, None, "Dev", 1)


@pytest.mark.parametrize(
    "message",
    ["cannot schedule new futures after shutdown", "Executor shutdown has been called"],
)
def test_resolver_shutdown_error_ends_the_loop(message: str) -> None:
    calls: list[float] = []

    def resolver() -> list[str]:
        calls.append(time.monotonic())
        raise RuntimeError(message)

    states: list[str] = []
    t = Transport(EmptyOutbox(), Endpoint([], 47800, FP), _hello, on_state=states.append,
                  resolver=resolver, backoff_min=0.01, backoff_max=0.02)
    t.start()
    try:
        deadline = time.monotonic() + 1.0
        while t.running and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not t.running, "transport loop kept running after a shutdown RuntimeError"
        assert len(calls) == 1  # no retries
        assert t.state == "offline"
    finally:
        t.stop(timeout=2)


def test_other_resolver_errors_still_retry() -> None:
    calls: list[float] = []

    def resolver() -> list[str]:
        calls.append(time.monotonic())
        raise RuntimeError("something else")

    t = Transport(EmptyOutbox(), Endpoint([], 47800, FP), _hello, resolver=resolver,
                  backoff_min=0.01, backoff_max=0.02)
    t.start()
    try:
        deadline = time.monotonic() + 2.0
        while len(calls) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(calls) >= 3 and t.running
    finally:
        t.stop(timeout=2)
    assert not t.running
