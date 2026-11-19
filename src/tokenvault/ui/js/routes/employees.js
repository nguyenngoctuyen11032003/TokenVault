// #/employees — every employee (account) with search, team filter, range and sortable columns.
// Clicking a row opens #/employee/<encoded key>. Team and range are shared filters
// (ctx.filters.team / ctx.filters.range); the search text is local to the page.
// Live events re-fetch and update the rows in place.

import { createEmployeeTable } from '../components/employeetable.js';
import {
  apiFilter,
  coalesce,
  debounce,
  deriveTeams,
  fold,
  h,
  LIVE_DEBOUNCE_MS,
  RANGES,
} from '../components/homekit.js';
import { createRangeSwitch } from '../components/rangeswitch.js';
import { createReportButton } from '../components/report-dialog.js';

// filters.range may hold a longer range set on #/overview (90d, all, custom "a..b"): this page
// only has today/7d/30d, so such a range is clamped to 30d with a visible note (never "today").
const isClamped = (range) => !!range && !RANGES.includes(range);
const pageRange = (range) => (RANGES.includes(range) ? range : isClamped(range) ? '30d' : 'today');

export default async function render(container, ctx) {
  const t = (key, params) => ctx.t(`employees.${key}`, params);
  await Promise.all([
    ctx.useStylesheet('css/pages/people.css'),
    ctx.useStylesheet('css/pages/employees.css'),
  ]);
  if (ctx.signal.aborted) return;

  let rows = [];
  let rev = 0;
  let teamsKnown = [];

  const search = h('input', {
    type: 'search',
    class: 'tv-employees-search',
    placeholder: t('search'),
    'aria-label': t('search'),
    autocomplete: 'off',
    spellcheck: 'false',
  });
  const teamSelect = h('select', { class: 'tv-employees-team', dataset: { filter: 'team' }, 'aria-label': t('teamFilter') });
  const range = createRangeSwitch({
    t,
    value: pageRange(ctx.filters.get().range),
    label: t('rangeLabel'),
    onChange: (value) => ctx.filters.set({ range: value }),
  });
  const table = createEmployeeTable({
    t,
    columns: ['account', 'team', 'devices', 'tokens', 'share', 'sessions', 'cost', 'lastSeen'],
    sortable: true,
    selectable: true,
    onOpen: (key) => ctx.navigate('employee', key),
    onSelect: (keys, shown) => syncSelected(keys.length, shown),
  });
  const exportAll = await createReportButton({
    api: ctx.api,
    signal: ctx.signal,
    attrs: { 'data-report-open': 'all' },
    preset: () => {
      const team = ctx.filters.get().team;
      return { scope: team ? 'team' : 'all', team: team ?? undefined, range: ctx.filters.get().range };
    },
  });
  const exportSelected = await createReportButton({
    api: ctx.api,
    signal: ctx.signal,
    attrs: { 'data-report-open': 'selected', disabled: true },
    labelKey: 'buttonSelected',
    labelParams: { n: 0 },
    preset: () => ({ scope: 'employee', employee_keys: table.selected(), range: ctx.filters.get().range }),
  });
  function syncSelected(n, shown = n) {
    exportSelected.disabled = n === 0;
    const hidden = n - shown;
    exportSelected.textContent = ctx.t(hidden > 0 ? 'report.buttonSelectedHidden' : 'report.buttonSelected', { n, hidden });
  }
  const count = h('span', { class: 'tv-employees-count' });
  const rangeNote = h('p', { class: 'tv-costnote', 'data-range-note': '', hidden: true }, t('rangeClamped'));
  const noMatch = h('p', { class: 'tv-muted tv-employees-nomatch', hidden: true }, t('noMatch'));
  const errorBox = h('div', { class: 'tv-loaderror', role: 'alert', 'data-load-error': '', hidden: true }, h('span'), h('button', { type: 'button', class: 'tv-btn', on: { click: () => refresh() } }, t('retry')));

  const root = h(
    'div',
    { class: 'tv-employees', dataset: { tvRev: '0' } },
    errorBox,
    h(
      'tv-panel',
      { heading: t('title'), class: 'tv-employees-panel' },
      h(
        'div',
        { class: 'tv-employees-toolbar' },
        h('label', { class: 'tv-employees-field tv-employees-field--search' }, search),
        h('label', { class: 'tv-employees-field' }, h('span', null, t('teamFilter')), teamSelect),
        range.el,
        count,
        h('span', { class: 'tv-employees-export' }, exportAll, exportSelected),
      ),
      rangeNote,
      h('div', { class: 'tv-employees-tablewrap' }, table.el),
      noMatch,
      h('p', { class: 'tv-costnote', 'data-cost-note': '' }, t('costNote')),
    ),
  );
  container.append(root);

  function fillTeams(teams) {
    const names = (Array.isArray(teams) ? teams : []).map((x) => String(x?.name ?? '')).filter(Boolean);
    const current = ctx.filters.get().team ?? '';
    if (current && !names.includes(current)) names.push(current);
    if (names.join('\n') === teamsKnown.join('\n') && teamSelect.value === current) return;
    teamsKnown = names;
    teamSelect.replaceChildren(
      h('option', { value: '' }, t('allTeams')),
      ...names.map((name) => h('option', { value: name }, name)),
    );
    teamSelect.value = current;
  }

  function applySearch() {
    const q = fold(search.value.trim());
    const shown = q
      ? rows.filter((r) => fold(`${r.display_name} ${r.email ?? ''} ${r.key} ${(r.teams || []).join(' ')}`).includes(q))
      : rows;
    table.update(shown, rows);
    noMatch.hidden = shown.length > 0;
    count.textContent = t('count', { shown: shown.length, total: rows.length });
  }

  async function draw() {
    const state = ctx.filters.get();
    try {
      const [teams, list] = await Promise.all([
        // teams() is optional: without it the team filter is derived from the employee rows.
        ctx.api.teams().catch((err) => {
          console.warn('[employees] teams() failed, deriving teams from employees', err);
          return null;
        }),
        ctx.api.employees(apiFilter({ team: state.team ?? null }, pageRange(state.range))),
      ]);
      if (ctx.signal.aborted) return;
      rows = Array.isArray(list) ? list : [];
      fillTeams(Array.isArray(teams) ? teams : deriveTeams(rows));
      range.set(pageRange(ctx.filters.get().range));
      rangeNote.hidden = !isClamped(ctx.filters.get().range);
      errorBox.hidden = true;
      applySearch();
    } catch (err) {
      if (ctx.signal.aborted) return;
      console.error('[employees] load failed', err);
      errorBox.firstChild.textContent = t('loadError', { detail: err?.message || String(err) });
      errorBox.hidden = false;
    }
    if (ctx.signal.aborted) return;
    rev += 1;
    root.dataset.tvRev = String(rev);
  }

  const refresh = coalesce(draw);
  search.addEventListener('input', applySearch);
  teamSelect.addEventListener('change', () => ctx.filters.set({ team: teamSelect.value || null }));
  ctx.filters.subscribe((_state, changed) => {
    if (changed.includes('team') || changed.includes('range')) refresh();
  });
  ctx.onLive(debounce(() => refresh(), LIVE_DEBOUNCE_MS, ctx.signal));
  await refresh();
}
