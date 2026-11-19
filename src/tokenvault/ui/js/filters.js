// Global filter store shared by every page (team, employee email, project, model, range).
//   filters.get()              -> current state (a copy)
//   filters.set({team: 'Dev'}) -> merges; notifies subscribers only if something changed
//   const off = filters.subscribe((state, changedKeys) => ...); off();
// State is persisted to localStorage; storage failures (private mode, corrupt JSON) are ignored.

export const STORAGE_KEY = 'tv.filters.v1';
export const DEFAULT_FILTERS = Object.freeze({
  team: null,
  email: null,
  project: null,
  model: null,
  range: 'today',
});

function readStored() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function writeStored(state) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    /* storage unavailable: keep in memory only */
  }
}

export function createStore(initial) {
  let state = { ...initial };
  const listeners = new Set();
  return {
    get: () => ({ ...state }),
    set(patch) {
      const changed = Object.keys(patch || {}).filter((k) => !Object.is(state[k], patch[k]));
      if (!changed.length) return false;
      state = { ...state, ...patch };
      writeStored(state);
      const snapshot = { ...state };
      for (const cb of [...listeners]) {
        try {
          cb(snapshot, changed);
        } catch (err) {
          console.error('[filters] subscriber failed', err);
        }
      }
      return true;
    },
    reset() {
      return this.set({ ...DEFAULT_FILTERS });
    },
    subscribe(cb) {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
  };
}

export const filters = createStore({ ...DEFAULT_FILTERS, ...readStored() });
