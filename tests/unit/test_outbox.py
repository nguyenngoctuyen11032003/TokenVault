import dataclasses
import sqlite3
from pathlib import Path

import pytest

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


def _events(ob: Outbox, limit: int) -> list[UsageEvent]:
    return [p.event for p in ob.pending(limit)]


def _ack(ob: Outbox, ids: list[str]) -> int:
    """Ack ids at their current revision (unknown ids are sent with revision 0)."""
    revs = {p.message_id: p.revision for p in ob.pending(1000)}
    return ob.ack([(m, revs.get(m, 0)) for m in ids])


def test_enqueue_idempotent(tmp_path: Path):
    ob = Outbox(tmp_path / "outbox.db")
    assert ob.enqueue([_event("m1"), _event("m2")]) == 2
    assert ob.enqueue([_event("m1"), _event("m2")]) == 0
    assert ob.enqueue([_event("m1", out=5)]) == 0  # smaller usage ignored
    assert [e.message_id for e in _events(ob, 10)] == ["m1", "m2"]
    assert _events(ob, 10)[0].output_tokens == 20
    ob.close()


def test_larger_usage_replaces_and_requeues_delivered_row(tmp_path: Path):
    ob = Outbox(tmp_path / "outbox.db")
    ob.enqueue([_event("m1")])
    _ack(ob, ["m1"])
    assert _events(ob, 10) == []
    assert ob.enqueue([_event("m1", out=99)]) == 1
    [ev] = _events(ob, 10)
    assert ev.output_tokens == 99
    ob.close()


def test_ack_marks_delivered_and_stats(tmp_path: Path):
    ob = Outbox(tmp_path / "outbox.db")
    ob.enqueue([_event("m1"), _event("m2"), _event("m3")])
    assert ob.stats() == OutboxStats(queued=3, delivered=0, waiting=3)
    assert _ack(ob, ["m1", "m3", "unknown"]) == 2
    assert _ack(ob, ["m1"]) == 0
    assert ob.stats() == OutboxStats(queued=3, delivered=2, waiting=1)
    assert [e.message_id for e in _events(ob, 10)] == ["m2"]
    ob.close()


def test_pending_respects_limit(tmp_path: Path):
    ob = Outbox(tmp_path / "outbox.db")
    ob.enqueue([_event(f"m{i}") for i in range(5)])
    assert len(_events(ob, 2)) == 2
    ob.close()


def test_outbox_survives_reopen(tmp_path: Path):
    db = tmp_path / "outbox.db"
    ob = Outbox(db)
    ev = dataclasses.replace(_event("m1"), tool_names=("Bash", "Read"), session_title="Tiêu đề")
    ob.enqueue([ev, _event("m2")])
    _ack(ob, ["m2"])
    ob.set_offset("src", SourceOffset(file_id="1:2", offset=123, prefix_hash="ab", file_size=200))
    ob.close()

    ob2 = Outbox(db)
    assert _events(ob2, 10) == [ev]
    assert ob2.stats() == OutboxStats(queued=2, delivered=1, waiting=1)
    assert ob2.get_offset("src") == SourceOffset("1:2", 123, "ab", 200)
    assert ob2.get_offset("other") is None
    ob2.close()


def test_set_session_title_updates_only_undelivered_rows_of_session(tmp_path: Path):
    ob = Outbox(tmp_path / "outbox.db")
    ob.enqueue([_event("m1"), _event("m2"), _event("m3", session="s-2")])
    _ack(ob, ["m1"])
    assert ob.set_session_title("s-1", "New title") == 1
    titles = {e.message_id: e.session_title for e in _events(ob, 10)}
    assert titles == {"m2": "New title", "m3": None}
    ob.close()


def test_ack_of_stale_revision_keeps_larger_usage_pending(tmp_path: Path):
    ob = Outbox(tmp_path / "outbox.db")
    ob.enqueue([_event("m1")])
    [sent] = ob.pending(10)  # sender takes the old usage
    assert ob.enqueue([_event("m1", out=99)]) == 1  # collector grows it meanwhile
    assert ob.ack([(sent.message_id, sent.revision)]) == 0  # stale receipt
    [p] = ob.pending(10)
    assert p.event.output_tokens == 99
    assert p.revision != sent.revision
    assert ob.ack([(p.message_id, p.revision)]) == 1
    assert ob.pending(10) == []
    ob.close()


def test_title_change_bumps_revision_so_stale_ack_keeps_row(tmp_path: Path):
    ob = Outbox(tmp_path / "outbox.db")
    ob.enqueue([_event("m1")])
    [sent] = ob.pending(10)
    ob.set_session_title("s-1", "T")
    assert ob.ack([(sent.message_id, sent.revision)]) == 0
    assert _events(ob, 10)[0].session_title == "T"
    ob.close()


class _FailingCommit:
    """Connection proxy whose next COMMIT raises once."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self.armed = True

    def execute(self, sql: str, *args: object) -> sqlite3.Cursor:
        if sql == "COMMIT" and self.armed:
            self.armed = False
            raise sqlite3.OperationalError("simulated commit failure")
        return self._db.execute(sql, *args)

    def __getattr__(self, name: str) -> object:
        return getattr(self._db, name)


def test_failed_commit_rolls_back_and_next_transaction_works(tmp_path: Path):
    ob = Outbox(tmp_path / "outbox.db")
    real = ob._db
    ob._db = _FailingCommit(real)  # type: ignore[assignment]
    with pytest.raises(sqlite3.OperationalError):
        ob.enqueue([_event("m1")])
    assert real.in_transaction is False
    ob._db = real
    assert _events(ob, 10) == []
    assert ob.enqueue([_event("m2")]) == 1
    assert [e.message_id for e in _events(ob, 10)] == ["m2"]
    ob.close()


def test_failing_body_rolls_back_and_next_transaction_works(tmp_path: Path):
    ob = Outbox(tmp_path / "outbox.db")
    with pytest.raises(AttributeError):
        ob.enqueue([_event("m1"), "not an event"])  # type: ignore[list-item]
    assert ob._db.in_transaction is False
    assert _events(ob, 10) == []
    assert ob.enqueue([_event("m1")]) == 1
    ob.close()


def test_session_titles_persist(tmp_path: Path):
    db = tmp_path / "outbox.db"
    with Outbox(db) as ob:
        ob.set_session_title("s-1", "Old")
        ob.set_session_title("s-1", "Tiêu đề")
        ob.set_session_title("s-2", "Other")
    with Outbox(db) as ob:
        assert ob.session_titles() == {"s-1": "Tiêu đề", "s-2": "Other"}
