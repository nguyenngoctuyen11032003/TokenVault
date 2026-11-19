// Chart drawing for #/overview (Task 15). Each function takes the chart host, the Overview
// payload and {t, signal, ...}; it returns false (and disposes the host's chart) when there is
// nothing to draw so the page can show an empty note instead of a blank chart.
// Colours: series keys go through palette.colorFor (same project/model/tool = same colour on
// every page); token kinds use fixed series colours so "cache read" always looks the same.

import { barChart, dispose, donutChart, lineChart } from '../charts.js';
import { SERIES_COLORS } from '../palette.js';
import { fmtTokens, localHour, shortDay, shortHour } from './homekit.js';
import { modelLabel } from './overview-kit.js';

const TOP_PROJECTS = 10;
const TOP_TOOLS = 10;

/** Fixed colours for token kinds (also used by the session page). */
export const KIND_COLORS = Object.freeze({
  input: SERIES_COLORS[3], // blue
  output: SERIES_COLORS[4], // violet
  cacheRead: SERIES_COLORS[2], // teal
  cacheWrite: SERIES_COLORS[1], // amber
  sidechain: SERIES_COLORS[6], // pink
});

const num = (v) => Number(v) || 0;

function shortMonth(month) {
  const m = /^(\d{4})-(\d{2})/.exec(String(month ?? ''));
  return m ? `${m[2]}/${m[1]}` : String(month ?? '');
}

/**
 * Token trend. mode: 'hour' (today), 'day' (overview.daily, last 90 days) or 'month'.
 * Returns {drawn, peak} where peak = {label, value} of the busiest point.
 */
export function drawTrend(host, overview, { mode, t, signal }) {
  let labels;
  let values;
  if (mode === 'hour') {
    const now = localHour();
    const points = overview.hourly || [];
    labels = points.map((p) => shortHour(p.hour));
    values = points.map((p) => (String(p.hour) > now ? null : num(p.total_tokens)));
  } else if (mode === 'month') {
    const points = overview.monthly || [];
    labels = points.map((p) => shortMonth(p.month));
    values = points.map((p) => num(p.total_tokens));
  } else {
    const points = overview.daily || [];
    labels = points.map((p) => shortDay(p.day));
    values = points.map((p) => num(p.total_tokens));
  }
  host.dataset.series = { hour: 'hourly', day: 'daily', month: 'monthly' }[mode];
  if (!values.some((v) => v > 0)) {
    dispose(host);
    return { drawn: false, peak: null };
  }
  let peak = 0;
  values.forEach((v, i) => {
    if ((v ?? 0) > (values[peak] ?? 0)) peak = i;
  });
  lineChart(
    host,
    { x: labels, series: [{ name: t('chart.tokens'), values }] },
    { label: t(`trend.label.${mode}`), unit: t('tokensUnit'), signal, peak: true },
  );
  return { drawn: true, peak: { label: labels[peak], value: values[peak] } };
}

/** Cache reads (+ cache writes) per day, or per hour for today. Returns average hit ratio. */
export function drawCache(host, overview, { hourly, t, signal }) {
  let labels;
  let reads;
  let writes;
  let ratio = null;
  if (hourly) {
    const now = localHour();
    const points = overview.hourly || [];
    labels = points.map((p) => shortHour(p.hour));
    const future = (p) => String(p.hour) > now;
    reads = points.map((p) => (future(p) ? null : num(p.cache_read_tokens)));
    writes = points.map((p) =>
      future(p) ? null : num(p.cache_create_5m_tokens) + num(p.cache_create_1h_tokens),
    );
  } else {
    const points = overview.cache_daily || [];
    labels = points.map((p) => shortDay(p.day));
    reads = points.map((p) => num(p.cache_read_tokens));
    writes = points.map((p) => num(p.cache_create_5m_tokens) + num(p.cache_create_1h_tokens));
    const active = points.filter((p) => num(p.cache_read_tokens) > 0);
    if (active.length) ratio = active.reduce((a, p) => a + num(p.hit_ratio), 0) / active.length;
  }
  if (!reads.some((v) => v > 0) && !writes.some((v) => v > 0)) {
    dispose(host);
    return { drawn: false, ratio };
  }
  lineChart(
    host,
    {
      x: labels,
      series: [
        { name: t('chart.cacheRead'), values: reads, color: KIND_COLORS.cacheRead },
        { name: t('chart.cacheWrite'), values: writes, color: KIND_COLORS.cacheWrite },
      ],
    },
    { label: t(hourly ? 'cache.labelHourly' : 'cache.label'), unit: t('tokensUnit'), signal },
  );
  return { drawn: true, ratio };
}

/** Horizontal bars: top projects by tokens, each in its palette colour. */
export function drawProjects(host, overview, { t, signal }) {
  const list = [...(overview.by_project || [])]
    .filter((p) => num(p.total_tokens) > 0)
    .sort((a, b) => num(b.total_tokens) - num(a.total_tokens))
    .slice(0, TOP_PROJECTS);
  if (!list.length) {
    dispose(host);
    return false;
  }
  barChart(
    host,
    {
      categories: list.map((p) => String(p.project || '') || t('noProject')),
      keys: list.map((p) => String(p.project || '')),
      values: list.map((p) => num(p.total_tokens)),
    },
    { label: t('projects.label'), unit: t('tokensUnit'), seriesName: t('chart.tokens'), signal, horizontal: true },
  );
  return true;
}

/** Donut of tokens by model with the total in the centre ("(no model)" for a missing one). */
export function drawModels(host, overview, { t, signal }) {
  const noModel = t('filter.noModel');
  const items = (overview.by_model || [])
    .filter((m) => num(m.total_tokens) > 0)
    .sort((a, b) => num(b.total_tokens) - num(a.total_tokens))
    .map((m) => {
      const name = modelLabel(m.model, noModel);
      return { name, key: name, value: num(m.total_tokens) };
    });
  if (!items.length) {
    dispose(host);
    return false;
  }
  const total = items.reduce((a, d) => a + d.value, 0);
  donutChart(host, items, {
    label: t('models.label'),
    unit: t('tokensUnit'),
    centerText: fmtTokens(total),
    signal,
  });
  return true;
}

/** Bars: most used tools by call count. */
export function drawTools(host, overview, { t, signal }) {
  const list = [...(overview.top_tools || [])]
    .filter((x) => num(x.calls) > 0)
    .sort((a, b) => num(b.calls) - num(a.calls))
    .slice(0, TOP_TOOLS);
  if (!list.length) {
    dispose(host);
    return false;
  }
  barChart(
    host,
    {
      categories: list.map((x) => String(x.tool ?? '')),
      values: list.map((x) => num(x.calls)),
    },
    { label: t('tools.label'), unit: t('tools.unit'), seriesName: t('tools.calls'), signal, horizontal: true },
  );
  return true;
}
