// Employee table (home + employees pages). Rows are EmployeeRow objects from api.employees().
//   const table = createEmployeeTable({ t, columns: ['account', 'tokens', 'share', 'cost'],
//                                       sortable: true, onOpen: (key) => ... });
//   panel.body.append(table.el); table.update(rows);
// Keyed in-place updates: live refreshes change numbers without rebuilding rows, so focus and
// scroll stay put. Each account cell holds a real link (#/employee/<encoded key>) for keyboard
// users; clicking anywhere else on the row opens the same page. All data is set as text.
// `t` is scoped to the calling page: headers `<page>.table.<column>`, `<page>.unknownCost`, ...

import { colorFor } from '../palette.js';
import { clipText, fmtCostPartial, fmtInt, fmtPct, fmtTokens, h, shortTime } from './homekit.js';

export function employeeHref(key) {
  return `#/employee/${encodeURIComponent(String(key ?? ''))}`;
}

const COLUMNS = {
  select: {
    cell(td, row, t, ctx) {
      td.className = 'tv-emptable__select';
      let box = td.firstChild;
      if (!box) {
        box = h('input', { type: 'checkbox', class: 'tv-emptable__check', dataset: { select: '' } });
        td.append(box);
      }
      box.checked = ctx.selected.has(String(row.key));
      box.setAttribute('aria-label', t('table.selectRow', { name: String(row.display_name || row.key || '') }));
    },
  },
  account: {
    sort: (r) => String(r.display_name || r.key || '').toLowerCase(),
    cell(td, row, t) {
      if (!td.firstChild) {
        td.className = 'tv-emptable__account';
        td.append(
          h('span', { class: 'tv-dot', 'aria-hidden': 'true' }),
          h('a', { class: 'tv-emptable__link' }, clipText('span', 'tv-emptable__name', '')),
        );
      }
      const name = String(row.display_name || row.email || row.key || '');
      const [dot, link] = td.children;
      dot.className = `tv-dot${row.online ? ' tv-dot--on' : ''}`;
      link.href = employeeHref(row.key);
      link.setAttribute('aria-label', `${name}, ${t(row.online ? 'status.online' : 'status.offline')}`);
      const span = link.firstChild;
      span.textContent = name;
      span.title = name;
    },
  },
  team: {
    sort: (r) => (r.teams || []).join(', ').toLowerCase(),
    cell(td, row) {
      td.className = 'tv-emptable__team';
      const text = (row.teams || []).join(', ');
      td.replaceChildren(clipText('span', '', text));
    },
  },
  devices: {
    num: true,
    sort: (r) => (r.devices || []).length,
    cell(td, row) {
      td.textContent = String((row.devices || []).length);
    },
  },
  tokens: {
    num: true,
    sort: (r) => Number(r.total_tokens) || 0,
    cell(td, row) {
      td.textContent = fmtTokens(row.total_tokens);
      td.title = fmtInt(row.total_tokens);
    },
  },
  share: {
    sort: (r) => Number(r.share) || 0,
    cell(td, row, t, ctx) {
      if (!td.firstChild) {
        td.className = 'tv-emptable__share';
        td.append(h('span', { class: 'tv-bar' }, h('i')), h('span', { class: 'tv-emptable__pct' }));
      }
      const share = Number(row.share) || 0;
      const bar = td.firstChild;
      const fill = bar.firstChild;
      fill.style.width = `${ctx.maxShare > 0 ? Math.max(2, (share / ctx.maxShare) * 100) : 0}%`;
      fill.style.background = colorFor(row.key);
      bar.setAttribute('role', 'img');
      bar.setAttribute('aria-label', t('table.shareOf', { pct: fmtPct(share) }));
      td.lastChild.textContent = fmtPct(share);
    },
  },
  sessions: {
    num: true,
    sort: (r) => Number(r.sessions) || 0,
    cell(td, row) {
      td.textContent = fmtInt(row.sessions);
    },
  },
  cost: {
    num: true,
    sort: (r) => (r.cost === null || r.cost === undefined ? -1 : Number(r.cost)),
    cell(td, row, t) {
      const text = fmtCostPartial(row.cost, row.cost_partial, t('unknownCost'));
      td.textContent = text;
      td.classList.toggle('tv-num--orange', row.cost !== null && row.cost !== undefined);
      td.classList.toggle('tv-emptable__unknown', row.cost === null || row.cost === undefined);
      td.title = row.cost_partial ? t('costPartialHint') : '';
    },
  },
  lastSeen: {
    sort: (r) => String(r.last_seen || ''),
    cell(td, row) {
      td.className = 'tv-emptable__seen';
      td.textContent = shortTime(row.last_seen);
    },
  },
};

export function createEmployeeTable({ t, columns, sortable = false, onOpen, initialSort, selectable = false, onSelect }) {
  const cols = (selectable ? ['select', ...columns] : columns).filter((id) => COLUMNS[id]);
  const selected = new Set(); // row keys ticked by the user (survives live refreshes)
  let headBox = null;
  let sort = initialSort || { col: 'tokens', dir: 'desc' };
  let data = [];
  const rows = new Map(); // key -> {tr, cells}

  const headCells = cols.map((id) => {
    const def = COLUMNS[id];
    const label = id === 'select' ? '' : t(`table.${id}`);
    const th = h('th', {
      scope: 'col',
      class: def.num ? 'tv-num' : null,
      dataset: { col: id },
      title: id === 'cost' ? t('table.costHint') : null,
    });
    if (id === 'select') {
      headBox = h('input', { type: 'checkbox', class: 'tv-emptable__check', 'aria-label': t('table.selectAll'), dataset: { selectAll: '' } });
      th.append(headBox);
    } else if (sortable) {
      th.append(
        h(
          'button',
          { type: 'button', class: 'tv-emptable__sort', dataset: { sort: id }, on: { click: () => toggle(id) } },
          label,
        ),
      );
    } else {
      th.textContent = label;
    }
    return th;
  });
  const tbody = h('tbody');
  const table = h(
    'table',
    { class: 'tv-table tv-emptable' },
    h('colgroup', null, cols.map((id) => h('col', { class: `tv-emptable__col--${id}` }))),
    h('thead', null, h('tr', null, headCells)),
    tbody,
  );

  function changed() {
    const shown = data.filter((r) => selected.has(String(r.key))).length;
    if (headBox) {
      headBox.checked = shown > 0 && shown === data.length;
      headBox.indeterminate = shown > 0 && shown < data.length;
    }
    onSelect?.([...selected], shown);
  }

  headBox?.addEventListener('change', () => {
    for (const r of data) (headBox.checked ? selected.add(String(r.key)) : selected.delete(String(r.key)));
    render();
    changed();
  });
  tbody.addEventListener('change', (event) => {
    const tr = event.target.closest('tr[data-key]');
    if (!tr || !event.target.matches('input[data-select]')) return;
    (event.target.checked ? selected.add(tr.dataset.key) : selected.delete(tr.dataset.key));
    changed();
  });

  tbody.addEventListener('click', (event) => {
    if (event.target.closest('a, input, .tv-emptable__select')) return; // the link navigates by itself
    const tr = event.target.closest('tr[data-key]');
    if (tr) onOpen?.(tr.dataset.key);
  });

  function toggle(id) {
    sort = sort.col === id ? { col: id, dir: sort.dir === 'asc' ? 'desc' : 'asc' } : { col: id, dir: id === 'account' || id === 'team' ? 'asc' : 'desc' };
    render();
  }

  function sorted() {
    const def = COLUMNS[sort.col] || COLUMNS.tokens;
    const sign = sort.dir === 'asc' ? 1 : -1;
    return [...data].sort((a, b) => {
      const va = def.sort(a);
      const vb = def.sort(b);
      if (va < vb) return -sign;
      if (va > vb) return sign;
      return String(a.key).localeCompare(String(b.key));
    });
  }

  function render() {
    headCells.forEach((th) => {
      if (th.dataset.col === sort.col && sortable) th.setAttribute('aria-sort', sort.dir === 'asc' ? 'ascending' : 'descending');
      else th.removeAttribute('aria-sort');
    });
    const list = sorted();
    const ctx = { maxShare: Math.max(0, ...list.map((r) => Number(r.share) || 0)), selected };
    const keep = new Set();
    list.forEach((row, index) => {
      const key = String(row.key ?? '');
      keep.add(key);
      let entry = rows.get(key);
      if (!entry) {
        const cells = cols.map((id) => h('td', { class: COLUMNS[id].num ? 'tv-num' : null, dataset: { col: id } }));
        const tr = h('tr', { dataset: { key } }, cells);
        entry = { tr, cells };
        rows.set(key, entry);
      }
      cols.forEach((id, i) => COLUMNS[id].cell(entry.cells[i], row, t, ctx));
      entry.cells.forEach((td, i) => {
        td.dataset.col = cols[i];
        if (COLUMNS[cols[i]].num) td.classList.add('tv-num');
      });
      if (tbody.children[index] !== entry.tr) tbody.insertBefore(entry.tr, tbody.children[index] || null);
    });
    for (const [key, entry] of rows) {
      if (!keep.has(key)) {
        entry.tr.remove();
        rows.delete(key);
      }
    }
  }

  // `list` = rows to show; `universe` = every row (default `list`). Ticks of rows hidden by a
  // page filter are kept; only rows missing from the whole dataset are dropped.
  function update(list, universe) {
    data = Array.isArray(list) ? list : [];
    const live = new Set((Array.isArray(universe) ? universe : data).map((r) => String(r.key)));
    for (const k of [...selected]) if (!live.has(k)) selected.delete(k);
    render();
    if (selectable) changed();
  }

  return { el: table, update, count: () => data.length, selected: () => [...selected] };
}
