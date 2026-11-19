// #/skills - how often the "Skill" tool was invoked. The wire protocol carries tool NAMES only,
// so the page counts calls of the tool named "Skill" per day and per employee; which skill was
// used is unknown and the page says so (always-visible note). Range: 7d / 30d / 90d / all
// (SkillsView ranges accepted by Aggregates). All data is untrusted and rendered as text.

import { barChart, disposeDetached, loadECharts } from '../charts.js';
import { C } from '../charts-core.js';
import { colorFor } from '../palette.js';
import {
  coalesce,
  debounce,
  fmtInt,
  fmtPct,
  h,
  LIVE_DEBOUNCE_MS,
  localDay,
  shortDay,
} from '../components/homekit.js';
import {
  createErrorBox,
  createKpiStrip,
  createRangeBar,
  DEFAULT_RANGE,
  fillDays,
  rankList,
} from '../components/skills-kit.js';

const TOP_N = 8;

export default async function render(container, ctx) {
  const t = (key, params) => ctx.t(`skills.${key}`, params);
  await Promise.all([
    ctx.useStylesheet('css/pages/people.css'),
    ctx.useStylesheet('css/pages/skills-shared.css'),
    ctx.useStylesheet('css/pages/skills-page.css'),
  ]);
  if (ctx.signal.aborted) return;
  const charts = loadECharts().catch((err) => {
    console.error('[skills] charts unavailable', err);
    return null;
  });

  let rev = 0;
  const range = createRangeBar({
    t,
    value: DEFAULT_RANGE,
    label: t('rangeLabel'),
    onChange: () => refresh(),
  });
  const errorBox = createErrorBox({ retryText: t('retry'), onRetry: () => refresh() });
  const kpis = createKpiStrip([
    { id: 'total', label: t('kpi.total'), accent: 'hot' },
    { id: 'days', label: t('kpi.days') },
    { id: 'people', label: t('kpi.people') },
    { id: 'peak', label: t('kpi.peak') },
  ]);
  const chartHost = h('div', { class: 'tv-skills-daily', dataset: { chart: 'daily' } });
  const rankHost = h('div', { class: 'tv-skills-ranklist', role: 'list', 'aria-label': t('rankTitle') });
  const tableBody = h('tbody');
  const empty = h('p', { class: 'tv-skills-empty', 'data-empty': 'skills', hidden: true }, t('empty'));
  const note = h(
    'p',
    { class: 'tv-skills-note', 'data-note': 'limitation', role: 'note' },
    h('b', null, t('noteLead')),
    ' ',
    t('note'),
  );

  const table = h(
    'table',
    { class: 'tv-table tv-skills-table' },
    h(
      'thead',
      null,
      h(
        'tr',
        null,
        h('th', { scope: 'col', class: 'tv-skills-table__rank' }, t('table.rank')),
        h('th', { scope: 'col' }, t('table.employee')),
        h('th', { scope: 'col', class: 'tv-num' }, t('table.count')),
        h('th', { scope: 'col', class: 'tv-num' }, t('table.share')),
      ),
    ),
    tableBody,
  );

  const body = h(
    'div',
    { class: 'tv-skills-body' },
    kpis.el,
    h(
      'div',
      { class: 'tv-grid tv-skills-charts' },
      h('tv-panel', { heading: t('dailyTitle') }, h('div', { class: 'tv-chart-area tv-skills-chartbox' }, chartHost)),
      h('tv-panel', { heading: t('rankTitle') }, rankHost),
    ),
    h('tv-panel', { heading: t('tableTitle') }, h('div', { class: 'tv-skills-tablewrap' }, table)),
  );

  const root = h(
    'div',
    { class: 'tv-skills-page tv-skills', dataset: { tvRev: '0' } },
    errorBox.el,
    h('tv-panel', { heading: t('panel'), class: 'tv-skills-head' }, h('div', { slot: 'actions' }, range.el), note, empty),
    body,
  );
  body.hidden = true;
  container.append(root);

  // ---------- updates ----------

  function updateKpis(view, daily) {
    const total = Number(view.total_invocations) || 0;
    const people = (view.by_employee || []).filter((e) => Number(e.count) > 0).length;
    const active = daily.filter((d) => d.count > 0);
    const peak = active.reduce((best, d) => (d.count > (best?.count ?? 0) ? d : best), null);
    kpis.set('total', fmtInt(total), fmtInt(total));
    kpis.set('days', fmtInt(active.length));
    kpis.set('people', fmtInt(people));
    const peakText = peak ? t('peakValue', { day: shortDay(peak.day), count: fmtInt(peak.count) }) : '-';
    kpis.set('peak', peakText, peak ? `${peak.day}: ${fmtInt(peak.count)}` : '');
  }

  function updateRanking(view) {
    const total = Number(view.total_invocations) || 0;
    const list = (view.by_employee || [])
      .map((e) => ({ key: String(e.key ?? ''), name: String(e.display_name || e.key || ''), value: Number(e.count) || 0 }))
      .filter((e) => e.value > 0);
    rankList(rankHost, list.slice(0, TOP_N).map((e) => ({ ...e, href: `#/employee/${encodeURIComponent(e.key)}` })), {
      total,
      unit: t('unit'),
      t,
    });
    tableBody.replaceChildren(
      ...list.map((e, i) =>
        h(
          'tr',
          { dataset: { key: e.key } },
          h('td', { class: 'tv-skills-table__rank' }, String(i + 1)),
          h(
            'td',
            null,
            h(
              'span',
              { class: 'tv-skills-table__who' },
              h('i', { class: 'tv-skills-rank__swatch', style: { background: colorFor(e.key) }, 'aria-hidden': 'true' }),
              h(
                'a',
                { class: 'tv-clip tv-skills-rank__link', href: `#/employee/${encodeURIComponent(e.key)}`, title: e.name },
                e.name,
              ),
            ),
          ),
          h('td', { class: 'tv-num tv-num--hot' }, fmtInt(e.value)),
          h('td', { class: 'tv-num' }, fmtPct(total ? e.value / total : 0)),
        ),
      ),
    );
  }

  async function updateChart(daily) {
    const echarts = await charts;
    if (!echarts || ctx.signal.aborted) return;
    barChart(
      chartHost,
      { categories: daily.map((d) => shortDay(d.day)), values: daily.map((d) => d.count) },
      { label: t('dailyLabel'), unit: t('unit'), seriesName: t('dailySeries'), color: C.hot, signal: ctx.signal },
    );
  }

  async function draw() {
    const rng = range.get();
    try {
      const view = await ctx.api.skills(rng);
      if (ctx.signal.aborted) return;
      if (rng !== range.get()) return; // superseded; a re-run is queued
      if (!view || typeof view !== 'object') throw new Error('empty response');
      errorBox.hide();
      const daily = fillDays(view.daily, rng, localDay(0));
      const none = !(Number(view.total_invocations) > 0);
      empty.hidden = !none;
      body.hidden = none;
      root.dataset.range = rng;
      updateKpis(view, daily);
      updateRanking(view);
      if (!none) await updateChart(daily);
    } catch (err) {
      if (ctx.signal.aborted) return;
      console.error('[skills] load failed', err);
      errorBox.show(t('loadError', { detail: err?.message || String(err) }));
    }
    if (ctx.signal.aborted) return;
    rev += 1;
    root.dataset.tvRev = String(rev);
  }

  const refresh = coalesce(draw);
  ctx.onLive(debounce(() => refresh(), LIVE_DEBOUNCE_MS, ctx.signal));
  await refresh();
}

export function destroy() {
  disposeDetached();
}
