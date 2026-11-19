// #/overview — full usage overview (reached from the home page; no nav entry).
// Filter bar (team, employee, project, model, range — the shared ctx.filters store), totals
// strip with the API-equivalent cost estimate (UNKNOWN / "+" partial, unpriced models named),
// token trend (hourly for today, daily = last 90 days, monthly toggle for long ranges), daily
// cache reads, tokens by project, tokens by model (donut), top tools, recent sessions.
// A filter with no data shows an empty state (with "clear filters"), never blank charts.
// Live events refetch after a 250 ms trailing debounce and update everything in place.

import { disposeDetached, loadECharts } from '../charts.js';
import {
  drawCache,
  drawModels,
  drawProjects,
  drawTools,
  drawTrend,
} from '../components/overview-charts.js';
import { cachedCall } from '../components/overview-fetch.js';
import { createFilterBar } from '../components/overview-filterbar.js';
import {
  CLEAR_PATCH,
  costCell,
  emptyState,
  errorBanner,
  hasNarrowing,
  ovFilter,
  ovRange,
  rangeDays,
  rememberSessions,
  sessionHref,
  swatchName,
} from '../components/overview-kit.js';
import {
  clipText,
  coalesce,
  debounce,
  fmtInt,
  fmtTokens,
  h,
  LIVE_DEBOUNCE_MS,
  shortTime,
} from '../components/homekit.js';

import { createReportButton } from '../components/report-dialog.js';

const FILTER_SHARE_MS = 1500; // a filter change may reuse an answer this young
const TOTALS = ['tokens', 'input', 'output', 'cache_read', 'cache_write', 'sessions', 'events', 'cost'];

export default async function render(container, ctx) {
  const t = (key, params) => ctx.t(`overview.${key}`, params);
  await Promise.all([
    ctx.useStylesheet('css/pages/people.css'),
    ctx.useStylesheet('css/pages/overview-shared.css'),
    ctx.useStylesheet('css/pages/overview.css'),
  ]);
  if (ctx.signal.aborted) return;
  const bar = await createFilterBar(ctx);
  const exportBtn = await createReportButton({
    api: ctx.api,
    signal: ctx.signal,
    attrs: { 'data-report-open': 'all', slot: 'actions' },
    preset: () => {
      const f = ctx.filters.get();
      return { scope: f.team ? 'team' : 'all', team: f.team ?? undefined, range: f.range };
    },
  });
  if (ctx.signal.aborted) return;
  const charts = loadECharts().catch((err) => {
    console.error('[overview] charts unavailable', err);
    return null;
  });

  const unknown = t('unknownCost');
  let rev = 0;
  let granularity = null; // null = automatic ('month' for ranges longer than 90 days)
  let last = null; // last Overview payload (granularity switch redraws without refetch)
  let paintSeq = 0; // bumped for every payload about to be painted
  let fresh = false; // next draw must not reuse a cached answer (live event)

  // ---------- layout ----------
  const totals = {};
  const totalsStrip = h(
    'div',
    { class: 'tv-ovtotals', role: 'list' },
    TOTALS.map((key) => {
      totals[key] = h('b', { class: 'tv-ovtotals__value' });
      return h(
        'div',
        { class: `tv-ovtotals__item tv-ovtotals__item--${key}`, role: 'listitem', dataset: { total: key } },
        h('span', { class: 'tv-ovtotals__label' }, t(`totals.${key}`)),
        totals[key],
      );
    }),
  );
  const costInfo = h('p', { class: 'tv-ov-costinfo', 'data-cost-info': '', hidden: true });

  const error = errorBanner((detail) => t('loadError', { detail }), t('retry'), () => refresh());
  const empty = emptyState({
    lead: t('empty.lead'),
    hint: t('empty.hint'),
    action: t('empty.clear'),
    onAction: () => ctx.filters.set({ ...CLEAR_PATCH }),
  });

  const host = (name) => h('div', { class: 'tv-ov-chart', dataset: { chart: name } });
  const hosts = {
    daily: host('daily'),
    cache: host('cache'),
    projects: host('projects'),
    models: host('models'),
    tools: host('tools'),
  };
  const notes = {};
  const box = (name, cls) => {
    notes[name] = h('p', { class: 'tv-muted tv-ov-nodata', hidden: true }, t(`noData.${name}`));
    return h('div', { class: `tv-chart-area tv-ov-chartbox ${cls || ''}`.trim() }, hosts[name], notes[name]);
  };

  const granButtons = ['day', 'month'].map((g) =>
    h(
      'button',
      {
        type: 'button',
        dataset: { granularity: g },
        'aria-pressed': 'false',
        on: { click: () => setGranularity(g) },
      },
      t(`trend.${g}`),
    ),
  );
  const granSeg = h('div', { class: 'tv-seg tv-ov-gran', role: 'group', 'aria-label': t('trend.granularity'), hidden: true }, granButtons);
  const trendMeta = h('span', { class: 'tv-ov-meta' });
  const cacheMeta = h('span', { class: 'tv-ov-meta' });

  const recentBody = h('tbody');
  const recentNone = h('p', { class: 'tv-muted', hidden: true }, t('recent.none'));
  const recentTable = h(
    'table',
    { class: 'tv-table tv-ovrecent' },
    h('colgroup', null, ['title', 'project', 'employee', 'tokens', 'cost', 'started'].map((c) => h('col', { class: `tv-ovrecent__col--${c}` }))),
    h(
      'thead',
      null,
      h(
        'tr',
        null,
        h('th', { scope: 'col' }, t('recent.title')),
        h('th', { scope: 'col' }, t('recent.project')),
        h('th', { scope: 'col' }, t('recent.employee')),
        h('th', { scope: 'col', class: 'tv-num' }, t('recent.tokens')),
        h('th', { scope: 'col', class: 'tv-num', title: t('costNote') }, t('recent.cost')),
        h('th', { scope: 'col' }, t('recent.started')),
      ),
    ),
    recentBody,
  );

  const panel = (heading, cls, ...children) => h('tv-panel', { heading, class: `tv-ov-panel ${cls}` }, ...children);
  const grid = h(
    'div',
    { class: 'tv-ov-grid' },
    panel(
      t('trend.title'),
      'tv-ov-panel--trend',
      h('div', { slot: 'actions' }, granSeg),
      h('div', { class: 'tv-meta' }, h('span', null, t('trend.sub')), trendMeta),
      box('daily'),
    ),
    panel(t('cache.title'), 'tv-ov-panel--cache', h('div', { class: 'tv-meta' }, h('span', null, t('cache.sub')), cacheMeta), box('cache')),
    panel(t('projects.title'), 'tv-ov-panel--projects', box('projects', 'tv-ov-chartbox--tall')),
    panel(t('models.title'), 'tv-ov-panel--models', box('models', 'tv-ov-chartbox--tall tv-ov-chartbox--plain')),
    panel(t('tools.title'), 'tv-ov-panel--tools', box('tools', 'tv-ov-chartbox--tall')),
    panel(
      t('recent.heading'),
      'tv-ov-panel--recent',
      h('a', { slot: 'actions', class: 'tv-ov-seeall', href: '#/sessions' }, t('recent.all')),
      h('div', { class: 'tv-ov-tablewrap' }, recentTable),
      recentNone,
    ),
  );

  const root = h(
    'div',
    { class: 'tv-overview', dataset: { tvRev: '0' } },
    h(
      'tv-panel',
      { heading: t('title'), class: 'tv-ov-head' },
      exportBtn, bar.clear, bar.el,
      totalsStrip,
      h('div', { class: 'tv-ov-costrow' }, h('p', { class: 'tv-costnote', 'data-cost-note': '' }, t('costNote')), costInfo),
    ),
    error.el,
    empty.el,
    grid,
  );
  container.append(root);

  // ---------- updates ----------

  function setTotal(key, compact, full) {
    totals[key].textContent = compact;
    totals[key].title = full;
  }

  function updateTotals(ov) {
    const s = ov.totals || {};
    const write = (Number(s.cache_create_5m_tokens) || 0) + (Number(s.cache_create_1h_tokens) || 0);
    const tok = (v) => [fmtTokens(v), `${fmtInt(v)} ${t('tokensUnit')}`];
    setTotal('tokens', ...tok(s.total_tokens));
    setTotal('input', ...tok(s.input_tokens));
    setTotal('output', ...tok(s.output_tokens));
    setTotal('cache_read', ...tok(s.cache_read_tokens));
    setTotal('cache_write', ...tok(write));
    setTotal('sessions', fmtInt(s.sessions), fmtInt(s.sessions));
    setTotal('events', fmtInt(s.events), fmtInt(s.events));
    const cost = costCell(s.cost, s.cost_partial, unknown);
    setTotal('cost', cost.text, s.cost_partial ? t('costPartialHint') : t('costNote'));
    totals.cost.className = `tv-ovtotals__value ${cost.known ? 'tv-num--orange' : 'tv-ov-unknown'}`;
    const models = (ov.unknown_cost_models || []).map((m) => String(m || t('filter.noModel'))).join(', ');
    let info = '';
    if (models && !cost.known) info = t('costUnknown', { models });
    else if (models) info = t('costPartial', { models });
    costInfo.textContent = info;
    costInfo.hidden = !info;
  }

  function updateRecent(ov) {
    const list = ov.recent_sessions || [];
    rememberSessions(list);
    recentNone.hidden = list.length > 0;
    recentTable.hidden = !list.length;
    recentBody.replaceChildren(
      ...list.map((s) => {
        const title = s.title ? String(s.title) : t('recent.untitled');
        const cost = costCell(s.cost, s.cost_partial, unknown);
        return h(
          'tr',
          { dataset: { session: String(s.session_id ?? '') } },
          h('td', { dataset: { col: 'title' } }, h('a', { class: 'tv-ov-link', href: sessionHref(s.session_id) }, clipText('span', s.title ? '' : 'tv-ov-untitled', title))),
          h('td', { dataset: { col: 'project' } }, swatchName(s.project, s.project || t('noProject'))),
          h('td', { dataset: { col: 'employee' } }, clipText('span', '', s.display_name || s.employee_key || '')),
          h('td', { class: 'tv-num', title: fmtInt(s.total_tokens) }, fmtTokens(s.total_tokens)),
          h('td', { class: `tv-num ${cost.cls}`, dataset: { col: 'cost' } }, cost.text),
          h('td', { class: 'tv-ov-time' }, shortTime(s.started_at)),
        );
      }),
    );
  }

  function trendMode() {
    const range = ovRange(ctx.filters.get().range);
    if (range === 'today') return 'hour';
    const long = rangeDays(range) > 90;
    granSeg.hidden = !long;
    if (!long) return 'day';
    return granularity || 'month';
  }

  function setGranularity(g) {
    granularity = g;
    if (!last) return;
    drawCharts(last, paintSeq).then((painted) => {
      if (painted) bump();
    });
  }

  // Paints only if no newer payload started painting meanwhile (seq) and the route is alive.
  async function drawCharts(ov, seq) {
    const echarts = await charts;
    if (!echarts || ctx.signal.aborted || seq !== paintSeq) return false;
    const opts = { t, signal: ctx.signal };
    const mode = trendMode();
    if (mode === 'hour') granSeg.hidden = true;
    for (const b of granButtons) b.setAttribute('aria-pressed', String(b.dataset.granularity === mode));
    const trend = drawTrend(hosts.daily, ov, { ...opts, mode });
    trendMeta.textContent = trend.peak ? t('trend.peak', { value: fmtTokens(trend.peak.value), when: trend.peak.label }) : '';
    trendMeta.hidden = !trend.peak;
    notes.daily.textContent = t(mode === 'day' && rangeDays(ctx.filters.get().range) > 90 ? 'trend.last90' : 'noData.daily');
    notes.daily.hidden = trend.drawn && !(mode === 'day' && rangeDays(ctx.filters.get().range) > 90);
    hosts.daily.hidden = !trend.drawn;
    const cache = drawCache(hosts.cache, ov, { ...opts, hourly: mode === 'hour' });
    cacheMeta.textContent = cache.ratio === null ? '' : t('cache.ratio', { pct: Math.round(cache.ratio * 100) });
    show('cache', cache.drawn);
    show('projects', drawProjects(hosts.projects, ov, opts));
    show('models', drawModels(hosts.models, ov, opts));
    show('tools', drawTools(hosts.tools, ov, opts));
    return true;
  }

  function show(name, drawn) {
    hosts[name].hidden = !drawn;
    notes[name].hidden = drawn;
  }

  function bump() {
    if (ctx.signal.aborted) return;
    rev += 1;
    root.dataset.tvRev = String(rev);
  }

  async function draw() {
    const state = ctx.filters.get();
    // Live refreshes are always fresh; filter changes may join the filter bar's call.
    const maxAge = fresh ? 0 : FILTER_SHARE_MS;
    fresh = false;
    try {
      const ov = await cachedCall(ctx.api, 'overview', [ovFilter(state)], { maxAge });
      if (ctx.signal.aborted) return;
      paintSeq += 1;
      const seq = paintSeq;
      if (!ov || typeof ov !== 'object') throw new Error(t('badResponse'));
      error.hide();
      last = ov;
      updateTotals(ov);
      const s = ov.totals || {};
      const isEmpty = !(Number(s.total_tokens) > 0) && !(Number(s.sessions) > 0) && !(ov.recent_sessions || []).length;
      empty.set(isEmpty, {
        hintText: hasNarrowing(state) ? t('empty.hint') : t('empty.hintRange'),
        showAction: hasNarrowing(state),
      });
      grid.hidden = isEmpty;
      if (!isEmpty) {
        updateRecent(ov);
        await drawCharts(ov, seq);
      }
    } catch (err) {
      if (ctx.signal.aborted) return;
      console.error('[overview] load failed', err);
      error.show(err);
    }
    bump();
  }

  const refresh = coalesce(draw);
  ctx.filters.subscribe(() => {
    granularity = null;
    refresh();
  });
  ctx.onLive(
    debounce(
      () => {
        fresh = true;
        refresh();
      },
      LIVE_DEBOUNCE_MS,
      ctx.signal,
    ),
  );
  await refresh();
}

export function destroy() {
  disposeDetached();
}
