// UI scale for large windows (Task 20).
//
// The NitroSense components are tuned in CSS pixels for ~1100..1600 px wide windows. On big
// windows (2560x1440, 4K, maximised) the whole UI is zoomed by `--tv-scale` (body { zoom }) so
// type, gauges and charts grow, while the layout still gains space: the scale grows at HALF the
// rate of the window, so a 3840x2160 window lays out like a 2560x1440 one at 1.5x.
//   scale = clamp(1, 1 + (min(W / 1600, H / 900) - 1) / 2, 1.5), rounded to 0.01
// window.css divides the window's 100vw/100vh by the same factor. Exposed as
// `--tv-scale` on <html> and `tv.uiScale`.

export const BASE_W = 1600;
export const BASE_H = 900;
export const MAX_SCALE = 1.5;

export function computeScale(width, height) {
  const ratio = Math.min(width / BASE_W, height / BASE_H);
  if (!Number.isFinite(ratio)) return 1;
  const s = Math.min(MAX_SCALE, Math.max(1, 1 + (ratio - 1) / 2));
  return Math.round(s * 100) / 100;
}

export function applyScale() {
  const s = computeScale(window.innerWidth, window.innerHeight);
  document.documentElement.style.setProperty('--tv-scale', String(s));
  const tv = globalThis.tv ?? (globalThis.tv = {});
  tv.uiScale = s;
  return s;
}

let installed = false;

export function installScale() {
  if (installed) return;
  installed = true;
  applyScale();
  window.addEventListener('resize', applyScale);
}
