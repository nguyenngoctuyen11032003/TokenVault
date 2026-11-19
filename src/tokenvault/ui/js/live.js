// Live-update bus, exposed globally as `window.tv`.
//   tv.onLive(cb)        -> subscribe; returns an unsubscribe function
//   tv.onLive(payload)   -> Python pushes with window.evaluate_js("tv.onLive(<json>)")
//   tv.emitLive(payload) -> same as above; payload may be an object or a JSON string
//   tv.lastLive          -> most recent payload (or null)
// Device counts for the titlebar are read from {online, total} (or {devices: {online, total}}).

const listeners = new Set();
let last = null;

export function emitLive(payload) {
  let data = payload;
  if (typeof data === 'string') {
    try {
      data = JSON.parse(data);
    } catch (err) {
      console.error('[live] invalid payload', err);
      return;
    }
  }
  last = data;
  for (const cb of [...listeners]) {
    try {
      cb(data);
    } catch (err) {
      console.error('[live] listener failed', err);
    }
  }
}

export function onLive(cbOrPayload) {
  if (typeof cbOrPayload !== 'function') {
    emitLive(cbOrPayload);
    return undefined;
  }
  listeners.add(cbOrPayload);
  return () => listeners.delete(cbOrPayload);
}

export function lastLive() {
  return last;
}

/** Extract {online, total} device counts from a live payload, or null. */
export function deviceCounts(payload) {
  const src = payload?.devices ?? payload;
  const online = Number(src?.online);
  const total = Number(src?.total);
  if (!Number.isFinite(online) || !Number.isFinite(total)) return null;
  return { online, total };
}

export function installGlobal() {
  const tv = globalThis.tv ?? {};
  tv.onLive = onLive;
  tv.emitLive = emitLive;
  Object.defineProperty(tv, 'lastLive', { get: lastLive, configurable: true });
  globalThis.tv = tv;
  return tv;
}
