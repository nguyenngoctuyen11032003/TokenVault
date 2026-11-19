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
// (ECharts measures it): give chart hosts an explicit height in CSS. Loader, mount, a11y table,
// theme constants and formatting live in charts-core.js.
//
// Accessibility: the host gets role="img", aria-label = opts.label, and aria-describedby
// pointing at a visually hidden table with every data point. Animation is off when the user
// prefers reduced motion. Series colours come from palette.js (same key -> same colour);
// a single-series line uses the red accent. Tooltip/legend names are escaped (untrusted data).

import {
  axisRows,
  base,
  C,
  describe,
  FONT,
  fmtCompact,
  fmtFull,
  marker,
  mount,
  rgba,
  tooltip,
  withUnit,
  X_AXIS,
  Y_AXIS,
} from './charts-core.js';
import { esc } from './dom.js';
import { colorFor } from './palette.js';

export { dispose, disposeDetached, liveCount, loadECharts } from './charts-core.js';

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
