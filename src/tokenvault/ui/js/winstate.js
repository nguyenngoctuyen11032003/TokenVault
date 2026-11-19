// Window state sync (Task 20). Python owns the real window; this module mirrors its state.
//
//   init(api)       -> asks api.window_state() once at boot, then follows Python's pushes
//                      `window.evaluate_js("tv.onWindowState(<WindowState json>)")`.
//   getState()      -> current state (defaults until Python answers)
//   subscribe(cb)   -> cb(state) on every accepted change; returns an unsubscribe function
//   applyState(p)   -> same entry point as tv.onWindowState (object or JSON string)
//
// WindowState (docs/superpowers/specs/manager-api-contract.md, "Window methods"):
//   {maximized, fullscreen, x, y, width, height, min_width, min_height, screen,
//    native_resize?, native_region?}
// native_resize: true -> the OS resizes the window, the UI draws no resize handles.
// native_region: true -> the OS window region is cut to the chamfer (Python's transparency
//   fallback), so the cut corners are not part of the window; the resize handles move inside.
// `maximized` and `fullscreen` must be booleans, otherwise the payload is dropped (logged with
// console.warn). The root <html> and `.tv-window` get `window--maximized` / `window--fullscreen`;
// window.css turns the chamfer off (`--tv-chamfer: 0`) in those states.

export const DEFAULT_MIN = { width: 1100, height: 720 };

const DEFAULTS = Object.freeze({
  maximized: false,
  fullscreen: false,
  native_resize: false,
  native_region: false,
  min_width: DEFAULT_MIN.width,
  min_height: DEFAULT_MIN.height,
  /** false until Python answered window_state() or pushed a valid state */
  available: false,
});

let state = { ...DEFAULTS };
let applied = 0; // accepted states so far (a push may arrive before window_state() answers)
const listeners = new Set();

const positive = (v, fallback) => (Number.isFinite(v) && v > 0 ? v : fallback);

/** Parse + validate a pushed payload; returns a normalised state or null when malformed. */
export function normalize(payload) {
  let data = payload;
  if (typeof data === 'string') {
    try {
      data = JSON.parse(data);
    } catch {
      return null;
    }
  }
  if (!data || typeof data !== 'object' || Array.isArray(data)) return null;
  if (typeof data.maximized !== 'boolean' || typeof data.fullscreen !== 'boolean') return null;
  return {
    ...data,
    native_resize: data.native_resize === true,
    native_region: data.native_region === true,
    min_width: positive(data.min_width, DEFAULT_MIN.width),
    min_height: positive(data.min_height, DEFAULT_MIN.height),
    available: true,
  };
}

function paint() {
  const roots = [document.documentElement, document.querySelector('.tv-window')];
  for (const el of roots) {
    if (!el) continue;
    el.classList.toggle('window--maximized', state.maximized && !state.fullscreen);
    el.classList.toggle('window--fullscreen', state.fullscreen);
  }
}

/** Accept a WindowState (object or JSON string). Returns true when it was applied. */
export function applyState(payload) {
  const next = normalize(payload);
  if (!next) {
    console.warn('[winstate] malformed window state ignored', payload);
    return false;
  }
  state = next;
  applied += 1;
  paint();
  for (const cb of [...listeners]) {
    try {
      cb(state);
    } catch (err) {
      console.error('[winstate] listener failed', err);
    }
  }
  return true;
}

/** Local change (browser Fullscreen API fallback in the dev server): merge and apply. */
export function patchState(patch) {
  return applyState({ ...state, ...patch });
}

export function getState() {
  return { ...state };
}

export function subscribe(cb) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

export function isFlat() {
  return state.maximized || state.fullscreen;
}

/** Install tv.onWindowState and ask Python for the initial state (never throws). */
export async function init(api) {
  const tv = globalThis.tv ?? (globalThis.tv = {});
  tv.onWindowState = (payload) => {
    applyState(payload);
  };
  Object.defineProperty(tv, 'windowState', { get: getState, configurable: true });
  paint();
  const before = applied;
  try {
    const initial = await api.window_state();
    if (initial != null && applied === before) applyState(initial);
  } catch (err) {
    // Older bridge or plain browser: keep the defaults (normal window, JS resize handles).
    console.info('[winstate] window_state unavailable', err?.message ?? err);
  }
  return getState();
}
