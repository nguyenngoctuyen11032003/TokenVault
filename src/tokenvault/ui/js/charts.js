// Themed ECharts helpers (NitroSense dark/red look), SVG renderer, offline vendor copy.
//
//   await loadECharts();                       // once per page, before drawing
//   lineChart(el, {x, series: [{name, values}]}, {label, unit, signal, peak: true});
//   barChart(el, {categories, values}, {label, unit, signal, horizontal});
//   stackedBarChart(el, {categories, series: [{name, values}]}, {label, unit, signal});
//   donutChart(el, [{name, value}], {label, unit, signal, centerText});
//
// One ECharts instance per element: calling a helper again on the same element updates the
// chart in place (numbers move, nothing is torn down), so live refreshes keep the view stable.
// Instances are resized by a ResizeObserver and disposed when `opts.signal` aborts (pass the
// route's ctx.signal) or by dispose(el) / disposeDetached(). The element needs a real size
// (ECharts measures it): give chart hosts an explicit height in CSS.
//
// Accessibility: the host gets role="img", aria-label = opts.label, and aria-describedby
// pointing at a visually hidden table with every data point. Animation is off when the user
// prefers reduced motion. Series colours come from palette.js (same key -> same colour);
// a single-series line uses the red accent. Tooltip/legend names are escaped (untrusted data).

import { compactNumber, esc, useStylesheet } from './dom.js';
import { colorFor } from './palette.js';

const FONT = '"Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif';
const C = {
  text: '#e8e8eb',
  muted: '#a1a1a9',
  dim: '#6e6e76',
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

function fmtFull(value) {
  const n = Number(value);
  return Number.isFinite(n) ? new Intl.NumberFormat('vi-VN').format(Math.round(n * 100) / 100) : '';
}

function fmtCompact(value) {
  return compactNumber(value) ?? '';
}

function withUnit(text, unit) {
  return unit ? `${text} ${unit}` : text;
}

function rgba(hex, alpha) {
  const v = Number.parseInt(String(hex).slice(1), 16);
  return `rgba(${(v >> 16) & 255}, ${(v >> 8) & 255}, ${v & 255}, ${alpha})`;
}

function mount(el, opts) {
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
function describe(el, rec, label, head, rows) {
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

function base(opts) {
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

function tooltip(extra) {
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

function marker(color) {
  return `<span style="display:inline-block;width:8px;height:8px;margin-right:6px;background:${color}"></span>`;
}

function axisRows(params, unit, format) {
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

const X_AXIS = {
  axisLine: { lineStyle: { color: C.axis } },
  axisTick: { show: false },
  axisLabel: { color: C.muted, fontSize: 11, hideOverlap: true },
  splitLine: { show: false },
};

const Y_AXIS = {
  type: 'value',
  axisLine: { show: false },
  axisTick: { show: false },
  axisLabel: { color: C.muted, fontSize: 11, formatter: (v) => fmtCompact(v) },
  splitLine: { lineStyle: { color: C.grid, type: [4, 3] } },
  splitNumber: 4,
};

function seriesColor(s, index, opts) {
  if (s.color) return s.color;
  if (index === 0 && opts.primary !== false && opts.single !== false) return C.hot;
  return colorFor(s.key ?? s.name);
}

/**
 * Line chart. data = {x: [labels], series: [{name, values, key?, color?}]}.
 * opts: {label, unit, signal, peak (mark the max of the first series), area (default true),
 *        format (value -> text, default full vi-VN number), yMin}.
 */
export function lineChart(el, data, opts = {}) {
  const rec = mount(el, opts);
  const x = data.x || [];
  const series = data.series || [];
  const single = series.length === 1;
  const format = opts.format || fmtFull;
  rec.chart.setOption(
    {
      ...base(opts),
      grid: { left: 8, right: 16, top: opts.peak ? 30 : 18, bottom: 6, containLabel: true },
      tooltip: tooltip({
        trigger: 'axis',
        axisPointer: { type: 'line', lineStyle: { color: C.hot, width: 1, type: [3, 3] } },
        formatter: (p) => axisRows(p, opts.unit, format),
      }),
      legend: single
        ? { show: false }
        : {
            show: true,
            top: 0,
            right: 8,
            icon: 'rect',
            itemWidth: 10,
            itemHeight: 3,
            textStyle: { color: C.muted, fontSize: 11 },
          },
      xAxis: { ...X_AXIS, type: 'category', data: x, boundaryGap: false },
      yAxis: { ...Y_AXIS, min: opts.yMin ?? 0 },
      series: series.map((s, i) => {
        const color = seriesColor(s, i, { ...opts, single });
        const area = opts.area !== false;
        return {
          id: `s${i}`,
          name: s.name,
          type: 'line',
          data: s.values,
          smooth: 0.3,
          showSymbol: false,
          symbol: 'circle',
          symbolSize: 7,
          connectNulls: false,
          color,
          lineStyle: {
            width: i === 0 ? 2.25 : 1.75,
            color,
            shadowColor: i === 0 ? rgba(color, 0.55) : 'transparent',
            shadowBlur: i === 0 ? 8 : 0,
          },
          emphasis: { focus: 'series', lineStyle: { width: 2.5 } },
          areaStyle: area
            ? {
                color: new globalThis.echarts.graphic.LinearGradient(0, 0, 0, 1, [
                  { offset: 0, color: rgba(color, i === 0 ? 0.36 : 0.16) },
                  { offset: 1, color: rgba(color, 0) },
                ]),
              }
            : undefined,
          markPoint:
            opts.peak && i === 0
              ? {
                  symbol: 'circle',
                  symbolSize: 9,
                  itemStyle: { color: C.hot, borderColor: '#fff', borderWidth: 1.5 },
                  label: {
                    show: true,
                    position: 'top',
                    distance: 6,
                    color: C.text,
                    fontSize: 11,
                    fontWeight: 600,
                    formatter: (p) => fmtCompact(p.value),
                  },
                  data: [{ type: 'max', name: 'max' }],
                }
              : undefined,
        };
      }),
    },
    { notMerge: false, replaceMerge: ['series'] },
  );
  describe(
    el,
    rec,
    opts.label,
    ['', ...series.map((s) => s.name)],
    x.map((label, j) =>
      [String(label), ...series.map((s) => withUnit(format(s.values?.[j] ?? 0), opts.unit))],
    ),
  );
  return rec.chart;
}

/**
 * Bar chart. data = {categories, values, keys?} - each bar takes colorFor(key ?? category),
 * or opts.color for a single colour. opts: {label, unit, signal, horizontal, format}.
 */
export function barChart(el, data, opts = {}) {
  const rec = mount(el, opts);
  const categories = data.categories || [];
  const values = data.values || [];
  const format = opts.format || fmtFull;
  const horizontal = !!opts.horizontal;
  const catAxis = {
    ...X_AXIS,
    type: 'category',
    data: categories,
    axisLabel: {
      ...X_AXIS.axisLabel,
      width: horizontal ? 110 : 80,
      overflow: 'truncate',
      interval: horizontal ? 0 : 'auto',
    },
    inverse: horizontal,
  };
  rec.chart.setOption(
    {
      ...base(opts),
      grid: { left: 8, right: horizontal ? 40 : 12, top: 14, bottom: 6, containLabel: true },
      tooltip: tooltip({
        trigger: 'axis',
        axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(255,45,63,0.08)' } },
        formatter: (p) => axisRows(p, opts.unit, format),
      }),
      xAxis: horizontal ? { ...Y_AXIS } : catAxis,
      yAxis: horizontal ? catAxis : { ...Y_AXIS },
      series: [
        {
          id: 'bars',
          name: opts.seriesName || opts.label || '',
          type: 'bar',
          barMaxWidth: horizontal ? 14 : 26,
          data: values.map((v, i) => ({
            value: v,
            itemStyle: {
              color: opts.color || colorFor(data.keys?.[i] ?? categories[i]),
              borderRadius: horizontal ? [0, 2, 2, 0] : [2, 2, 0, 0],
            },
          })),
          label: horizontal
            ? {
                show: true,
                position: 'right',
                color: C.muted,
                fontSize: 11,
                formatter: (p) => fmtCompact(p.value),
              }
            : { show: false },
        },
      ],
    },
    { replaceMerge: ['series'] },
  );
  describe(
    el,
    rec,
    opts.label,
    ['', opts.seriesName || opts.unit || ''],
    categories.map((c, i) => [String(c), withUnit(format(values[i] ?? 0), opts.unit)]),
  );
  return rec.chart;
}

/**
 * Stacked bars. data = {categories, series: [{name, values, key?, color?}]}.
 * opts: {label, unit, signal, format}.
 */
export function stackedBarChart(el, data, opts = {}) {
  const rec = mount(el, opts);
  const categories = data.categories || [];
  const series = data.series || [];
  const format = opts.format || fmtFull;
  rec.chart.setOption(
    {
      ...base(opts),
      grid: { left: 8, right: 12, top: 30, bottom: 6, containLabel: true },
      tooltip: tooltip({
        trigger: 'axis',
        axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(255,45,63,0.08)' } },
        formatter: (p) => axisRows(p, opts.unit, format),
      }),
      legend: {
        top: 0,
        right: 8,
        type: 'scroll',
        icon: 'rect',
        itemWidth: 10,
        itemHeight: 10,
        textStyle: { color: C.muted, fontSize: 11 },
        pageTextStyle: { color: C.muted },
      },
      xAxis: { ...X_AXIS, type: 'category', data: categories },
      yAxis: { ...Y_AXIS },
      series: series.map((s, i) => ({
        id: `s${i}`,
        name: s.name,
        type: 'bar',
        stack: 'total',
        barMaxWidth: 22,
        data: s.values,
        itemStyle: {
          color: s.color || colorFor(s.key ?? s.name),
          borderColor: C.panel,
          borderWidth: 0.5,
        },
        emphasis: { focus: 'series' },
      })),
    },
    { replaceMerge: ['series'] },
  );
  describe(
    el,
    rec,
    opts.label,
    ['', ...series.map((s) => s.name)],
    categories.map((c, j) => [
      String(c),
      ...series.map((s) => withUnit(format(s.values?.[j] ?? 0), opts.unit)),
    ]),
  );
  return rec.chart;
}

/**
 * Donut with the total in the centre. data = [{name, value, key?, color?}].
 * opts: {label, unit, signal, centerText (default compact total),
 *        legend: 'right'|'bottom'|false (default: right when the host is >= 400px wide)}.
 */
export function donutChart(el, data, opts = {}) {
  const rec = mount(el, opts);
  const items = (data || []).filter((d) => Number(d.value) > 0);
  const total = items.reduce((sum, d) => sum + Number(d.value), 0);
  // 'auto' (default): legend on the right when there is room, else below the ring.
  const pick = () => (el.clientWidth >= 400 ? 'right' : 'bottom');
  const legend = opts.legend ?? pick();
  rec.onResize = opts.legend === undefined
    ? () => {
        if (pick() !== legend) donutChart(el, data, opts);
      }
    : null;
  const cx = legend === 'right' ? '30%' : '50%';
  const cy = legend === 'bottom' ? '44%' : '50%';
  const titleTop = legend === 'bottom' ? Math.max(0, Math.round(el.clientHeight * 0.44) - 18) : 'middle';
  const format = opts.format || fmtFull;
  rec.chart.setOption(
    {
      ...base(opts),
      title: {
        text: opts.centerText ?? fmtCompact(total),
        subtext: opts.unit || '',
        left: cx,
        top: titleTop,
        textAlign: 'center',
        itemGap: 2,
        textStyle: { color: C.text, fontSize: 20, fontWeight: 600, fontFamily: FONT },
        subtextStyle: { color: C.muted, fontSize: 10, fontFamily: FONT },
      },
      tooltip: tooltip({
        trigger: 'item',
        formatter: (p) =>
          `${marker(p.color)}${esc(p.name)}<br/><b>${esc(withUnit(format(p.value), opts.unit))}</b>` +
          ` <span style="color:${C.muted}">${esc(p.percent.toFixed(1))}%</span>`,
      }),
      legend: legend
        ? {
            show: true,
            type: legend === 'right' ? 'scroll' : 'plain',
            orient: legend === 'right' ? 'vertical' : 'horizontal',
            ...(legend === 'right' ? { right: 4, top: 'middle' } : { bottom: 0, left: 'center' }),
            icon: 'rect',
            itemWidth: 10,
            itemHeight: 10,
            itemGap: 10,
            textStyle: {
              color: C.text,
              fontSize: 12,
              width: 150,
              overflow: 'truncate',
            },
            pageTextStyle: { color: C.muted },
            pageIconColor: C.hot,
            formatter: (name) => {
              const item = items.find((d) => d.name === name);
              const pct = total ? Math.round((Number(item?.value || 0) / total) * 100) : 0;
              return `${name}  ${pct}%`;
            },
          }
        : { show: false },
      series: [
        {
          id: 'donut',
          name: opts.label || '',
          type: 'pie',
          center: [cx, cy],
          radius: legend === 'bottom' ? ['50%', '70%'] : ['60%', '82%'],
          padAngle: items.length > 1 ? 1.5 : 0,
          avoidLabelOverlap: true,
          itemStyle: { borderRadius: 3, borderColor: C.panel, borderWidth: 1 },
          label: { show: false },
          labelLine: { show: false },
          emphasis: { scale: true, scaleSize: 4, label: { show: false } },
          data: items.map((d) => ({
            name: d.name,
            value: d.value,
            itemStyle: { color: d.color || colorFor(d.key ?? d.name) },
          })),
        },
      ],
    },
    { replaceMerge: ['series'] },
  );
  describe(
    el,
    rec,
    opts.label,
    ['', opts.unit || '', '%'],
    items.map((d) => [
      String(d.name),
      withUnit(format(d.value), opts.unit),
      total ? `${((Number(d.value) / total) * 100).toFixed(1)}%` : '0%',
    ]),
  );
  return rec.chart;
}

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

/** Dispose every chart inside `root` (or all charts). */
export function disposeAll(root) {
  for (const el of [...live.keys()]) if (!root || root.contains(el)) dispose(el);
}

/** Number of live chart instances (diagnostics/tests). */
export function liveCount() {
  return live.size;
}
