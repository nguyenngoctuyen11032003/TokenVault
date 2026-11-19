// Device table for #/settings (Task 17): online dot, employee name (display name / email),
// host + Windows user, team <select> (reassign; "new team..." asks via the page), last seen,
// revoke button. Keyed in-place updates so live refreshes keep focus and open selects.
//   const table = createDeviceTable({ t, onTeam: (device, team|NEW_TEAM) => Promise,
//                                     onRevoke: (device) => Promise });
//   table.update(devices, teamNames);
// `t` is scoped to settings.* keys. Untrusted values only reach textContent / attributes.

import { clipText, deviceName, fmtWhen, h } from './installer-kit.js';

export const NEW_TEAM = '__new__';

export function createDeviceTable({ t, onTeam, onRevoke }) {
  const body = h('tbody');
  const table = h(
    'table',
    { class: 'tv-table tv-settings-devices', 'aria-label': t('devicesLabel') },
    h('colgroup', null, ['employee', 'device', 'team', 'seen', 'action'].map((c) => h('col', { class: `tv-settings-devices__col--${c}` }))),
    h('thead', null, h('tr', null,
      h('th', { scope: 'col' }, t('col.employee')),
      h('th', { scope: 'col' }, t('col.device')),
      h('th', { scope: 'col' }, t('col.team')),
      h('th', { scope: 'col' }, t('col.lastSeen')),
      h('th', { scope: 'col', class: 'tv-settings-devices__actionhead' }, h('span', { class: 'tv-settings-sr' }, t('col.action'))))),
    body,
  );
  const rows = new Map(); // device_id -> {tr, device, ...cells}
  let teams = [];

  function fillSelect(select, current) {
    const names = [...new Set([...teams, current])].sort((a, b) => a.localeCompare(b, 'vi'));
    const options = names.map((name) => h('option', { value: name }, name === '' ? t('noTeam') : name));
    options.push(h('option', { value: NEW_TEAM }, t('newTeam')));
    select.replaceChildren(...options);
    select.value = current;
    select.dataset.options = JSON.stringify(names);
  }

  function makeRow(device) {
    const dot = h('span', { class: 'tv-dot', 'aria-hidden': 'true' });
    const name = clipText('span', 'tv-settings-name', '');
    const state = h('span', { class: 'tv-settings-sr' });
    const host = clipText('span', 'tv-settings-host', '');
    const select = h('select', { class: 'tv-settings-select' });
    const seen = h('td', { class: 'tv-settings-seen' });
    const revoke = h('button', { type: 'button', class: 'tv-installer-linkbtn', dataset: { action: 'revoke-device' } }, t('revoke'));
    const tr = h('tr', null,
      h('td', null, h('div', { class: 'tv-settings-who' }, dot, name, state)),
      h('td', null, host),
      h('td', { dataset: { col: 'team' } }, select),
      seen,
      h('td', { class: 'tv-settings-rowaction' }, revoke));
    const row = { tr, dot, name, state, host, select, seen, revoke, device };
    select.addEventListener('change', async () => {
      const value = select.value;
      const prev = row.device.team ?? '';
      if (value === prev) return;
      select.disabled = true;
      try {
        const ok = await onTeam(row.device, value);
        if (!ok) select.value = prev;
      } finally {
        select.disabled = false;
      }
    });
    revoke.addEventListener('click', () => onRevoke(row.device));
    return row;
  }

  function fill(row, device) {
    row.device = device;
    const label = deviceName(device);
    const team = String(device.team ?? '');
    row.tr.dataset.device = String(device.device_id);
    row.tr.dataset.team = team;
    row.dot.classList.toggle('tv-dot--on', !!device.online);
    row.name.textContent = label;
    row.name.title = device.email && device.email !== label ? `${label} (${device.email})` : label;
    row.state.textContent = device.online ? t('online') : t('offline');
    const host = [device.hostname, device.windows_user].filter(Boolean).map(String).join(' · ');
    row.host.textContent = host;
    row.host.title = host;
    row.select.setAttribute('aria-label', t('teamSelect', { name: label }));
    const optionsKey = JSON.stringify([...new Set([...teams, team])].sort((a, b) => a.localeCompare(b, 'vi')));
    if (document.activeElement !== row.select && (row.select.dataset.options !== optionsKey || row.select.value !== team)) {
      fillSelect(row.select, team);
    }
    row.seen.textContent = device.online ? t('online') : fmtWhen(device.last_seen) || t('never');
    row.seen.classList.toggle('is-online', !!device.online);
    row.revoke.setAttribute('aria-label', t('revokeAria', { name: label }));
  }

  function update(devices, teamNames) {
    teams = [...new Set((teamNames || []).map((x) => String(x ?? '')))];
    const list = Array.isArray(devices) ? devices : [];
    const seenIds = new Set();
    const ordered = [];
    for (const device of list) {
      const id = String(device?.device_id ?? '');
      if (!id || seenIds.has(id)) continue;
      seenIds.add(id);
      let row = rows.get(id);
      if (!row) {
        row = makeRow(device);
        rows.set(id, row);
      }
      fill(row, device);
      ordered.push(row.tr);
    }
    for (const [id, row] of rows) {
      if (!seenIds.has(id)) {
        row.tr.remove();
        rows.delete(id);
      }
    }
    ordered.forEach((tr, i) => {
      if (body.children[i] !== tr) body.insertBefore(tr, body.children[i] ?? null);
    });
  }

  return { el: table, update };
}
