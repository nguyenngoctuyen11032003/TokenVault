// Bridge to the Python Manager API.
//
// Inside the app, pywebview exposes the Python `Api` object on `window.pywebview.api` (its
// methods appear once the `pywebviewready` event fires). In a plain browser (dev server, UI
// tests without a stub) every call falls back to `POST /api/<method>` with the JSON-encoded
// argument list as body. Usage: `await api.overview(filter)`, `await api.call('overview', f)`.

const WAIT_MS = Number(globalThis.TV_API_WAIT_MS ?? 1500);

export class ApiError extends Error {
  constructor(method, message, cause) {
    super(`${method}: ${message}`);
    this.name = 'ApiError';
    this.method = method;
    this.cause = cause;
  }
}

function hasBridge() {
  const bridge = globalThis.pywebview?.api;
  return !!bridge && Object.keys(bridge).some((k) => typeof bridge[k] === 'function');
}

let modePromise = null;

/** Resolves to 'pywebview' or 'http' (memoised). */
function resolveMode() {
  if (modePromise) return modePromise;
  modePromise = new Promise((resolve) => {
    if (hasBridge()) {
      resolve('pywebview');
      return;
    }
    // If pywebview is injecting, wait for it; give a plain browser a short grace period.
    const limit = globalThis.pywebview ? WAIT_MS * 10 : WAIT_MS;
    const done = () => {
      window.removeEventListener('pywebviewready', onReady);
      clearInterval(poll);
      clearTimeout(timer);
      resolve(hasBridge() ? 'pywebview' : 'http');
    };
    const onReady = () => done();
    window.addEventListener('pywebviewready', onReady);
    const poll = setInterval(() => hasBridge() && done(), 50);
    const timer = setTimeout(done, limit);
  });
  return modePromise;
}

async function viaHttp(method, args) {
  let response;
  try {
    response = await fetch(`/api/${encodeURIComponent(method)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(args),
    });
  } catch (err) {
    throw new ApiError(method, 'network error', err);
  }
  if (!response.ok) throw new ApiError(method, `HTTP ${response.status}`);
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

/** Call a Python API method by name. Rejects with ApiError. */
export async function call(method, ...args) {
  const mode = await resolveMode();
  if (mode === 'http') return viaHttp(method, args);
  const fn = globalThis.pywebview.api[method];
  if (typeof fn !== 'function') throw new ApiError(method, 'not exposed by the Python API');
  try {
    return await fn(...args);
  } catch (err) {
    throw new ApiError(method, err?.message ?? String(err), err);
  }
}

const base = {
  call,
  mode: resolveMode,
  /** Minimise the frameless window. */
  window_min: () => call('window_min'),
  /** Close button: Python hides the window to the tray (ingest keeps running). */
  window_close: () => call('window_close'),
};

/** `api.<anyMethod>(...args)` forwards to the Python API; no edit needed for new methods. */
export const api = new Proxy(base, {
  get(target, prop) {
    if (prop in target) return target[prop];
    if (typeof prop !== 'string' || prop === 'then') return undefined;
    return (...args) => call(prop, ...args);
  },
});

export default api;
