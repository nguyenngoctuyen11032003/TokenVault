"""Windows process metrics for the performance bench (no psutil).

One ``NtQuerySystemInformation(SystemProcessInformation)`` call returns, for every
process: parent pid, image name, CPU time (user + kernel), working set, private
bytes, I/O operation/transfer counters and, per thread, the context-switch count.
Context switches of a process = how often its threads were scheduled, i.e. its
wake-ups (an idle thread that polls with a 0.1 s timeout costs ~10/s).
"""

from __future__ import annotations

import ctypes
import statistics
import struct
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

_ntdll = ctypes.WinDLL("ntdll")
_SYSTEM_PROCESS_INFORMATION = 5
_STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
_PROC_HDR = 0x100  # sizeof(SYSTEM_PROCESS_INFORMATION) on x64
_THREAD_SIZE = 0x50  # sizeof(SYSTEM_THREAD_INFORMATION) on x64


@dataclass
class Proc:
    pid: int
    ppid: int
    name: str
    threads: int
    cpu_s: float  # user + kernel
    ws: int  # working set bytes
    private: int  # private bytes
    ctx: int  # sum of thread context switches
    write_ops: int
    write_bytes: int
    read_bytes: int


def snapshot() -> dict[int, Proc]:
    size = 1 << 20
    while True:
        buf = ctypes.create_string_buffer(size)
        ret_len = ctypes.c_ulong(0)
        st = _ntdll.NtQuerySystemInformation(_SYSTEM_PROCESS_INFORMATION, buf, size,
                                             ctypes.byref(ret_len)) & 0xFFFFFFFF
        if st == _STATUS_INFO_LENGTH_MISMATCH:
            size = max(size * 2, ret_len.value + 65536)
            continue
        if st != 0:
            raise OSError(f"NtQuerySystemInformation failed 0x{st:08x}")
        break
    raw = buf.raw
    out: dict[int, Proc] = {}
    off = 0
    while True:
        nxt, nthreads = struct.unpack_from("<II", raw, off)
        user, kern = struct.unpack_from("<qq", raw, off + 40)
        name_len, _mx, name_ptr = struct.unpack_from("<HHxxxxQ", raw, off + 56)
        pid, ppid = struct.unpack_from("<QQ", raw, off + 80)
        ws = struct.unpack_from("<Q", raw, off + 144)[0]
        private = struct.unpack_from("<Q", raw, off + 200)[0]
        r_ops, w_ops, _o_ops, r_bytes, w_bytes = struct.unpack_from("<QQQQQ", raw, off + 208)
        name = ""
        if name_ptr and name_len:
            name = ctypes.wstring_at(name_ptr, name_len // 2)
        ctx = 0
        for i in range(nthreads):
            ctx += struct.unpack_from("<I", raw, off + _PROC_HDR + i * _THREAD_SIZE + 64)[0]
        out[pid] = Proc(pid, ppid, name, nthreads, (user + kern) / 1e7, ws, private, ctx,
                        w_ops, w_bytes, r_bytes)
        if nxt == 0:
            break
        off += nxt
    return out


def tree(root: int, snap: dict[int, Proc]) -> list[int]:
    """``root`` and all its descendants present in ``snap``."""
    pids, todo = [], [root]
    while todo:
        p = todo.pop()
        if p in snap and p not in pids:
            pids.append(p)
            todo.extend(c.pid for c in snap.values() if c.ppid == p and c.pid != p)
    return pids


@dataclass
class Window:
    """Metrics of a set of processes over a time window."""

    seconds: float = 0.0
    cpu_s: float = 0.0
    ctx: int = 0
    write_bytes: int = 0
    write_ops: int = 0
    ws: list[int] = field(default_factory=list)
    private: list[int] = field(default_factory=list)
    cpu_pct_samples: list[float] = field(default_factory=list)  # per sample interval
    procs: int = 0

    def summary(self) -> dict[str, float]:
        el = max(self.seconds, 1e-9)
        s = self.cpu_pct_samples
        sustained = 0.0
        if len(s) >= 5:  # worst 5-sample moving average = "sustained"
            sustained = max(sum(s[i:i + 5]) / 5 for i in range(len(s) - 4))
        return {
            "seconds": round(self.seconds, 1),
            "cpu_pct_of_one_core": round(100 * self.cpu_s / el, 3),
            "cpu_pct_peak_sample": round(max(s), 1) if s else 0.0,
            "cpu_pct_sustained_5s": round(sustained, 1),
            "wakeups_per_s": round(self.ctx / el, 1),
            "ws_mb_avg": round(statistics.fmean(self.ws) / 2**20, 1) if self.ws else 0.0,
            "ws_mb_max": round(max(self.ws) / 2**20, 1) if self.ws else 0.0,
            "private_mb_max": round(max(self.private) / 2**20, 1) if self.private else 0.0,
            "write_mb": round(self.write_bytes / 2**20, 2),
            "write_ops": self.write_ops,
            "processes": self.procs,
        }


def _sum(snap: dict[int, Proc], pids: Iterable[int]) -> tuple[float, int, int, int, int, int]:
    cpu = ctx = ws = priv = wb = wo = 0
    for p in pids:
        pr = snap.get(p)
        if pr is None:
            continue
        cpu += pr.cpu_s  # type: ignore[assignment]
        ctx += pr.ctx
        ws += pr.ws
        priv += pr.private
        wb += pr.write_bytes
        wo += pr.write_ops
    return float(cpu), ctx, ws, priv, wb, wo


def measure(pids_of: Callable[[dict[int, Proc]], list[int]], seconds: float,
            interval: float = 1.0, until: Callable[[], bool] | None = None) -> Window:
    """Sample the processes chosen by ``pids_of`` for ``seconds`` (or until ``until()``).

    CPU, wake-ups and I/O are deltas of processes alive at both ends of an interval.
    """
    w = Window()
    snap = snapshot()
    pids = pids_of(snap)
    prev = {p: snap[p] for p in pids}
    t0 = last = time.monotonic()
    w.procs = len(pids)
    while True:
        time.sleep(interval)
        snap = snapshot()
        now = time.monotonic()
        pids = pids_of(snap)
        w.procs = max(w.procs, len(pids))
        both = [p for p in pids if p in prev]
        c1, x1, ws, priv, wb1, wo1 = _sum(snap, both)
        c0, x0, _, _, wb0, wo0 = _sum(prev, both)
        w.cpu_s += c1 - c0
        w.ctx += x1 - x0
        w.write_bytes += wb1 - wb0
        w.write_ops += wo1 - wo0
        w.cpu_pct_samples.append(100 * (c1 - c0) / max(now - last, 1e-9))
        _, _, ws_all, priv_all, _, _ = _sum(snap, pids)
        w.ws.append(ws_all)
        w.private.append(priv_all)
        prev = {p: snap[p] for p in pids}
        last = now
        w.seconds = now - t0
        if until is not None and until():
            break
        if until is None and w.seconds >= seconds - interval / 2:
            break
        if until is not None and w.seconds >= seconds:
            break
    return w


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    k = min(len(v) - 1, max(0, round(q / 100 * (len(v) - 1))))
    return v[k]
