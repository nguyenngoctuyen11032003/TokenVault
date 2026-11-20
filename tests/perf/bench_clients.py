"""N simulated adapters in ONE process (bench helper): the real adapter ``Transport``
(pinned TLS WebSocket, Hello/Welcome, Batch/Receipt) over in-memory outboxes.

    python bench_clients.py --installer X --clients 50

stdout: one JSON line ``{"connected": n}`` once every client is connected; then for
each stdin line ``send <per_client>`` the clients enqueue that many events each and
the line ``{"sent": total, "seconds": s}`` follows when every one was acknowledged.
``stop`` (or EOF) ends the process.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tokenvault.adapter.outbox import PendingEvent  # noqa: E402
from tokenvault.adapter.transport import Endpoint, Transport  # noqa: E402
from tokenvault.common.config_payload import read_payload  # noqa: E402
from tokenvault.common.models import UsageEvent  # noqa: E402
from tokenvault.common.protocol import PROTOCOL_VERSION, Hello  # noqa: E402


class MemOutbox:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: dict[str, PendingEvent] = {}
        self.acked = 0

    def add(self, events: list[UsageEvent]) -> None:
        with self._lock:
            for e in events:
                self._rows[e.message_id or e.uuid] = PendingEvent(e.message_id or e.uuid, 0, e)

    def pending(self, limit: int) -> list[PendingEvent]:
        with self._lock:
            return list(self._rows.values())[:limit]

    def ack(self, sent: Any) -> int:
        n = 0
        with self._lock:
            for m, _r in sent:
                if self._rows.pop(m, None) is not None:
                    n += 1
            self.acked += n
        return n

    def waiting(self) -> int:
        with self._lock:
            return len(self._rows)


class Client:
    def __init__(self, i: int, payload: Any) -> None:
        self.i = i
        self.device = f"benchhost{i}:user{i}"
        self.email = f"emp{i:02d}@bench.local"
        self.key: str | None = None
        self.secret = payload.secret
        self.team = payload.team
        self.box = MemOutbox()
        self.state = "offline"
        self.t = Transport(self.box, Endpoint(list(payload.hosts), payload.port,
                                              payload.fingerprint), self.hello,
                           on_state=self._on_state, resolver=lambda: [],
                           on_device_key=self._on_key)
        self.sent = 0

    def hello(self) -> Hello:
        if self.key:
            return Hello(self.device, None, self.key, self.team, PROTOCOL_VERSION)
        return Hello(self.device, self.secret, None, self.team, PROTOCOL_VERSION)

    def _on_key(self, key: str) -> None:
        self.key = key

    def _on_state(self, s: str) -> None:
        self.state = s

    def make(self, n: int) -> list[UsageEvent]:
        now = datetime.now(UTC)
        out = []
        for k in range(n):
            self.sent += 1
            ts = (now - timedelta(seconds=k)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            out.append(UsageEvent(
                uuid=f"u{self.i}-{self.sent}", session_id=f"s{self.i}-{self.sent // 200}",
                message_id=f"m{self.i}-{self.sent}-{time.time_ns()}", timestamp=ts,
                model="claude-sonnet-4-6", reasoning_effort=None, is_sidechain=False,
                input_tokens=5, output_tokens=100, cache_read_tokens=1000,
                cache_create_5m_tokens=20, cache_create_1h_tokens=0, project=f"p{k % 7}",
                session_title=None, tool_names=("Read", "Edit"), claude_account_email=self.email,
                account_uuid=None, windows_user=f"user{self.i}", hostname=f"benchhost{self.i}",
                team=self.team, device_id=self.device))
        return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--installer", required=True)
    ap.add_argument("--clients", type=int, default=50)
    a = ap.parse_args()
    payload = read_payload(Path(a.installer))
    assert payload is not None
    clients = [Client(i, payload) for i in range(a.clients)]
    for c in clients:
        c.t.start()
        time.sleep(0.02)
    end = time.monotonic() + 120
    while time.monotonic() < end and not all(c.state == "connected" for c in clients):
        time.sleep(0.1)
    print(json.dumps({"connected": sum(c.state == "connected" for c in clients)}), flush=True)
    for line in sys.stdin:
        cmd = line.split()
        if not cmd or cmd[0] == "stop":
            break
        if cmd[0] == "send":
            per = int(cmd[1])
            batches = [c.make(per) for c in clients]
            t0 = time.monotonic()
            for c, b in zip(clients, batches, strict=True):
                c.box.add(b)
                c.t.notify()
            while any(c.box.waiting() for c in clients) and time.monotonic() - t0 < 600:
                time.sleep(0.05)
            print(json.dumps({"sent": per * len(clients),
                              "seconds": round(time.monotonic() - t0, 3)}), flush=True)
    for c in clients:
        c.t.stop(2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
