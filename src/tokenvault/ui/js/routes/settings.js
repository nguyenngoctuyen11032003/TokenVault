// #/settings — Manager connection (static-IP recommendation, port actually bound vs requested,
// manager id, editable host list written into new installers), app toggles (start with Windows,
// transparent window — needs a restart) and the employee device list (team reassign, revoke).
// Live events refresh the device list (debounced) without touching unsaved host edits.

import { confirmDialog } from '../components/installer-dialog.js';
import { describeError } from '../components/installer-errors.js';
import {
  coalesce,
  debounce,
  deviceName,
  errorBanner,
  errText,
  h,
  LIVE_DEBOUNCE_MS,
  revCounter,
  shortFingerprint,
  TEAM_MAX,
  toggleSwitch,
  HOSTS_MAX,
  validateHost,
  validateTeam,
} from '../components/installer-kit.js';
import { createDeviceTable, NEW_TEAM } from '../components/settings-devices.js';

export default async function render(container, ctx) {
  const t = (key, params) => ctx.t(`settings.${key}`, params);
  await Promise.all([
    ctx.useStylesheet('css/pages/people.css'),
    ctx.useStylesheet('css/pages/installer-kit.css'),
    ctx.useStylesheet('css/pages/settings.css'),
  ]);
  if (ctx.signal.aborted) return;

  const root = h('div', { class: 'tv-settings', dataset: { tvRev: '0' } });
  container.append(root);
  const bump = revCounter(root);
  const loadError = h('div');
  let dialogOpen = false;

  // ---------- network panel ----------
  const staticHint = h('p', { class: 'tv-settings-static__text' });
  const portValue = h('span', { class: 'tv-settings-port tv-num--orange', 'data-bound-port': '' });
  const portRequested = h('div', { class: 'tv-settings-fact', hidden: true });
  const portWarning = h('div', { class: 'tv-settings-warn', role: 'alert', 'data-port-warning': '', hidden: true });
  const managerId = h('dd', { class: 'tv-installer-mono' });
  const version = h('dd', { class: 'tv-installer-mono' });
  const fingerprint = h('dd', { class: 'tv-installer-mono' });

  const hostList = h('ul', { class: 'tv-settings-hosts', 'aria-label': t('hostsLabel') });
  const hostsEmpty = h('p', { class: 'tv-settings-hostsempty', hidden: true }, t('hostsEmpty'));
  const hostInput = h('input', {
    type: 'text', id: 'tv-settings-host', class: 'tv-installer-field__input', autocomplete: 'off',
    spellcheck: 'false', placeholder: t('hostPlaceholder'), 'aria-describedby': 'tv-settings-host-error',
  });
  const hostError = h('p', { class: 'tv-installer-field__error', id: 'tv-settings-host-error', 'data-host-error': '', hidden: true });
  const saveHosts = h('button', { type: 'button', class: 'tv-btn tv-btn--primary', dataset: { action: 'save-hosts' }, disabled: true }, t('saveHosts'));
  const resetHosts = h('button', { type: 'button', class: 'tv-installer-linkbtn', dataset: { action: 'reset-hosts' }, hidden: true }, t('resetHosts'));
  const hostsStatus = h('span', { class: 'tv-settings-hostsstatus', role: 'status' });
  const hostsSaveError = h('div');

  const networkPanel = h('tv-panel', { heading: t('networkTitle'), class: 'tv-settings-network' },
    h('div', { class: 'tv-settings-static', 'data-static-ip': '' },
      h('h3', { class: 'tv-settings-static__title' }, t('staticIpTitle')),
      staticHint,
      h('p', { class: 'tv-settings-static__text tv-settings-static__sub' }, t('subnetHint'))),
    h('div', { class: 'tv-settings-facts' },
      h('div', { class: 'tv-settings-fact tv-settings-fact--port' }, h('span', { class: 'tv-settings-fact__label' }, t('port')), portValue),
      portRequested),
    portWarning,
    h('div', { class: 'tv-settings-hostsbox' },
      h('h3', { class: 'tv-settings-subtitle' }, t('hostsTitle')),
      h('p', { class: 'tv-installer-field__hint' }, t('hostsHint')),
      hostList,
      hostsEmpty,
      h('div', { class: 'tv-settings-addhost' },
        h('label', { class: 'tv-settings-sr', for: 'tv-settings-host' }, t('hostInput')),
        hostInput,
        h('button', { type: 'button', class: 'tv-btn', dataset: { action: 'add-host' }, on: { click: addHost } }, t('addHost'))),
      hostError,
      hostsSaveError,
      h('div', { class: 'tv-settings-hostsactions' }, saveHosts, resetHosts, hostsStatus)));

  // ---------- app panel ----------
  const saveError = h('div');
  async function saveToggle(key, value) {
    if (!settings) throw new Error('settings not loaded'); // never persist the placeholder
    saveError.replaceChildren();
    try {
      const next = await ctx.api.save_settings({ [key]: value });
      if (next && typeof next === 'object') settings = { ...settings, ...next };
    } catch (err) {
      console.warn('[settings] save_settings failed', err);
      saveError.replaceChildren(errorBanner({ text: t('saveError', { detail: errText(err) }), attr: 'data-save-error' }));
      throw err;
    } finally {
      bump();
    }
  }
  const startSwitch = toggleSwitch({ setting: 'start_with_windows', label: t('startWithWindows'), checked: false, disabled: true, onChange: (v) => saveToggle('start_with_windows', v) });
  const transparentSwitch = toggleSwitch({ setting: 'transparent_window', label: t('transparent'), checked: true, disabled: true, onChange: (v) => saveToggle('transparent_window', v) });
  const appPanel = h('tv-panel', { heading: t('appTitle'), class: 'tv-settings-app' },
    saveError,
    h('div', { class: 'tv-settings-toggle' }, startSwitch, h('p', { class: 'tv-installer-field__hint' }, t('startWithWindowsHint'))),
    h('div', { class: 'tv-settings-toggle' }, transparentSwitch, h('p', { class: 'tv-installer-field__hint', 'data-restart-note': '' }, t('transparentHint'))),
    h('div', { class: 'tv-settings-about' },
      h('h3', { class: 'tv-settings-subtitle' }, t('aboutTitle')),
      h('dl', { class: 'tv-settings-ids' },
        h('dt', null, t('managerId')), managerId,
        h('dt', null, t('version')), version,
        h('dt', null, t('fingerprint')), fingerprint),
      h('a', { class: 'tv-settings-link', href: '#/diagnostics' }, t('openDiagnostics'))));

  // ---------- devices panel ----------
  const deviceTable = createDeviceTable({ t, onTeam: changeTeam, onRevoke: revokeDevice, onReset: resetDevice });
  const devicesCount = h('span', { class: 'tv-settings-count', slot: 'actions' });
  const devicesError = h('div');
  const devicesEmpty = h('div', { class: 'tv-settings-empty', 'data-devices-empty': '', hidden: true },
    h('p', { class: 'tv-muted' }, t('devicesEmpty')),
    h('a', { class: 'tv-btn tv-btn--primary', href: '#/installer' }, t('devicesEmptyAction')));
  const devicesPanel = h('tv-panel', { heading: t('devicesTitle'), class: 'tv-settings-devicespanel' },
    devicesCount, devicesError, h('div', { class: 'tv-settings-tablewrap' }, deviceTable.el), devicesEmpty);

  root.append(loadError, h('div', { class: 'tv-grid tv-settings-top' }, networkPanel, appPanel), devicesPanel);

  // ---------- state ----------
  let settings = null;
  let savedHosts = [];
  let draftHosts = [];
  let knownTeams = [];
  const dirty = () => JSON.stringify(savedHosts) !== JSON.stringify(draftHosts);

  function renderHosts() {
    hostList.replaceChildren(...draftHosts.map((host) => h('li', { dataset: { host } },
      h('span', { class: 'tv-clip tv-installer-mono', title: host }, host),
      h('button', {
        type: 'button', class: 'tv-settings-removehost', dataset: { action: 'remove-host' },
        'aria-label': t('removeHost', { host }), title: t('removeHost', { host }),
        on: { click: () => { draftHosts = draftHosts.filter((x) => x !== host); renderHosts(); } },
      }, h('span', { class: 'tv-settings-removehost__x', 'aria-hidden': 'true' })))));
    // Empty configured list = automatic: the Manager writes the addresses it detects.
    const auto = Array.isArray(settings?.hosts_auto) ? settings.hosts_auto.map(String) : [];
    hostsEmpty.hidden = draftHosts.length > 0;
    hostsEmpty.classList.toggle('tv-settings-hostsempty--auto', auto.length > 0);
    hostsEmpty.dataset.auto = auto.length ? '1' : '0';
    hostsEmpty.textContent = auto.length ? t('hostsAuto', { hosts: auto.join(', ') }) : t('hostsEmpty');
    if (auto.length) hostInput.placeholder = t('hostPlaceholderAuto', { host: auto[0] });
    else hostInput.placeholder = t('hostPlaceholder');
    const changed = dirty();
    saveHosts.disabled = !changed;
    resetHosts.hidden = !changed;
    hostsStatus.textContent = changed ? t('hostsUnsaved') : '';
  }

  function addHost() {
    const check = validateHost(hostInput.value, draftHosts);
    if (!check.ok) {
      hostError.dataset.reason = check.reason;
      hostError.textContent = t(`hostError.${check.reason}`, { max: HOSTS_MAX });
      hostError.hidden = false;
      hostInput.setAttribute('aria-invalid', 'true');
      hostInput.focus();
      return;
    }
    draftHosts = [...draftHosts, check.value];
    hostInput.value = '';
    renderHosts();
    hostInput.focus();
  }
  hostInput.addEventListener('input', () => { hostError.hidden = true; hostInput.removeAttribute('aria-invalid'); });
  hostInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      addHost();
    }
  });
  resetHosts.addEventListener('click', () => { draftHosts = [...savedHosts]; renderHosts(); });
  saveHosts.addEventListener('click', async () => {
    saveHosts.disabled = true;
    hostsSaveError.replaceChildren();
    try {
      const next = await ctx.api.save_settings({ hosts: draftHosts });
      if (ctx.signal.aborted) return;
      savedHosts = Array.isArray(next?.hosts) ? next.hosts.map(String) : [...draftHosts];
      draftHosts = [...savedHosts];
      renderHosts();
      hostsStatus.textContent = t('hostsSaved');
    } catch (err) {
      console.warn('[settings] save hosts failed', err);
      hostsSaveError.replaceChildren(errorBanner({ text: t('saveError', { detail: errText(err) }), attr: 'data-save-error' }));
      renderHosts();
    }
    bump();
  });

  function renderNetwork(diag) {
    const hint = String(settings?.static_ip_hint || '').trim();
    staticHint.textContent = hint || t('staticIpHint');
    const bound = Number(diag?.port) || Number(settings?.port) || 0;
    const requested = Number(diag?.port_requested) || Number(settings?.port) || bound;
    portValue.textContent = bound ? String(bound) : '';
    const fallback = !!diag && bound && requested && bound !== requested;
    portRequested.hidden = !fallback;
    portRequested.replaceChildren(h('span', { class: 'tv-settings-fact__label' }, t('portRequested')), h('span', { class: 'tv-settings-port tv-settings-port--old' }, String(requested)));
    portWarning.hidden = !fallback;
    portWarning.textContent = fallback ? t('portFallback', { requested, port: bound }) : '';
    managerId.textContent = String(settings?.manager_id ?? '');
    version.textContent = String(settings?.version ?? '');
    fingerprint.textContent = shortFingerprint(settings?.fingerprint);
    fingerprint.title = String(settings?.fingerprint ?? '');
    const hosts = Array.isArray(settings?.hosts) ? settings.hosts.map(String) : [];
    if (!dirty()) {
      savedHosts = hosts;
      draftHosts = [...hosts];
    } else {
      savedHosts = hosts;
    }
    renderHosts();
    // The switches stay disabled until real values arrived: never save the placeholder state.
    if (startSwitch.dataset.busy === undefined) startSwitch.set(!!settings?.start_with_windows);
    if (transparentSwitch.dataset.busy === undefined) transparentSwitch.set(settings?.transparent_window !== false);
    startSwitch.disabled = false;
    transparentSwitch.disabled = false;
  }

  function renderDevices(devices, teams) {
    const list = (Array.isArray(devices) ? devices : []).slice().sort((a, b) =>
      Number(!!b.online) - Number(!!a.online) || deviceName(a).localeCompare(deviceName(b), 'vi'));
    knownTeams = [...new Set([...(teams || []).map((x) => String(x?.name ?? '')), ...list.map((d) => String(d.team ?? ''))])];
    deviceTable.update(list, knownTeams);
    deviceTable.el.hidden = !list.length;
    devicesEmpty.hidden = list.length > 0;
    devicesCount.textContent = list.length ? t('devicesCount', { online: list.filter((d) => d.online).length, total: list.length }) : '';
  }

  async function changeTeam(device, value) {
    let team = value;
    if (value === NEW_TEAM) {
      dialogOpen = true;
      const name = deviceName(device);
      team = await confirmDialog({
        title: t('newTeamTitle'),
        body: t('newTeamBody', { name }),
        confirmLabel: t('newTeamConfirm'),
        cancelLabel: t('cancel'),
        signal: ctx.signal,
        input: {
          label: t('newTeamLabel'),
          maxLength: 80,
          validate: (raw) => {
            const check = validateTeam(raw);
            return check.ok ? '' : t(`teamError.${check.reason}`, { max: TEAM_MAX });
          },
          normalize: (raw) => validateTeam(raw).value,
        },
      });
      dialogOpen = false;
      if (team === null || ctx.signal.aborted) return false;
    }
    devicesError.replaceChildren();
    try {
      await ctx.api.set_device_team(device.device_id, team);
    } catch (err) {
      console.warn('[settings] set_device_team failed', err);
      const { code, text } = describeError(
        err,
        { invalid_team: t('teamError.server'), revoked: t('deviceRevoked') },
        (detail) => t('teamChangeError', { detail }),
      );
      devicesError.replaceChildren(errorBanner({ text, attr: 'data-device-error', kind: code || 'generic' }));
      if (code === 'revoked') refresh();
      bump();
      return false;
    }
    await refresh();
    return true;
  }

  async function revokeDevice(device) {
    const name = deviceName(device);
    dialogOpen = true;
    const ok = await confirmDialog({
      title: t('revokeTitle'),
      body: [t('revokeBody', { name, host: String(device.hostname ?? '') }), t('revokeHint')],
      confirmLabel: t('revokeConfirm'),
      cancelLabel: t('cancel'),
      danger: true,
      signal: ctx.signal,
    });
    dialogOpen = false;
    if (!ok || ctx.signal.aborted) return;
    devicesError.replaceChildren();
    try {
      await ctx.api.revoke_device(device.device_id);
    } catch (err) {
      console.warn('[settings] revoke_device failed', err);
      const { code, text } = describeError(err, { revoked: t('deviceRevoked') }, (detail) => t('revokeError', { detail }));
      devicesError.replaceChildren(errorBanner({ text, attr: 'data-device-error', kind: code || 'generic' }));
      if (code === 'revoked') await refresh();
      bump();
      return;
    }
    await refresh();
  }

  async function resetDevice(device) {
    const name = deviceName(device);
    dialogOpen = true;
    const ok = await confirmDialog({
      title: t('resetTitle'),
      body: [t('resetBody', { name, host: String(device.hostname ?? '') }), t('resetHint')],
      confirmLabel: t('resetConfirm'),
      cancelLabel: t('cancel'),
      danger: false,
      signal: ctx.signal,
    });
    dialogOpen = false;
    if (!ok || ctx.signal.aborted) return;
    devicesError.replaceChildren();
    try {
      await ctx.api.reset_device(device.device_id);
    } catch (err) {
      console.warn('[settings] reset_device failed', err);
      const { code, text } = describeError(err, { revoked: t('deviceRevoked') }, (detail) => t('resetError', { detail }));
      devicesError.replaceChildren(errorBanner({ text, attr: 'data-device-error', kind: code || 'generic' }));
      bump();
      return;
    }
    await refresh();
  }

  async function draw() {
    try {
      const [s, diag, devices, teams] = await Promise.all([
        ctx.api.settings(),
        ctx.api.diagnostics().catch((err) => { console.warn('[settings] diagnostics failed', err); return null; }),
        ctx.api.devices(),
        ctx.api.teams().catch(() => []),
      ]);
      if (ctx.signal.aborted) return;
      settings = s || {};
      loadError.replaceChildren();
      renderNetwork(diag);
      renderDevices(devices, teams);
    } catch (err) {
      if (ctx.signal.aborted) return;
      console.warn('[settings] load failed', err);
      loadError.replaceChildren(errorBanner({ text: t('loadError', { detail: errText(err) }), retryLabel: t('retry'), onRetry: () => refresh() }));
    }
    bump();
  }

  const refresh = coalesce(draw);

  ctx.onLive(debounce(() => { if (!dialogOpen) refresh(); }, LIVE_DEBOUNCE_MS, ctx.signal));
  await refresh();
}
