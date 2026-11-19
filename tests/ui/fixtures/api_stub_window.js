// Test-only: adds the Manager window methods (docs/superpowers/specs/manager-api-contract.md,
// "Window methods") to the fake pywebview bridge from conftest.stub_script. State lives in
// window.__tvWin; every call is recorded in window.__tvCalls. Options (set before boot through
// window.__tvWinOpts): native_resize (bool), extra (object merged into the state, e.g. the
// exact payload Task 12's window_control.state() returns), passive (toggles return null and change nothing,
// so only explicit tv.onWindowState pushes change the UI), bounds_delay_ms (latency of
// window_set_bounds, used to prove that at most one call is in flight).
(() => {
  const api = window.pywebview && window.pywebview.api;
  if (!api) return;
  const opts = window.__tvWinOpts || {};
  const calls = window.__tvCalls || (window.__tvCalls = []);
  const clone = (v) => JSON.parse(JSON.stringify(v ?? null));
  const W = (window.__tvWin = {
    state: {
      maximized: false,
      fullscreen: false,
      x: 100,
      y: 80,
      width: 1440,
      height: 900,
      min_width: 1100,
      min_height: 720,
      native_resize: !!opts.native_resize,
      screen: { x: 0, y: 0, width: 2560, height: 1440, work_x: 0, work_y: 0, work_width: 2560, work_height: 1400 },
    },
    inflight: 0,
    maxInflight: 0,
  });
  Object.assign(W.state, clone(opts.extra || {}));
  const result = () => (opts.passive ? null : clone(W.state));
  const rec = (name, fn, delay = 0) => (...args) => {
    calls.push({ name, args: clone(args) });
    const value = fn(...args);
    if (!delay) return Promise.resolve(value);
    return new Promise((resolve) => setTimeout(() => resolve(value), delay));
  };
  const set = (patch) => {
    if (!opts.passive) Object.assign(W.state, patch);
    return result();
  };
  api.window_state = rec('window_state', () => clone(W.state));
  api.window_toggle_maximize = rec('window_toggle_maximize', () =>
    set({ maximized: !W.state.maximized }));
  api.window_maximize = rec('window_maximize', () => set({ maximized: true }));
  api.window_restore = rec('window_restore', () => set({ maximized: false }));
  api.window_toggle_fullscreen = rec('window_toggle_fullscreen', () =>
    set({ fullscreen: !W.state.fullscreen }));
  api.window_set_fullscreen = rec('window_set_fullscreen', (on) => set({ fullscreen: !!on }));
  api.window_get_bounds = rec('window_get_bounds', () => {
    const { x, y, width, height } = W.state;
    return { x, y, width, height };
  });
  const setBounds = rec(
    'window_set_bounds',
    (x, y, width, height) => {
      Object.assign(W.state, { x, y, width, height });
      return clone(W.state);
    },
    Number(opts.bounds_delay_ms || 0),
  );
  api.window_set_bounds = (...args) => {
    W.inflight += 1;
    W.maxInflight = Math.max(W.maxInflight, W.inflight);
    return setBounds(...args).finally(() => {
      W.inflight -= 1;
    });
  };
})();
