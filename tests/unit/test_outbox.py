import dataclasses
from pathlib import Path

from tokenvault.adapter.outbox import Outbox, OutboxStats, SourceOffset
from tokenvault.common.models import UsageEvent


def _event(
    mid: str, *, session: str = "s-1", out: int = 20, title: str | None = None
) -> UsageEvent:
    return UsageEvent(
        uuid=f"u-{mid}",
        session_id=session,
        message_id=mid,
        timestamp="2026-01-01T10:00:00.000Z",
        model="claude-opus-4-7",
        reasoning_effort=None,
        is_sidechain=False,
        input_tokens=10,
        output_tokens=out,
        cache_read_tokens=0,
        cache_create_5m_tokens=0,
        cache_create_1h_tokens=0,
        project="proj",
        session_title=title,
        tool_names=(),
        claude_account_email="a@example.com",
        account_uuid="acc-1",
        windows_user="user",
        hostname="host",
        team="team",
        device_id="host:user",
    )


def test_enqueue_idempotent(tmp_path: Path):
    ob = Outbox(tmp_path / "outbox.db")
    assert ob.enqueue([_event("m1"), _event("m2")]) == 2
    assert ob.enqueue([_event("m1"), _event("m2")]) == 0
    assert ob.enqueue([_event("m1", out=5)]) == 0  # smaller usage ignored
    assert [e.message_id for e in ob.pending(10)] == ["m1", "m2"]
    assert ob.pending(10)[0].output_tokens == 20
    ob.close()


def test_larger_usage_replaces_and_requeues_delivered_row(tmp_path: Path):
    ob = Outbox(tmp_path / "outbox.db")
    ob.enqueue([_event("m1")])
    ob.ack(["m1"])
    assert ob.pending(10) == []
    assert ob.enqueue([_event("m1", out=99)]) == 1
    [ev] = ob.pending(10)
    assert ev.output_tokens == 99
    ob.close()


def test_ack_marks_delivered_and_stats(tmp_path: Path):
    ob = Outbox(tmp_path / "outbox.db")
    ob.enqueue([_event("m1"), _event("m2"), _event("m3")])
    assert ob.stats() == OutboxStats(queued=3, delivered=0, waiting=3)
    assert ob.ack(["m1", "m3", "unknown"]) == 2
    assert ob.ack(["m1"]) == 0
    assert ob.stats() == OutboxStats(queued=3, delivered=2, waiting=1)
    assert [e.message_id for e in ob.pending(10)] == ["m2"]
    ob.close()


def test_pending_respects_limit(tmp_path: Path):
    ob = Outbox(tmp_path / "outbox.db")
    ob.enqueue([_event(f"m{i}") for i in range(5)])
    assert len(ob.pending(2)) == 2
    ob.close()


def test_outbox_survives_reopen(tmp_path: Path):
    db = tmp_path / "outbox.db"
    ob = Outbox(db)
    ev = dataclasses.replace(_event("m1"), tool_names=("Bash", "Read"), session_title="Tiêu đề")
    ob.enqueue([ev, _event("m2")])
    ob.ack(["m2"])
    ob.set_offset("src", SourceOffset(file_id="1:2", offset=123, prefix_hash="ab", file_size=200))
    ob.close()

    ob2 = Outbox(db)
    assert ob2.pending(10) == [ev]
    assert ob2.stats() == OutboxStats(queued=2, delivered=1, waiting=1)
    assert ob2.get_offset("src") == SourceOffset("1:2", 123, "ab", 200)
    assert ob2.get_offset("other") is None
    ob2.close()


def test_set_session_title_updates_only_undelivered_rows_of_session(tmp_path: Path):
    ob = Outbox(tmp_path / "outbox.db")
    ob.enqueue([_event("m1"), _event("m2"), _event("m3", session="s-2")])
    ob.ack(["m1"])
    assert ob.set_session_title("s-1", "New title") == 1
    titles = {e.message_id: e.session_title for e in ob.pending(10)}
    assert titles == {"m2": "New title", "m3": None}
    ob.close()
