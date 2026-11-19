// Team list (home, left panel): "All" + one entry per team, with online/total device counts
// and an online dot. Keyed in-place updates, so live refreshes keep focus and selection.
//   const list = createTeamList({ t, onSelect: (team|null) => ... });
//   list.update(teams /* [{name, devices, online}] */, selectedTeam /* string|null */);
// `t` is scoped to the calling page (`<page>.teams.*` keys).

import { h } from './homekit.js';

export function createTeamList({ t, onSelect }) {
  const ul = h('ul', { class: 'tv-list tv-teamlist', role: 'list' });
  const items = new Map(); // team key ('' = all) -> {li, button, name, dot, count}

  function entry(key) {
    let item = items.get(key);
    if (item) return item;
    const name = h('span', { class: 'tv-clip tv-teamlist__name' });
    const dot = h('span', { class: 'tv-dot', 'aria-hidden': 'true' });
    const count = h('span', { class: 'tv-list__count tv-teamlist__count' });
    const button = h(
      'button',
      {
        type: 'button',
        class: 'tv-list__item',
        dataset: { team: key },
        on: { click: () => onSelect?.(key === '' ? null : key) },
      },
      h('span', { class: 'tv-list__ring', 'aria-hidden': 'true' }),
      name,
      count,
      dot,
    );
    const li = h('li', null, button);
    item = { li, button, name, dot, count };
    items.set(key, item);
    return item;
  }

  function update(teams, selected) {
    const list = Array.isArray(teams) ? teams : [];
    const sum = (field) => list.reduce((a, team) => a + (Number(team?.[field]) || 0), 0);
    const rows = [
      { key: '', label: t('teams.all'), devices: sum('devices'), online: sum('online') },
      ...list.map((team) => ({
        key: String(team?.name ?? ''),
        label: String(team?.name || t('teams.none')),
        devices: Number(team?.devices) || 0,
        online: Number(team?.online) || 0,
      })),
    ];
    const keep = new Set();
    rows.forEach((row, index) => {
      if (index > 0 && row.key === '') return; // a team literally named '' cannot be selected
      const item = entry(row.key);
      keep.add(row.key);
      item.name.textContent = row.label;
      item.name.title = row.label;
      item.count.textContent = `${row.online}/${row.devices}`;
      const on = row.online > 0;
      item.dot.className = `tv-dot${on ? ' tv-dot--on' : ''}`;
      const status = t('teams.status', { online: row.online, total: row.devices });
      item.button.setAttribute('aria-label', `${row.label}, ${status}`);
      item.button.setAttribute('aria-pressed', String((selected ?? '') === row.key));
      if (ul.children[index] !== item.li) ul.insertBefore(item.li, ul.children[index] || null);
    });
    for (const [key, item] of items) {
      if (!keep.has(key)) {
        item.li.remove();
        items.delete(key);
      }
    }
    // A selected team that no longer exists: nothing pressed except via the caller's reset.
  }

  return { el: ul, update };
}
