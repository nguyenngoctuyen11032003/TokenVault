"""Regression tests for the Task 30 adapter optimisations (see docs/performance.md)."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from tokenvault.adapter import parser, priority
from tokenvault.adapter.collector import Collector
from tokenvault.adapter.cpu_budget import CpuBudget
from tokenvault.adapter.framing import frame_rows
from tokenvault.adapter.identity import Identity
from tokenvault.adapter.outbox import Outbox, PendingEvent
from tokenvault.common import protocol
from tokenvault.common.models import UsageEvent

SRC = Path(__file__).resolve().parents[2] / "src"


# ------------------------------------------------------------------ CpuBudget
class Clock:
    def __init__(self) -> None:
        self.t = 100.0
        self.c = 5.0


def test_budget_first_call_opens_the_window_and_never_charges_startup() -> None:
    k = Clock()
    b = CpuBudget(0.1, 5.0, clock=lambda: k.t, cpu=lambda: k.c)
    assert b.delay() == 0.0  # 5 s of start-up CPU before this is not counted
    k.c += 0.2  # 200 ms of work
    k.t += 0.2
    assert b.delay() == pytest.approx(1.8)  # 0.2 / 0.1 = 2 s of wall in total


def test_budget_window_slides_without_saving_up_idle_time() -> None:
    k = Clock()
    b = CpuBudget(0.1, 5.0, clock=lambda: k.t, cpu=lambda: k.c)
    b.start()
    k.t += 60  # long idle: a new window starts, the idle minute is not a credit
    assert b.delay() == 0.0
    k.c += 1.0
    k.t += 1.0
    assert b.delay() == pytest.approx(9.0)


def test_budget_pace_returns_when_stopped() -> None:
    k = Clock()
    b = CpuBudget(0.1, clock=lambda: k.t, cpu=lambda: k.c)
    b.start()
    k.c += 100  # would rest ~1000 s
    stop = threading.Event()
    threading.Timer(0.05, stop.set).start()
    t0 = time.monotonic()
    assert b.pace(stop) is True
    assert time.monotonic() - t0 < 2


def test_budget_rejects_bad_fraction() -> None:
    with pytest.raises(ValueError):
        CpuBudget(0)


# ------------------------------------------------------------------ parser
def test_parser_skips_prompt_and_tool_lines_without_json_decoding(
        monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bytes] = []
    real = parser.json.loads
    monkeypatch.setattr(parser.json, "loads", lambda s: calls.append(s) or real(s))
    ctx = parser.SessionContext()
    user = json.dumps({"type": "user", "message": {"content": "x" * 5000}}).encode()
    assert parser.parse_line(user, ctx) is None
    assert calls == []
    title = json.dumps({"type": "ai-title", "sessionId": "s", "aiTitle": "T"}).encode()
    assert isinstance(parser.parse_line(title, ctx), parser.TitleUpdate)
    assert len(calls) == 1


# ------------------------------------------------------------------ collector
def _line(i: int) -> str:
    return json.dumps({"type": "assistant", "uuid": f"u{i}", "sessionId": "s1",
                       "timestamp": "2026-01-01T00:00:00Z", "cwd": "C:\\w\\p",
                       "message": {"id": f"m{i}", "model": "m", "usage": {
                           "input_tokens": 1, "output_tokens": 2}}}) + "\n"


def test_collect_commits_rows_titles_and_offset_in_one_transaction(tmp_path: Path) -> None:
    f = tmp_path / "a.jsonl"
    f.write_text(_line(1) + json.dumps({"type": "ai-title", "sessionId": "s1",
                                        "aiTitle": "T"}) + "\n" + _line(2), encoding="utf-8")
    ob = Outbox(tmp_path / "o.db")
    n_tx = 0
    real = ob._tx

    def counting():  # type: ignore[no-untyped-def]
        nonlocal n_tx
        n_tx += 1
        return real()

    ob._tx = counting  # type: ignore[method-assign]
    col = Collector(ob, Identity(None, None, "u", "h", "d"), "T")
    r = col.collect_file(f)
    assert r.new_events == 2
    assert n_tx == 1
    assert ob.session_titles() == {"s1": "T"}
    assert ob.get_offset(next(iter(_offsets(ob)))).offset == f.stat().st_size
    ob.close()


def _offsets(ob: Outbox) -> list[str]:
    return [r[0] for r in ob._db.execute("SELECT source_id FROM source_offsets")]


def test_collect_max_bytes_reads_in_small_steps(tmp_path: Path) -> None:
    f = tmp_path / "a.jsonl"
    f.write_text("".join(_line(i) for i in range(40)), encoding="utf-8")
    ob = Outbox(tmp_path / "o.db")
    col = Collector(ob, Identity(None, None, "u", "h", "d"), "T")
    steps, total = 0, 0
    while True:
        r = col.collect_file(f, max_bytes=1000)
        steps += 1
        total += r.new_events
        if not r.more:
            break
    assert total == 40 and steps > 5
    ob.close()


# ------------------------------------------------------------------ framing
def _pe(i: int, **kw: object) -> PendingEvent:
    base = dict(uuid=f"u{i}", session_id="s", message_id=f"m{i}",
                timestamp="2026-01-01T00:00:00Z", model="m", reasoning_effort=None,
                is_sidechain=False, input_tokens=1, output_tokens=1, cache_read_tokens=0,
                cache_create_5m_tokens=0, cache_create_1h_tokens=0, project="p",
                session_title=None, tool_names=(), claude_account_email=None,
                account_uuid=None, windows_user="u", hostname="h", team="T", device_id="d")
    base.update(kw)
    return PendingEvent(f"m{i}", 0, UsageEvent(**base))  # type: ignore[arg-type]


def test_frame_encodes_a_good_batch_once(monkeypatch: pytest.MonkeyPatch) -> None:
    import tokenvault.adapter.framing as fr

    n = 0
    real = fr.encode_msg

    def spy(m: object) -> str:
        nonlocal n
        n += 1
        return real(m)  # type: ignore[arg-type]

    monkeypatch.setattr(fr, "encode_msg", spy)
    frame, rows, bad = frame_rows([_pe(i) for i in range(200)])
    assert frame is not None and len(rows) == 200 and bad == [] and n == 1


def test_frame_still_drops_poison_and_splits_oversize() -> None:
    poison = _pe(0, project="bad \ud800 surrogate")  # cannot be UTF-8 encoded
    frame, rows, bad = frame_rows([poison, _pe(1), _pe(2)])
    assert bad == [poison] and [r.message_id for r in rows] == ["m1", "m2"] and frame
    big = [_pe(i, project="p" * 3000) for i in range(150)]  # > MAX_FRAME_BYTES together
    frame, rows, bad = frame_rows(big)
    assert frame is not None and 0 < len(rows) < 150 and bad == []
    assert len(frame.encode()) <= protocol.MAX_FRAME_BYTES


# ------------------------------------------------------------------ priority / imports
def test_eco_qos_requests_execution_speed_throttling() -> None:
    seen: list[tuple[object, ...]] = []

    class K:
        def GetCurrentProcess(self) -> int:  # noqa: N802 - Win32 name
            return 7

        def SetProcessInformation(self, h: int, cls: int, ptr: object, size: int) -> int:  # noqa: N802
            st = ptr._obj  # type: ignore[attr-defined]  # the structure behind byref()
            seen.append((h, cls, st.Version, st.ControlMask, st.StateMask, size))
            return 1

    assert priority.set_eco_qos(K()) is True
    assert seen == [(7, 4, 1, 1, 1, 12)]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows thread background mode")
def test_thread_background_mode_can_be_entered_and_left() -> None:
    out: list[bool] = []
    t = threading.Thread(target=lambda: out.extend(
        [priority.thread_background(True), priority.thread_background(False)]))
    t.start()
    t.join()
    assert out == [True, True]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows working set")
def test_trim_working_set_succeeds() -> None:
    assert priority.trim_working_set() is True


def test_watcher_trims_memory_when_the_last_hot_file_goes_quiet(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import tokenvault.adapter.watcher as wmod

    trims: list[int] = []
    monkeypatch.setattr(wmod, "trim_working_set", lambda: trims.append(1) or True)
    monkeypatch.setattr(wmod, "HOT_TTL_S", 0.0)
    f = tmp_path / "a.jsonl"
    f.write_text("x\n")
    w = wmod.Watcher(tmp_path, object(), lambda: None)  # type: ignore[arg-type]
    w.backfill_done.set()
    w._mark_hot(f, baseline=True)
    time.sleep(0.01)
    w._poll_hot()
    assert trims == [1] and not w._hot
    w._poll_hot()  # nothing hot any more: no repeated trims
    assert trims == [1]


def test_agent_start_imports_no_gui_or_crypto_modules() -> None:
    code = ("import sys; import tokenvault.adapter.main, tokenvault.adapter.app; "
            "print(sorted(m for m in ('PIL','pystray','tkinter','cryptography') "
            "if m in sys.modules))")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env={**__import__("os").environ, "PYTHONPATH": str(SRC)}, check=True)
    assert r.stdout.strip() == "[]"
