// Chart plumbing shared by js/charts.js: ECharts loader, one-instance-per-element mount with
// ResizeObserver + signal disposal, the screen-reader data table, theme constants and number
// formatting. Pages import from js/charts.js (which re-exports what they need), not from here.

import { compactNumber, esc, useStylesheet } from './dom.js';

export const FONT = '"Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif';
export const C = {
  text: '#e8e8eb',
  muted: '#a1a1a9',
  grid: '#33100f',
  axis: '#4a1418',
  red: '#e10b1f',
  hot: '#ff2d3f',
  panel: '#151518',
  tooltipBg: 'rgba(12, 12, 14, 0.96)',
};

let loading = null;

/** Load vendor/echarts.min.js (UMD global) and the chart stylesheet once. */
export function loadECharts() {
  const css = useStylesheet('css/charts.css');
  if (!loading) {
    loading = globalThis.echarts
      ? Promise.resolve(globalThis.echarts)
      : new Promise((resolve, reject) => {
          const script = document.createElement('script');
          script.src = new URL('../vendor/echarts.min.js', import.meta.url).href;
          script.async = true;
          script.onload = () =>
            globalThis.echarts ? resolve(globalThis.echarts) : reject(new Error('echarts missing'));
          script.onerror = () => {
            loading = null;
            script.remove();
            reject(new Error('echarts failed to load'));
          };
          document.head.append(script);
        });
  }
  return Promise.all([loading, css]).then(([echarts]) => echarts);
}

const live = new Map(); // element -> { chart, ro, desc, frame }
let seq = 0;

function reducedMotion() {
  return globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
}

export function fmtFull(value) {
  const n = Number(value);
  return Number.isFinite(n) ? new Intl.NumberFormat('vi-VN').format(Math.round(n * 100) / 100) : '';
}

export function fmtCompact(value) {
  return compactNumber(value) ?? '';
}

export function withUnit(text, unit) {
  return unit ? `${text} ${unit}` : text;
}

export function rgba(hex, alpha) {
  const v = Number.parseInt(String(hex).slice(1), 16);
  return `rgba(${(v >> 16) & 255}, ${(v >> 8) & 255}, ${v & 255}, ${alpha})`;
}

export function mount(el, opts) {
  const ec = globalThis.echarts;
  if (!ec) throw new Error('charts: call loadECharts() before drawing');
  let rec = live.get(el);
  if (!rec) {
    const chart = ec.init(el, null, { renderer: 'svg' });
    rec = { chart, ro: null, desc: null, frame: 0 };
    rec.ro = new ResizeObserver(() => {
      cancelAnimationFrame(rec.frame);
      rec.frame = requestAnimationFrame(() => {
        if (chart.isDisposed() || !el.isConnected) return;
        chart.resize();
        rec.onResize?.();
      });
    });
    rec.ro.observe(el);
    live.set(el, rec);
    el.classList.add('tv-chart');
    if (opts.signal) {
      if (opts.signal.aborted) queueMicrotask(() => dispose(el));
      else opts.signal.addEventListener('abort', () => dispose(el), { once: true });
    }
  }
  return rec;
}

/** Visually hidden data table + aria wiring for screen readers. */
export function describe(el, rec, label, head, rows) {
  el.setAttribute('role', 'img');
  if (label) el.setAttribute('aria-label', label);
  if (!rec.desc) {
    rec.desc = document.createElement('div');
    rec.desc.className = 'tv-chart-sr';
    rec.desc.id = `tv-chart-desc-${++seq}`;
    el.after(rec.desc);
    el.setAttribute('aria-describedby', rec.desc.id);
  } else if (!rec.desc.isConnected) {
    el.after(rec.desc);
  }
  const table = document.createElement('table');
  const caption = document.createElement('caption');
  caption.textContent = label || '';
  const thead = document.createElement('thead');
  const tbody = document.createElement('tbody');
  const tr = document.createElement('tr');
  for (const cell of head) {
    const th = document.createElement('th');
    th.textContent = cell;
    tr.append(th);
  }
  thead.append(tr);
  for (const row of rows) {
    const r = document.createElement('tr');
    for (const cell of row) {
      const td = document.createElement('td');
      td.textContent = cell;
      r.append(td);
    }
    tbody.append(r);
  }
  table.append(caption, thead, tbody);
  rec.desc.replaceChildren(table);
}

export function base(opts) {
  const anim = !reducedMotion() && opts.animation !== false;
  return {
    backgroundColor: 'transparent',
    animation: anim,
    animationDuration: anim ? 600 : 0,
    animationDurationUpdate: anim ? 450 : 0,
    textStyle: { color: C.text, fontFamily: FONT, fontSize: 12 },
    aria: { enabled: false }, // we provide our own label + data table
  };
}

export function tooltip(extra) {
  return {
    backgroundColor: C.tooltipBg,
    borderColor: C.red,
    borderWidth: 1,
    padding: [8, 12],
    textStyle: { color: C.text, fontFamily: FONT, fontSize: 12 },
    extraCssText: 'border-radius:0;box-shadow:0 6px 24px rgba(0,0,0,.6);',
    confine: true,
    ...extra,
  };
}

export function marker(color) {
  return `<span style="display:inline-block;width:8px;height:8px;margin-right:6px;background:${color}"></span>`;
}

export function axisRows(params, unit, format) {
  const list = Array.isArray(params) ? params : [params];
  const headText = list[0]?.axisValueLabel ?? list[0]?.name ?? '';
  const rows = list
    .filter((p) => p.value !== null && p.value !== undefined && p.value !== '-')
    .map(
      (p) =>
        `<div style="display:flex;gap:14px;justify-content:space-between">` +
        `<span>${marker(p.color)}${esc(p.seriesName)}</span>` +
        `<b style="font-variant-numeric:tabular-nums">${esc(withUnit(format(p.value), unit))}</b></div>`,
    )
    .join('');
  return `<div style="color:${C.muted};margin-bottom:4px">${esc(headText)}</div>${rows}`;
}

export const X_AXIS = {
  axisLine: { lineStyle: { color: C.axis } },
  axisTick: { show: false },
  axisLabel: { color: C.muted, fontSize: 11, hideOverlap: true },
  splitLine: { show: false },
};

export const Y_AXIS = {
  type: 'value',
  axisLine: { show: false },
  axisTick: { show: false },
  axisLabel: { color: C.muted, fontSize: 11, formatter: (v) => fmtCompact(v) },
  splitLine: { lineStyle: { color: C.grid, type: [4, 3] } },
  splitNumber: 4,
};

/** Dispose the chart on `el` (no-op if none). */
export function dispose(el) {
  const rec = live.get(el);
  if (!rec) return;
  live.delete(el);
  cancelAnimationFrame(rec.frame);
  rec.ro.disconnect();
  if (!rec.chart.isDisposed()) rec.chart.dispose();
  rec.desc?.remove();
  el.removeAttribute('aria-describedby');
}

/** Dispose every chart whose element is no longer in the document. */
export function disposeDetached() {
  for (const el of [...live.keys()]) if (!el.isConnected) dispose(el);
}

/** Number of live chart instances (diagnostics/tests). */
export function liveCount() {
  return live.size;
}
