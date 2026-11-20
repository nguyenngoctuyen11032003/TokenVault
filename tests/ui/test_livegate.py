"""Task 30: live pushes reach a page at most once per second and never while hidden."""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.ui

_RUN = """async ({hidden}) => {
  const { gateLive } = await import('./js/livegate.js');
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden.v });
  const got = [];
  const ctl = new AbortController();
  const push = gateLive((p) => got.push(p), ctl.signal, 300);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  hidden.v = false;
  for (let i = 0; i < 20; i++) { push(i); await sleep(20); }   // 400 ms of pushes
  await sleep(350);
  const visibleCount = got.length;
  const lastVisible = got[got.length - 1];
  hidden.v = true;
  for (let i = 100; i < 110; i++) { push(i); await sleep(20); }
  await sleep(400);
  const hiddenCount = got.length - visibleCount;
  hidden.v = false;
  document.dispatchEvent(new Event('visibilitychange'));
  await sleep(400);
  const replay = got.slice(visibleCount);
  ctl.abort();
  push(999);
  await sleep(400);
  return { visibleCount, lastVisible, hiddenCount, replay, after: got.length };
}"""


def test_gate_throttles_and_pauses_while_hidden(open_shell: Any) -> None:
    page = open_shell("#/settings")
    r = page.evaluate(_RUN, {"hidden": {"v": False}})
    assert r["visibleCount"] in (2, 3)  # leading + trailing per 300 ms, not 20
    assert r["lastVisible"] == 19  # the newest payload is never lost
    assert r["hiddenCount"] == 0
    assert r["replay"] == [109]  # one replay with the newest payload on show
    assert r["after"] == r["visibleCount"] + 1  # nothing after abort
