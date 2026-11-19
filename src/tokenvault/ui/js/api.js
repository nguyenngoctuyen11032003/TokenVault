// Bridge to the Python Manager API.
//
// Inside the app, pywebview exposes the Python `Api` object on `window.pywebview.api` (its
// methods appear once the `pywebviewready` event fires). In a plain browser (dev server, UI
// tests without a stub) every call falls back to `POST /api/<method>` with the JSON-encoded
// argument list as body. Usage: `await api.overview(filter)`, `await api.call('overview', f)`.

// No `window.pywebview` this long after DOMContentLoaded -> plain browser, use http at once.
const ABSENT_MS = Number(globalThis.TV_API_ABSENT_MS ?? 300);
// `window.pywebview` exists but its api is not ready yet -> wait this long for pywebviewready.
const BRIDGE_WAIT_MS = Number(globalThis.TV_API_BRIDGE_WAIT_MS ?? 15000);

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
    const timers = [];
    let finished = false;
    const done = (mode) => {
      if (finished) return;
      finished = true;
      window.removeEventListener('pywebviewready', onReady);
      document.removeEventListener('DOMContentLoaded', armAbsentCheck);
      timers.forEach((id) => clearTimeout(id));
      clearInterval(poll);
      resolve(mode ?? (hasBridge() ? 'pywebview' : 'http'));
    };
    const onReady = () => done();
    window.addEventListener('pywebviewready', onReady);
    const poll = setInterval(() => hasBridge() && done('pywebview'), 50);
    // Plain browser: nothing named pywebview shortly after the DOM is ready.
    function armAbsentCheck() {
      timers.push(setTimeout(() => !globalThis.pywebview && done('http'), ABSENT_MS));
    }
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', armAbsentCheck, { once: true });
    } else {
      armAbsentCheck();
    }
    // pywebview present but its api never became ready: give up eventually.
    timers.push(setTimeout(() => done(), BRIDGE_WAIT_MS));
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
