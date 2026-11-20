// Gate between the live bus and a page's live callback (Task 30).
//
// Python pushes tv.onLive at most twice a second while adapters send; every page answers a
// push by re-fetching its data. The gate hands a page at most one push per LIVE_MIN_GAP_MS
// (leading + trailing: the newest payload is always delivered) and NONE while the document
// is hidden (window hidden to the tray / minimised): one push is replayed when it becomes
// visible again, so a hidden Manager does no page work at all.

export const LIVE_MIN_GAP_MS = 1000;

export function gateLive(fn, signal, minGap = LIVE_MIN_GAP_MS) {
  let last = -Infinity;
  let timer = 0;
  let payload;
  let waiting = false; // a push arrived while hidden

  const fire = () => {
    timer = 0;
    if (signal?.aborted) return;
    if (document.hidden) {
      waiting = true;
      return;
    }
    waiting = false;
    last = performance.now();
    fn(payload);
  };

  function push(p) {
    payload = p;
    if (signal?.aborted || timer) return;
    if (document.hidden) {
      waiting = true;
      return;
    }
    const wait = last + minGap - performance.now();
    if (wait <= 0) fire();
    else timer = setTimeout(fire, wait);
  }

  const onVisibility = () => {
    if (!document.hidden && waiting) push(payload);
  };
  document.addEventListener('visibilitychange', onVisibility);
  signal?.addEventListener(
    'abort',
    () => {
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisibility);
    },
    { once: true },
  );
  return push;
}
