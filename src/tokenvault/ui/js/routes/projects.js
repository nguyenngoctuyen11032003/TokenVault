// #/projects — every project in the filtered range: bar chart of tokens + table (tokens, share
// bar in the project's palette colour, API-equivalent cost, sessions, employees, last used).
// Uses the shared filters except `project` itself (this is the list of projects; the selected
// one is highlighted). Clicking a project sets the project filter and opens #/sessions.

import { barChart, dispose, disposeDetached, loadECharts } from '../charts.js';
import { createFilterBar } from '../components/overview-filterbar.js';
import {
  CLEAR_PATCH,
  costCell,
  emptyState,
  errorBanner,
  hasNarrowing,
  ovFilter,
  projectKey,
  swatchName,
} from '../components/overview-kit.js';
import {
  coalesce,
  debounce,
  fmtInt,
  fmtPct,
  fmtTokens,
  h,
  LIVE_DEBOUNCE_MS,
  shortTime,
} from '../components/homekit.js';
import { colorFor } from '../palette.js';

const CHART_TOP = 12;
const COLUMNS = ['project', 'tokens', 'share', 'cost', 'sessions', 'employees', 'last'];

export default async function render(container, ctx) {
  const t = (key, params) => ctx.t(`projects.${key}`, params);
  await Promise.all([
    ctx.useStylesheet('css/pages/people.css'),
    ctx.useStylesheet('css/pages/overview-shared.css'),
    ctx.useStylesheet('css/pages/projects.css'),
  ]);
  if (ctx.signal.aborted) return;
  const bar = await createFilterBar(ctx);
  if (ctx.signal.aborted) return;
  const charts = loadECharts().catch((err) => {
    console.error('[projects] charts unavailable', err);
    return null;
  });

  const unknown = t('unknownCost');
  let rev = 0;

  const error = errorBanner((detail) => t('loadError', { detail }), t('retry'), () => refresh());
  const empty = emptyState({
    lead: t('empty.lead'),
    hint: t('empty.hint'),
    action: t('empty.clear'),
    onAction: () => ctx.filters.set({ ...CLEAR_PATCH }),
  });
  const chartHost = h('div', { class: 'tv-proj-chart', dataset: { chart: 'projects' } });
  const chartBox = h('div', { class: 'tv-chart-area tv-proj-chartbox' }, chartHost);
  const summary = h('span', { class: 'tv-proj-summary' });
  const body = h('tbody');
  const table = h(
    'table',
    { class: 'tv-table tv-projtable' },
    h('colgroup', null, COLUMNS.map((c) => h('col', { class: `tv-projtable__col--${c}` }))),
    h(
      'thead',
      null,
      h(
        'tr',
        null,
        COLUMNS.map((c) =>
          h(
            'th',
            {
              scope: 'col',
              class: ['project', 'share', 'last'].includes(c) ? null : 'tv-num',
              title: c === 'cost' ? t('costNote') : null,
            },
            t(`col.${c}`),
          ),
        ),
      ),
    ),
    body,
  );

  const content = h(
    'div',
    { class: 'tv-proj-content' },
    h('tv-panel', { heading: t('chartTitle'), class: 'tv-proj-chartpanel' }, h('div', { class: 'tv-meta' }, h('span', null, t('chartSub')), summary), chartBox),
    h(
      'tv-panel',
      { heading: t('tableTitle'), class: 'tv-proj-tablepanel' },
      h('div', { class: 'tv-proj-tablewrap' }, table),
      h('p', { class: 'tv-costnote', 'data-cost-note': '' }, t('costNote')),
    ),
  );
  const root = h(
    'div',
    { class: 'tv-projects', dataset: { tvRev: '0' } },
    h('tv-panel', { heading: t('title'), class: 'tv-proj-head' }, bar.clear, bar.el),
    error.el,
    empty.el,
    content,
  );
  container.append(root);

  // A missing project is "" on the server; the store keeps the NO_PROJECT sentinel instead
  // (an empty string would mean "no filter"); ovFilter() sends "" back to the API.
  function openProject(project) {
    ctx.filters.set({ project: projectKey(project) });
    ctx.navigate('sessions');
  }

  function row(p, total, selected) {
    const project = String(p.project ?? '');
    const name = project || t('noProject');
    const share = total ? Number(p.total_tokens) / total : 0;
    const color = colorFor(project);
    const cost = costCell(p.cost, p.cost_partial, unknown);
    const unpriced = (p.unknown_cost_models || []).join(', ');
    const link = h(
      'a',
      {
        class: 'tv-proj-link',
        href: '#/sessions',
        title: name,
        on: {
          click: (ev) => {
            ev.preventDefault();
            openProject(project);
          },
        },
      },
      swatchName(project, name),
    );
    return h(
      'tr',
      {
        dataset: { project, tokens: String(Number(p.total_tokens) || 0) },
        'aria-selected': selected === projectKey(project) ? 'true' : null,
        on: {
          click: (ev) => {
            if (!ev.target.closest('a')) openProject(project);
          },
        },
      },
      h('td', { dataset: { col: 'project' } }, link),
      h('td', { class: 'tv-num', dataset: { col: 'tokens' }, title: `${fmtInt(p.total_tokens)} ${t('tokensUnit')}` }, fmtTokens(p.total_tokens)),
      h(
        'td',
        { class: 'tv-proj-share', dataset: { col: 'share' } },
        h('span', { class: 'tv-bar' }, h('i', { style: { width: `${Math.max(1, share * 100)}%`, background: color, color } })),
        h('span', { class: 'tv-proj-pct' }, fmtPct(share)),
      ),
      h(
        'td',
        { class: `tv-num ${cost.cls}`, dataset: { col: 'cost' }, title: unpriced ? t('costPartial', { models: unpriced }) : null },
        cost.text,
      ),
      h('td', { class: 'tv-num', dataset: { col: 'sessions' } }, fmtInt(p.sessions)),
      h('td', { class: 'tv-num', dataset: { col: 'employees' } }, fmtInt(p.employees)),
      h('td', { class: 'tv-proj-time', dataset: { col: 'last' } }, p.last_active ? shortTime(p.last_active) : '—'),
    );
  }

  async function drawChart(list) {
    const echarts = await charts;
    if (!echarts || ctx.signal.aborted) return;
    const top = list.slice(0, CHART_TOP);
    if (!top.length) {
      dispose(chartHost);
      return;
    }
    chartBox.style.height = `${Math.max(140, top.length * 30 + 30)}px`;
    barChart(
      chartHost,
      {
        categories: top.map((p) => String(p.project || '') || t('noProject')),
        keys: top.map((p) => String(p.project || '')),
        values: top.map((p) => Number(p.total_tokens) || 0),
      },
      { label: t('chartLabel'), unit: t('tokensUnit'), seriesName: t('col.tokens'), signal: ctx.signal, horizontal: true },
    );
  }

  async function draw() {
    const state = ctx.filters.get();
    try {
      const rows = await ctx.api.projects(ovFilter(state, ['project']));
      if (ctx.signal.aborted) return;
      if (!Array.isArray(rows)) throw new Error(t('badResponse'));
      error.hide();
      const list = rows
        .filter((p) => p && typeof p === 'object')
        .sort((a, b) => (Number(b.total_tokens) || 0) - (Number(a.total_tokens) || 0));
      const total = list.reduce((a, p) => a + (Number(p.total_tokens) || 0), 0);
      const isEmpty = !list.length;
      empty.set(isEmpty, { hintText: hasNarrowing(state) ? t('empty.hint') : t('empty.hintRange'), showAction: hasNarrowing(state) });
      content.hidden = isEmpty;
      summary.textContent = t('summary', { count: fmtInt(list.length), tokens: fmtTokens(total) });
      body.replaceChildren(...list.map((p) => row(p, total, state.project ?? null)));
      if (!isEmpty) await drawChart(list);
    } catch (err) {
      if (ctx.signal.aborted) return;
      console.error('[projects] load failed', err);
      error.show(err);
    }
    if (ctx.signal.aborted) return;
    rev += 1;
    root.dataset.tvRev = String(rev);
  }

  const refresh = coalesce(draw);
  ctx.filters.subscribe(() => refresh());
  ctx.onLive(debounce(() => refresh(), LIVE_DEBOUNCE_MS, ctx.signal));
  await refresh();
}

export function destroy() {
  disposeDetached();
}
