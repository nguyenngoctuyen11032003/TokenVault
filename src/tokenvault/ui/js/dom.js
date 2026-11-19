// Small DOM helpers shared by the shell and route modules.

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

/** Escape a value for interpolation into an HTML template string. */
export function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ESC[c]);
}

const UI_ROOT = new URL('../', import.meta.url);

/**
 * Ensure a stylesheet is linked once. `href` is relative to the ui/ root
 * (e.g. 'css/pages/home.css'). Resolves when loaded (or immediately if already present).
 */
export function useStylesheet(href) {
  const url = new URL(href, UI_ROOT).href;
  const existing = document.querySelector(`link[rel="stylesheet"][data-tv-href="${CSS.escape(url)}"]`);
  if (existing) return Promise.resolve(existing);
  return new Promise((resolve, reject) => {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = url;
    link.dataset.tvHref = url;
    link.onload = () => resolve(link);
    link.onerror = () => reject(new Error(`stylesheet failed: ${href}`));
    document.head.append(link);
  });
}

/** Compact number for gauges/KPIs: 950 -> "950", 4820000 -> "4.82M", 812000 -> "812K". */
export function compactNumber(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  const abs = Math.abs(n);
  const units = [
    [1e9, 'B'],
    [1e6, 'M'],
    [1e3, 'K'],
  ];
  for (const [size, suffix] of units) {
    if (abs >= size) {
      const scaled = n / size;
      const digits = Math.abs(scaled) >= 100 ? 0 : Math.abs(scaled) >= 10 ? 1 : 2;
      return `${Number(scaled.toFixed(digits))}${suffix}`;
    }
  }
  return String(Number(n.toFixed(abs >= 100 ? 0 : 1)));
}
