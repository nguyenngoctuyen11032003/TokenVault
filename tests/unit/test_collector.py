import json
from pathlib import Path

import pytest

from tokenvault.adapter.collector import MAX_LINE_BYTES, Collector
from tokenvault.adapter.identity import Identity
from tokenvault.adapter.outbox import Outbox
from tokenvault.adapter.parser import SessionContext

IDENT = Identity("a@example.com", "acc-1", "user", "host", "host:user")


def _assistant(
    mid: str,
    *,
    session: str = "s-1",
    out: int = 20,
    ts: str = "2026-01-01T10:00:01.000Z",
    tool: str | None = None,
) -> bytes:
    content = [{"type": "tool_use", "name": tool, "input": {"secret": "x"}}] if tool else []
    obj = {
        "type": "assistant",
        "uuid": f"u-{mid}-{out}-{tool}",
        "sessionId": session,
        "timestamp": ts,
        "cwd": "C:\\Users\\user\\work\\proj",
        "message": {
            "id": mid,
            "model": "claude-opus-4-7",
            "content": content,
            "usage": {"input_tokens": 10, "output_tokens": out},
        },
    }
    return json.dumps(obj).encode() + b"\n"


def _title(session: str, title: str) -> bytes:
    obj = {"type": "ai-title", "sessionId": session, "aiTitle": title}
    return json.dumps(obj, ensure_ascii=False).encode() + b"\n"


@pytest.fixture
def outbox(tmp_path: Path):
    ob = Outbox(tmp_path / "outbox.db")
    yield ob
    ob.close()


@pytest.fixture
def collector(outbox: Outbox) -> Collector:
    return Collector(outbox, IDENT, "team-a", SessionContext())


def _ids(outbox: Outbox) -> list[str | None]:
    return [p.message_id for p in outbox.pending(1000)]


def _events(outbox: Outbox, limit: int):
    return [p.event for p in outbox.pending(limit)]


def _ack(outbox: Outbox, ids: list[str]) -> int:
    revs = {p.message_id: p.revision for p in outbox.pending(1000)}
    return outbox.ack([(m, revs[m]) for m in ids])


def test_second_collect_reads_only_new_lines(tmp_path: Path, outbox, collector):
    f = tmp_path / "t.jsonl"
    f.write_bytes(_assistant("m1") + _assistant("m1") + _assistant("m2"))
    r1 = collector.collect_file(f)
    assert r1.new_events == 2
    assert r1.rebaselined is False
    with f.open("ab") as fh:
        fh.write(_assistant("m3"))
    r2 = collector.collect_file(f)
    assert r2.new_events == 1
    assert _ids(outbox) == ["m1", "m2", "m3"]
    assert collector.collect_file(f).new_events == 0
    ev = _events(outbox, 1)[0]
    assert (ev.team, ev.device_id, ev.claude_account_email) == (
        "team-a",
        "host:user",
        "a@example.com",
    )
    assert (ev.windows_user, ev.hostname, ev.account_uuid) == ("user", "host", "acc-1")
    assert ev.project == "proj"


def test_partial_last_line_not_consumed_until_newline(tmp_path: Path, outbox, collector):
    f = tmp_path / "t.jsonl"
    full = _assistant("m1")
    f.write_bytes(full[:-10])
    assert collector.collect_file(f).new_events == 0
    with f.open("ab") as fh:
        fh.write(full[-10:-1])  # still no newline
    assert collector.collect_file(f).new_events == 0
    with f.open("ab") as fh:
        fh.write(b"\n")
    r = collector.collect_file(f)
    assert r.new_events == 1
    assert r.rebaselined is False
    assert _ids(outbox) == ["m1"]


def test_truncated_or_replaced_file_rebaselines_without_duplicates(
    tmp_path: Path, outbox, collector
):
    f = tmp_path / "t.jsonl"
    f.write_bytes(_assistant("m1") + _assistant("m2") + _assistant("m3"))
    assert collector.collect_file(f).new_events == 3
    assert _ack(outbox, ["m1", "m2", "m3"]) == 3

    # Truncated: shorter than the saved offset.
    f.write_bytes(_assistant("m1"))
    r = collector.collect_file(f)
    assert r.rebaselined is True
    assert r.new_events == 0
    assert _events(outbox, 10) == []

    # Replaced: same length region but different bytes before the offset.
    f.write_bytes(_assistant("m2", session="s-1") + _assistant("m4"))
    r = collector.collect_file(f)
    assert r.rebaselined is True
    assert r.new_events == 1
    assert _ids(outbox) == ["m4"]
    assert outbox.stats().queued == 4


def test_oversize_line_skipped_and_reported(tmp_path: Path, outbox, collector):
    f = tmp_path / "t.jsonl"
    big = b'{"type":"user","pad":"' + b"x" * (MAX_LINE_BYTES + 10)
    # Oversize line still being written: not walked past yet.
    f.write_bytes(_assistant("m1") + big)
    r = collector.collect_file(f)
    assert (r.new_events, r.skipped_oversize) == (1, 0)
    with f.open("ab") as fh:
        fh.write(b'"}\n' + _assistant("m2"))
    r = collector.collect_file(f)
    assert (r.new_events, r.skipped_oversize) == (1, 1)
    assert collector.collect_file(f).skipped_oversize == 0
    assert _ids(outbox) == ["m1", "m2"]


def test_title_arriving_after_usage_updates_pending_rows_of_session(
    tmp_path: Path, outbox, collector
):
    f = tmp_path / "t.jsonl"
    f.write_bytes(_assistant("m1") + _assistant("m9", session="s-2"))
    collector.collect_file(f)
    _ack(outbox, ["m9"])
    assert _events(outbox, 10)[0].session_title is None
    with f.open("ab") as fh:
        fh.write(_title("s-1", "Sửa lỗi đăng nhập") + _assistant("m2"))
    collector.collect_file(f)
    titles = {e.message_id: e.session_title for e in _events(outbox, 10)}
    assert titles == {"m1": "Sửa lỗi đăng nhập", "m2": "Sửa lỗi đăng nhập"}


def test_invalid_timestamp_and_huge_tokens_skipped(tmp_path: Path, outbox, collector):
    f = tmp_path / "t.jsonl"
    f.write_bytes(
        _assistant("m1", ts="")
        + _assistant("m2", ts="not-a-date")
        + _assistant("m3", out=10**12 + 1)
        + _assistant("m4")
    )
    r = collector.collect_file(f)
    assert (r.new_events, r.skipped_invalid) == (1, 3)
    assert _ids(outbox) == ["m4"]


def test_message_split_across_collects_keeps_one_row_with_all_tools(
    tmp_path: Path, outbox, collector
):
    f = tmp_path / "t.jsonl"
    f.write_bytes(_assistant("m1", tool="Bash"))
    collector.collect_file(f)
    with f.open("ab") as fh:
        fh.write(_assistant("m1", tool="Read"))
    collector.collect_file(f)
    [ev] = _events(outbox, 10)
    assert ev.tool_names == ("Bash", "Read")


def test_batch_cap_sets_more(tmp_path: Path, outbox, monkeypatch):
    import tokenvault.adapter.collector as mod

    monkeypatch.setattr(mod, "MAX_BATCH_BYTES", 300)
    c = Collector(outbox, IDENT, "t", SessionContext())
    f = tmp_path / "t.jsonl"
    f.write_bytes(b"".join(_assistant(f"m{i}") for i in range(6)))
    rounds = 0
    while True:
        rounds += 1
        r = c.collect_file(f)
        if not r.more:
            break
        assert rounds < 20
    assert rounds > 1
    assert len(_ids(outbox)) == 6


def test_collect_all_walks_jsonl_files(tmp_path: Path, outbox, collector):
    (tmp_path / "p1").mkdir()
    (tmp_path / "p2").mkdir()
    (tmp_path / "p1" / "a.jsonl").write_bytes(_assistant("m1"))
    (tmp_path / "p2" / "b.jsonl").write_bytes(_assistant("m2"))
    (tmp_path / "p2" / "notes.txt").write_bytes(_assistant("m3"))
    r = collector.collect_all(tmp_path)
    assert r.new_events == 2
    assert sorted(_ids(outbox)) == ["m1", "m2"]


def test_session_title_survives_restart(tmp_path: Path):
    db = tmp_path / "restart.db"
    f = tmp_path / "t.jsonl"
    f.write_bytes(_title("s-1", "Persisted title") + _assistant("m1"))
    with Outbox(db) as ob:
        Collector(ob, IDENT, "team-a", SessionContext()).collect_file(f)
    with f.open("ab") as fh:
        fh.write(_assistant("m2"))
    with Outbox(db) as ob:
        r = Collector(ob, IDENT, "team-a", SessionContext()).collect_file(f)
        assert r.new_events == 1
        titles = {p.message_id: p.event.session_title for p in ob.pending(10)}
    assert titles["m2"] == "Persisted title"
