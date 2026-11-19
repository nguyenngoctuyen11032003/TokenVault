"""Recording TLS proxy between the Adapters and the Manager (E2E harness, own process).

It presents the Manager's OWN certificate (read from the Manager's data dir), so the
Adapter's fingerprint pin passes exactly as against the Manager; it forwards every
byte over a second TLS connection to the real ingest port and records the plaintext:

* ``<capture>.raw``  every decrypted byte in both directions (HTTP upgrade + frames)
* ``<capture>.jsonl`` one record per WebSocket message, client frames UNMASKED
  (masking would otherwise hide text from a grep), with direction and opcode.

Prints ``{"port": N}`` on stdout when listening; runs until killed.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import ssl
import sys
import time
from pathlib import Path
from typing import IO


class WsRecorder:
    def __init__(self, conn: int, direction: str, raw: IO[bytes], msgs: IO[str]) -> None:
        self.conn, self.dir, self.raw, self.msgs = conn, direction, raw, msgs
        self.buf = bytearray()
        self.http_done = False
        self.frag: bytearray | None = None
        self.frag_op = 0

    def feed(self, data: bytes) -> None:
        self.raw.write(data)
        self.raw.flush()
        self.buf += data
        if not self.http_done:
            end = self.buf.find(b"\r\n\r\n")
            if end < 0:
                return
            self._emit(-1, bytes(self.buf[: end + 4]))
            del self.buf[: end + 4]
            self.http_done = True
        while self._frame():
            pass

    def _frame(self) -> bool:
        b = self.buf
        if len(b) < 2:
            return False
        fin, op, masked, n = b[0] & 0x80, b[0] & 0x0F, b[1] & 0x80, b[1] & 0x7F
        i = 2
        if n == 126:
            if len(b) < 4:
                return False
            n, i = int.from_bytes(b[2:4], "big"), 4
        elif n == 127:
            if len(b) < 10:
                return False
            n, i = int.from_bytes(b[2:10], "big"), 10
        key = b""
        if masked:
            if len(b) < i + 4:
                return False
            key, i = bytes(b[i:i + 4]), i + 4
        if len(b) < i + n:
            return False
        payload = bytes(b[i:i + n])
        if masked:
            payload = bytes(c ^ key[j % 4] for j, c in enumerate(payload))
        del b[: i + n]
        if op == 0 and self.frag is not None:
            self.frag += payload
            if fin:
                self._emit(self.frag_op, bytes(self.frag))
                self.frag = None
        elif not fin and op in (1, 2):
            self.frag, self.frag_op = bytearray(payload), op
        else:
            self._emit(op, payload)
        return True

    def _emit(self, op: int, payload: bytes) -> None:
        self.msgs.write(json.dumps({"t": time.time(), "conn": self.conn, "dir": self.dir,
                                    "op": op, "text": payload.decode("utf-8", "replace")},
                                   ensure_ascii=False) + "\n")
        self.msgs.flush()


async def pipe(r: asyncio.StreamReader, w: asyncio.StreamWriter, rec: WsRecorder) -> None:
    try:
        while data := await r.read(65536):
            rec.feed(data)
            w.write(data)
            await w.drain()
    except (OSError, ssl.SSLError, ConnectionError):
        pass
    finally:
        try:
            w.close()
        except Exception:  # noqa: BLE001
            pass


async def amain(a: argparse.Namespace) -> None:
    from tokenvault.manager.tls import ensure_certificate, load_server_context

    server_ctx = load_server_context(ensure_certificate(Path(a.cert_dir)))
    up_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    up_ctx.check_hostname = False
    up_ctx.verify_mode = ssl.CERT_NONE
    raw = open(a.capture + ".raw", "ab")  # noqa: SIM115
    msgs = open(a.capture + ".jsonl", "a", encoding="utf-8")  # noqa: SIM115
    ids = itertools.count(1)

    async def handle(cr: asyncio.StreamReader, cw: asyncio.StreamWriter) -> None:
        conn = next(ids)
        try:
            ur, uw = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", a.upstream, ssl=up_ctx), 10)
        except (OSError, TimeoutError, ssl.SSLError):
            cw.close()
            return
        await asyncio.gather(pipe(cr, uw, WsRecorder(conn, "c2s", raw, msgs)),
                             pipe(ur, cw, WsRecorder(conn, "s2c", raw, msgs)))

    srv = await asyncio.start_server(handle, "127.0.0.1", a.port, ssl=server_ctx)
    print(json.dumps({"port": srv.sockets[0].getsockname()[1]}), flush=True)
    async with srv:
        await srv.serve_forever()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cert-dir", required=True)
    ap.add_argument("--upstream", type=int, required=True)
    ap.add_argument("--capture", required=True)
    ap.add_argument("--port", type=int, default=0)
    asyncio.run(amain(ap.parse_args()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
