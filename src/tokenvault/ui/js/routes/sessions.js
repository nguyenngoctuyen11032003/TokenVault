// #/sessions — every session matching the shared filters (team, employee, project, model,
// range), with local search, sortable columns and "load more" paging (200 rows at a time; a
// range can hold thousands of sessions). Columns: title, project, employee, model(s), tokens,
// API-equivalent cost (UNKNOWN / "+"), started. A row opens #/session/<encoded id>.

import { createFilterBar } from '../components/overview-filterbar.js';
import {
  CLEAR_PATCH,
  emptyState,
  errorBanner,
  hasNarrowing,
  ovFilter,
  rememberSessions,
} from '../components/overview-kit.js';
import { createSessionsTable } from '../components/sessions-table.js';
import { coalesce, debounce, h, LIVE_DEBOUNCE_MS } from '../components/homekit.js';

export default async function render(container, ctx) {
  const t = (key, params) => ctx.t(`sessions.${key}`, params);
  await Promise.all([
    ctx.useStylesheet('css/pages/people.css'),
    ctx.useStylesheet('css/pages/overview-shared.css'),
    ctx.useStylesheet('css/pages/sessions.css'),
  ]);
  if (ctx.signal.aborted) return;
  const bar = await createFilterBar(ctx);
  if (ctx.signal.aborted) return;

  let rev = 0;
  const error = errorBanner((detail) => t('loadError', { detail }), t('retry'), () => refresh());
  const empty = emptyState({
    lead: t('empty.lead'),
    hint: t('empty.hint'),
    action: t('empty.clear'),
    onAction: () => ctx.filters.set({ ...CLEAR_PATCH }),
  });
  const table = createSessionsTable({ t, unknownText: t('unknownCost'), noModel: t('noModel') });
  const search = h('input', {
    type: 'search',
    class: 'tv-sessions-search',
    placeholder: t('search'),
    'aria-label': t('search'),
    autocomplete: 'off',
    spellcheck: 'false',
    on: { input: () => table.setSearch(search.value) },
  });
  table.table.addEventListener('click', (ev) => {
    const tr = ev.target.closest('tr[data-session]');
    if (!tr || ev.target.closest('a, button')) return;
    ctx.navigate('session', tr.dataset.session);
  });

  const listPanel = h(
    'tv-panel',
    { heading: t('listTitle'), class: 'tv-sessions-panel' },
    h('div', { class: 'tv-sessions-toolbar' }, h('label', { class: 'tv-sessions-field' }, search), table.count),
    h('div', { class: 'tv-sessions-tablewrap' }, table.table),
    table.noMatch,
    h('div', { class: 'tv-sessions-foot' }, table.more, h('p', { class: 'tv-costnote', 'data-cost-note': '' }, t('costNote'))),
  );
  const root = h(
    'div',
    { class: 'tv-sessions', dataset: { tvRev: '0' } },
    h('tv-panel', { heading: t('title'), class: 'tv-sessions-head' }, bar.clear, bar.el),
    error.el,
    empty.el,
    listPanel,
  );
  container.append(root);

  async function draw() {
    const state = ctx.filters.get();
    try {
      const rows = await ctx.api.sessions(ovFilter(state));
      if (ctx.signal.aborted) return;
      if (!Array.isArray(rows)) throw new Error(t('badResponse'));
      error.hide();
      rememberSessions(rows);
      const isEmpty = rows.length === 0;
      empty.set(isEmpty, { hintText: hasNarrowing(state) ? t('empty.hint') : t('empty.hintRange'), showAction: hasNarrowing(state) });
      listPanel.hidden = isEmpty;
      table.setRows(rows);
    } catch (err) {
      if (ctx.signal.aborted) return;
      console.error('[sessions] load failed', err);
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
