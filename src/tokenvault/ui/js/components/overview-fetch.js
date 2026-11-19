// Short-lived shared cache for read-only Manager API calls of the Task 15 pages, so the filter
// bar and the page never ask the same question twice for one filter change.
//   cachedCall(ctx.api, 'overview', [filter], { maxAge: 1500 })
// - A call still in flight is always shared (same method + same JSON args).
// - A finished answer is reused while it is younger than `maxAge` ms (default OPTIONS_TTL).
//   Pages pass maxAge 0 for live refreshes (always fresh, but still joins an in-flight call).
// - Rejections are not cached. Callers treat the answer as read-only (it may be shared).

export const OPTIONS_TTL = 30_000;
const MAX_ENTRIES = 64;
const cache = new Map(); // key -> { at, pending, promise }

export function cachedCall(api, method, args, { maxAge = OPTIONS_TTL } = {}) {
  const key = `${method}:${JSON.stringify(args)}`;
  const hit = cache.get(key);
  if (hit && (hit.pending || Date.now() - hit.at <= maxAge)) return hit.promise;
  const entry = { at: Date.now(), pending: true, promise: null };
  entry.promise = Promise.resolve()
    .then(() => api[method](...args))
    .then(
      (value) => {
        entry.pending = false;
        entry.at = Date.now();
        return value;
      },
      (err) => {
        if (cache.get(key) === entry) cache.delete(key);
        throw err;
      },
    );
  cache.delete(key);
  cache.set(key, entry);
  while (cache.size > MAX_ENTRIES) cache.delete(cache.keys().next().value);
  return entry.promise;
}

/** Forget everything (tests, or after a management change). */
export function clearCallCache() {
  cache.clear();
}
