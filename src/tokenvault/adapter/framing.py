"""Turn queued outbox rows into ONE ``Batch`` frame for the transport."""

from __future__ import annotations

from tokenvault.adapter.outbox import PendingEvent
from tokenvault.common.protocol import Batch, ProtocolError, encode_msg


def frame_rows(rows: list[PendingEvent]) -> tuple[str | None, list[PendingEvent],
                                                 list[PendingEvent]]:
    """Return ``(frame, rows in the frame, poison rows)``.

    The whole batch is encoded once in the common case (Task 30: the old code also
    encoded every row on its own first, doubling the JSON work). Only when that fails
    is each row checked alone: a row that cannot be framed by itself is poison (the
    caller acks it so it can never block the queue); the rest is halved until the
    frame fits. ``frame`` is None when nothing is sendable.
    """
    try:
        return encode_msg(Batch([r.event for r in rows])), rows, []
    except (ProtocolError, ValueError):  # incl. UnicodeEncodeError, oversize
        pass
    good: list[PendingEvent] = []
    bad: list[PendingEvent] = []
    for r in rows:
        try:
            encode_msg(Batch([r.event]))
            good.append(r)
        except (ProtocolError, ValueError):
            bad.append(r)
    while good:
        try:
            return encode_msg(Batch([r.event for r in good])), good, bad
        except (ProtocolError, ValueError):
            good = good[: max(1, len(good) // 2)]  # too big together: send fewer
    return None, [], bad
