// Global filter bar for the overview / projects / sessions pages (Task 15).
//   const bar = await createFilterBar(ctx);   // ctx = the route ctx
//   panel.append(bar.el, bar.clear);  // `clear` carries slot="actions" (tv-panel tab row)
// Team, employee, project, model selects + range switch (today / 7d / 30d / 90d / all) and a
// custom from/to date pair. Every change goes to the shared ctx.filters store (persisted), so
// the choice follows the user to the other pages and across restarts. Options come from
// api.teams(), api.employees(filter) and api.overview(filter without project/model) through the
// shared call cache (overview-fetch.js: one call per filter, refreshed at most every 30 s); a value that
// is selected but not in the lists (e.g. no data in this range) is still shown. Strings live
// under `overview.filter.*` (this component's owner); they are loaded here so the bar works on
// any page.

import { loadI18nExtension } from '../i18n.js';
import { today } from '../overview-clock.js';
import { coalesce, debounce, h, LIVE_DEBOUNCE_MS } from './homekit.js';
import { cachedCall } from './overview-fetch.js';
import {
  CLEAR_PATCH,
  customRange,
  makeCustomRange,
  modelLabel,
  NO_PROJECT,
  OV_RANGES,
  ovFilter,
  ovRange,
  projectKey,
} from './overview-kit.js';

const FIELDS = ['team', 'email', 'project', 'model'];

export async function createFilterBar(ctx) {
  await loadI18nExtension('overview');
  const t = (key, params) => ctx.t(`overview.filter.${key}`, params);
  const noModel = t('noModel');
  const lists = { team: [], email: [], project: [], model: [] };

  const selects = {};
  const fields = FIELDS.map((name) => {
    const select = h('select', {
      class: 'tv-ovfilter__select',
      dataset: { filter: name },
      'aria-label': t(`${name}.label`),
      on: { change: () => ctx.filters.set({ [name]: select.value || null }) },
    });
    selects[name] = select;
    return h(
      'label',
      { class: `tv-ovfilter__field tv-ovfilter__field--${name}` },
      h('span', { class: 'tv-ovfilter__label' }, t(`${name}.label`)),
      select,
    );
  });

  const rangeButtons = OV_RANGES.map((range) =>
    h(
      'button',
      {
        type: 'button',
        dataset: { range },
        'aria-pressed': 'false',
        on: { click: () => ctx.filters.set({ range }) },
      },
      t(`range.${range}`),
    ),
  );
  const rangeSeg = h('div', { class: 'tv-seg tv-ovfilter-range', role: 'group', 'aria-label': t('range.label') }, rangeButtons);

  const dateInput = (name) =>
    h('input', {
      type: 'date',
      class: 'tv-ovfilter__date',
      dataset: { filter: name },
      max: today(),
      'aria-label': t(`range.${name}`),
      on: { change: () => applyCustom() },
    });
  const startInput = dateInput('start');
  const endInput = dateInput('end');
  const custom = h(
    'div',
    { class: 'tv-ovfilter__custom' },
    h('span', { class: 'tv-ovfilter__label' }, t('range.from')),
    startInput,
    h('span', { class: 'tv-ovfilter__label' }, t('range.to')),
    endInput,
  );

  const clear = h(
    'button',
    {
      type: 'button',
      class: 'tv-btn tv-ovfilter__clear',
      'data-filter-clear': '',
      slot: 'actions',
      on: { click: () => ctx.filters.set({ ...CLEAR_PATCH }) },
    },
    t('clear'),
  );

  const el = h(
    'div',
    { class: 'tv-ovfilter', role: 'search', 'aria-label': t('label') },
    h('div', { class: 'tv-ovfilter__row' }, fields),
    h('div', { class: 'tv-ovfilter__row tv-ovfilter__row--range' }, rangeSeg, custom),
  );

  function applyCustom() {
    const start = startInput.value;
    const end = endInput.value;
    if (!start || !end) return;
    const range = makeCustomRange(start, end);
    if (range) ctx.filters.set({ range });
  }

  function labelFor(name, value) {
    if (name === 'model') return modelLabel(value, noModel);
    if (name === 'project') return value === NO_PROJECT || !value ? t('noProject') : value;
    if (name === 'email') return lists.email.find((e) => e.value === value)?.label || value;
    return value;
  }

  function fillSelect(name, state) {
    const select = selects[name];
    const current = state[name] ?? '';
    const items = [...lists[name]];
    if (current && !items.some((i) => i.value === current)) {
      items.unshift({ value: current, label: labelFor(name, current) });
    }
    const options = [h('option', { value: '' }, t(`${name}.all`))];
    for (const item of items) {
      options.push(h('option', { value: item.value, title: item.label }, item.label));
    }
    select.replaceChildren(...options);
    select.value = current;
    select.title = current ? labelFor(name, current) : '';
    select.closest('label').classList.toggle('tv-ovfilter__field--on', !!current);
  }

  function sync(state = ctx.filters.get()) {
    for (const name of FIELDS) fillSelect(name, state);
    const range = ovRange(state.range);
    const cust = customRange(range);
    for (const b of rangeButtons) b.setAttribute('aria-pressed', String(b.dataset.range === range));
    custom.classList.toggle('tv-ovfilter__custom--on', !!cust);
    if (cust) {
      startInput.value = cust.start;
      endInput.value = cust.end;
    }
    clear.disabled = !FIELDS.some((name) => state[name]);
  }

  async function loadOptions() {
    const state = ctx.filters.get();
    const bounds = ovFilter(state);
    const people = { ...bounds, email: null, project: null, model: null };
    // Shared with the page (same filter -> one call) and cached for OPTIONS_TTL.
    const [teams, rows, overview] = await Promise.all([
      cachedCall(ctx.api, 'teams', []).catch((err) => warn('teams', err)),
      cachedCall(ctx.api, 'employees', [people]).catch((err) => warn('employees', err)),
      cachedCall(ctx.api, 'overview', [ovFilter(state, ['project', 'model'])]).catch((err) => warn('overview', err)),
    ]);
    if (ctx.signal.aborted) return;
    if (Array.isArray(teams)) {
      lists.team = teams.map((x) => String(x?.name ?? '')).filter(Boolean).map((v) => ({ value: v, label: v }));
    } else if (Array.isArray(rows)) {
      const names = new Set(rows.flatMap((p) => p?.teams || []));
      lists.team = [...names].map((v) => ({ value: String(v), label: String(v) }));
    }
    if (Array.isArray(rows)) {
      lists.email = rows
        .filter((p) => p && p.key)
        .map((p) => ({ value: String(p.key), label: String(p.email || p.display_name || p.key) }))
        .sort((a, b) => a.label.localeCompare(b.label));
    }
    if (overview && typeof overview === 'object') {
      lists.project = (overview.by_project || [])
        .filter((p) => p && typeof p === 'object')
        .map((p) => projectKey(p.project))
        .map((v) => ({ value: v, label: v === NO_PROJECT ? t('noProject') : v }));
      lists.model = (overview.by_model || [])
        .map((m) => String(m?.model ?? ''))
        .filter(Boolean)
        .map((v) => ({ value: v, label: modelLabel(v, noModel) }));
    }
    sync();
  }

  function warn(what, err) {
    if (!ctx.signal.aborted) console.warn(`[filterbar] ${what}() failed`, err);
    return null;
  }

  const reload = coalesce(loadOptions);
  ctx.filters.subscribe((state, changed) => {
    sync(state);
    if (changed.some((k) => k === 'team' || k === 'email' || k === 'range')) reload();
  });
  // Live data: options follow at most every OPTIONS_TTL (the cache answers in between).
  ctx.onLive(debounce(() => reload(), LIVE_DEBOUNCE_MS, ctx.signal));
  sync();
  reload();
  return { el, clear, sync, reload };
}
