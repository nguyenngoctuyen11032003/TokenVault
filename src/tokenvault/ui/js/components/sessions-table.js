// Sessions table for #/sessions (Task 15): local search (accent-insensitive), sortable columns
// and incremental rendering (PAGE rows at a time + "load more"), so thousands of SessionRows
// never become thousands of DOM rows at once.
//   const table = createSessionsTable({ t, unknownText, noModel });
//   table.setRows(rows);          // keeps search, sort and how many rows are shown
//   table.setSearch('text');
// t is scoped to the `sessions.*` namespace.

import { clipText, fmtInt, fmtTokens, fold, h, shortTime } from './homekit.js';
import { costCell, modelLabel, sessionHref, swatchName } from './overview-kit.js';

export const PAGE = 200;
const COLUMNS = ['title', 'project', 'employee', 'model', 'tokens', 'cost', 'started'];
const NUMERIC = new Set(['tokens', 'cost']);
const SORTABLE = new Set(['title', 'project', 'employee', 'tokens', 'cost', 'started']);

function sortValue(row, key) {
  switch (key) {
    case 'title':
      return fold(row.title || '');
    case 'project':
      return fold(row.project || '');
    case 'employee':
      return fold(row.display_name || row.employee_key || '');
    case 'tokens':
      return Number(row.total_tokens) || 0;
    case 'cost':
      return row.cost === null || row.cost === undefined ? -1 : Number(row.cost) || 0;
    default:
      return String(row.started_at || '');
  }
}

export function createSessionsTable({ t, unknownText, noModel }) {
  let all = [];
  let view = [];
  let shown = PAGE;
  let query = '';
  let sort = { key: 'started', dir: -1 };

  const headCells = COLUMNS.map((col) => {
    const label = t(`col.${col}`);
    const th = h('th', {
      scope: 'col',
      class: NUMERIC.has(col) ? 'tv-num' : null,
      dataset: SORTABLE.has(col) ? { sort: col } : {},
      title: col === 'cost' ? t('costNote') : null,
    });
    if (SORTABLE.has(col)) {
      th.append(
        h('button', { type: 'button', class: 'tv-emptable__sort', on: { click: () => toggleSort(col) } }, label),
      );
    } else {
      th.textContent = label;
    }
    return th;
  });
  const body = h('tbody');
  const table = h(
    'table',
    { class: 'tv-table tv-emptable tv-sesstable' },
    h('colgroup', null, COLUMNS.map((c) => h('col', { class: `tv-sesstable__col--${c}` }))),
    h('thead', null, h('tr', null, headCells)),
    body,
  );
  const more = h('button', { type: 'button', class: 'tv-btn tv-sessions-more', 'data-load-more': '', hidden: true, on: { click: () => showMore() } });
  const count = h('span', { class: 'tv-sessions-count', role: 'status' });
  const noMatch = h('p', { class: 'tv-muted tv-sessions-nomatch', hidden: true }, t('noMatch'));

  function row(s) {
    const title = s.title ? String(s.title) : t('untitled');
    const cost = costCell(s.cost, s.cost_partial, unknownText);
    const models = (s.models || []).map((m) => modelLabel(m, noModel)).join(', ');
    const side = Number(s.sidechain_tokens) > 0;
    return h(
      'tr',
      { dataset: { session: String(s.session_id ?? ''), tokens: String(Number(s.total_tokens) || 0) } },
      h(
        'td',
        { dataset: { col: 'title' } },
        h('a', { class: 'tv-ov-link', href: sessionHref(s.session_id) }, clipText('span', s.title ? '' : 'tv-ov-untitled', title)),
      ),
      h('td', { dataset: { col: 'project' } }, swatchName(s.project, s.project || t('noProject'))),
      h('td', { dataset: { col: 'employee' } }, clipText('span', '', s.display_name || s.employee_key || '')),
      h('td', { dataset: { col: 'model' } }, clipText('span', 'tv-sessions-model', models || noModel)),
      h(
        'td',
        { class: 'tv-num', dataset: { col: 'tokens' }, title: side ? t('sidechainTokens', { tokens: fmtInt(s.sidechain_tokens) }) : fmtInt(s.total_tokens) },
        side ? h('i', { class: 'tv-sessions-side', 'aria-hidden': 'true' }) : null,
        fmtTokens(s.total_tokens),
      ),
      h('td', { class: `tv-num ${cost.cls}`, dataset: { col: 'cost' } }, cost.text),
      h('td', { class: 'tv-ov-time', dataset: { col: 'started' } }, shortTime(s.started_at)),
    );
  }

  function compute() {
    const q = fold(query.trim());
    const words = q ? q.split(/\s+/) : [];
    const matches = words.length
      ? all.filter((s) => {
          const hay = fold(
            [s.title, s.project, s.display_name, s.employee_key, (s.models || []).join(' '), s.session_id].join(' '),
          );
          return words.every((w) => hay.includes(w));
        })
      : [...all];
    const { key, dir } = sort;
    matches.sort((a, b) => {
      const va = sortValue(a, key);
      const vb = sortValue(b, key);
      if (va < vb) return -dir;
      if (va > vb) return dir;
      return String(b.started_at || '').localeCompare(String(a.started_at || ''));
    });
    view = matches;
  }

  function paint() {
    const n = Math.min(shown, view.length);
    body.replaceChildren(...view.slice(0, n).map(row));
    footer();
    for (const th of headCells) {
      if (th.dataset.sort === sort.key) th.setAttribute('aria-sort', sort.dir < 0 ? 'descending' : 'ascending');
      else th.removeAttribute('aria-sort');
    }
  }

  function footer() {
    const n = Math.min(shown, view.length);
    count.textContent = t('count', { shown: fmtInt(n), total: fmtInt(view.length) });
    noMatch.hidden = !(all.length && !view.length);
    more.hidden = n >= view.length;
    more.textContent = t('loadMore', { n: fmtInt(Math.min(PAGE, view.length - n)) });
  }

  function showMore() {
    const from = Math.min(shown, view.length);
    shown += PAGE;
    body.append(...view.slice(from, Math.min(shown, view.length)).map(row));
    footer();
  }

  function toggleSort(key) {
    sort = sort.key === key ? { key, dir: -sort.dir } : { key, dir: NUMERIC.has(key) || key === 'started' ? -1 : 1 };
    compute();
    paint();
  }

  return {
    table,
    more,
    count,
    noMatch,
    setRows(rows) {
      all = Array.isArray(rows) ? rows.filter((r) => r && typeof r === 'object') : [];
      compute();
      paint();
    },
    setSearch(text) {
      query = String(text ?? '');
      shown = PAGE;
      compute();
      paint();
    },
    total: () => all.length,
  };
}
