// #/diagnostics — connectivity self-test (bind, firewall rule, LAN IPs, certificate), live
// status (port bound vs requested, connected devices, last event, rejected events, store,
// certificate fingerprint), Manager warnings with a prominent banner when the firewall rule is
// missing or the port fell back, and an explicit "Regenerate certificate" behind a strong
// confirm. Status auto-refreshes every 5 s while the window is visible and on live events
// (debounced); timers and listeners are removed when the route is left.

import { confirmDialog } from '../components/installer-dialog.js';
import { describeError } from '../components/installer-errors.js';
import { fmtInt } from '../components/homekit.js';
import {
  clipText,
  coalesce,
  copyButton,
  debounce,
  deviceName,
  errorBanner,
  fmtWhen,
  h,
  LIVE_DEBOUNCE_MS,
  revCounter,
  shortFingerprint,
} from '../components/installer-kit.js';

const REFRESH_MS = Number(globalThis.TV_DIAG_REFRESH_MS ?? 5000);
const KNOWN_CHECKS = ['bind', 'firewall_rule', 'lan_ips', 'certificate'];

export default async function render(container, ctx) {
  const t = (key, params) => ctx.t(`diagnostics.${key}`, params);
  await Promise.all([
    ctx.useStylesheet('css/pages/people.css'),
    ctx.useStylesheet('css/pages/installer-kit.css'),
    ctx.useStylesheet('css/pages/diagnostics.css'),
  ]);
  if (ctx.signal.aborted) return;

  const root = h('div', { class: 'tv-diagnostics', dataset: { tvRev: '0' } });
  container.append(root);
  const bump = revCounter(root);
  const banners = h('div', { class: 'tv-diagnostics-banners' });
  const loadError = h('div');
  let fingerprint = '';
  let dialogOpen = false;

  // ---------- self-test ----------
  const runButton = h('button', { type: 'button', class: 'tv-btn tv-btn--primary', dataset: { action: 'run-test' } }, t('runTest'));
  const testSummary = h('p', { class: 'tv-diagnostics-summary', role: 'status' });
  const checks = h('ul', { class: 'tv-diagnostics-checks', 'aria-label': t('checksLabel') });
  const testPanel = h('tv-panel', { heading: t('testTitle'), class: 'tv-diagnostics-test' },
    h('div', { class: 'tv-diagnostics-testhead' }, h('p', { class: 'tv-muted' }, t('testLead')), runButton),
    testSummary, checks,
    h('p', { class: 'tv-diagnostics-subnet' }, t('subnetHint')));

  // ---------- status ----------
  const stat = (key, cls = '') => h('dd', { class: `tv-diagnostics-stat__value ${cls}`.trim(), dataset: { stat: key } });
  const s = {
    port: stat('port', 'tv-num--orange'),
    portSub: h('span', { class: 'tv-diagnostics-stat__sub', hidden: true }),
    connected: stat('connected', 'tv-num--hot'),
    lastEvent: stat('lastEvent'),
    rejected: stat('rejected'),
    queue: stat('queue'),
    db: stat('db'),
    fp: h('code', { class: 'tv-installer-mono tv-diagnostics-fp', 'data-fingerprint': '' }),
    expires: stat('expires'),
  };
  const copyFp = copyButton({
    label: t('copyFingerprint'), copiedLabel: t('copied'), failedLabel: t('copyFailed'), hint: t('copyHint'),
    action: 'copy-fingerprint', cls: 'tv-installer-linkbtn', getText: () => fingerprint, target: () => s.fp,
  });
  copyFp.disabled = true; // enabled once a fingerprint has loaded
  const updated = h('span', { class: 'tv-diagnostics-updated', slot: 'actions' });
  const item = (label, ...value) => h('div', { class: 'tv-diagnostics-stat' }, h('dt', null, label), ...value);
  const statusPanel = h('tv-panel', { heading: t('statusTitle'), class: 'tv-diagnostics-status' },
    updated,
    h('dl', { class: 'tv-diagnostics-stats' },
      item(t('stat.port'), s.port, s.portSub),
      item(t('stat.connected'), s.connected),
      item(t('stat.rejected'), s.rejected, h('span', { class: 'tv-diagnostics-stat__sub' }, t('rejectedHint'))),
      item(t('stat.lastEvent'), s.lastEvent),
      item(t('stat.queue'), s.queue),
      item(t('stat.expires'), s.expires),
      h('div', { class: 'tv-diagnostics-stat tv-diagnostics-stat--wide' }, h('dt', null, t('stat.fingerprint')),
        h('dd', { class: 'tv-diagnostics-fprow' }, s.fp,
          copyFp)),
      h('div', { class: 'tv-diagnostics-stat tv-diagnostics-stat--wide' }, h('dt', null, t('stat.db')), s.db)));

  // ---------- connected devices + warnings ----------
  const connectedList = h('ul', { class: 'tv-diagnostics-connected', 'data-connected': '', 'aria-label': t('connectedLabel') });
  const connectedEmpty = h('p', { class: 'tv-muted', hidden: true }, t('connectedEmpty'));
  const warningsBox = h('div', { class: 'tv-diagnostics-warnings', hidden: true },
    h('h3', { class: 'tv-diagnostics-subtitle' }, t('warningsTitle')));
  const warningsList = h('ul', { 'data-warnings': '' });
  warningsBox.append(warningsList);
  const connectedPanel = h('tv-panel', { heading: t('connectedTitle'), class: 'tv-diagnostics-connpanel' },
    connectedList, connectedEmpty, warningsBox);

  // ---------- certificate ----------
  const regenButton = h('button', { type: 'button', class: 'tv-btn tv-diagnostics-regen', dataset: { action: 'regenerate' }, on: { click: regenerate } }, t('regenerate'));
  const regenResult = h('div');
  const certPanel = h('tv-panel', { heading: t('certTitle'), class: 'tv-diagnostics-cert' },
    h('p', { class: 'tv-muted' }, t('certLead')),
    h('div', { class: 'tv-diagnostics-danger' },
      h('p', null, t('regenerateBody')),
      regenButton),
    regenResult);

  root.append(banners, loadError,
    h('div', { class: 'tv-grid tv-diagnostics-top' }, testPanel, statusPanel),
    h('div', { class: 'tv-grid tv-diagnostics-bottom' }, connectedPanel, certPanel));

  // ---------- rendering ----------
  function renderBanners(diag) {
    const port = Number(diag.port) || 0;
    const requested = Number(diag.port_requested) || port;
    const list = [];
    if (diag.firewall_rule_present === false) list.push(['firewall', t('warnFirewall', { port: requested || port })]);
    if (port && requested && port !== requested) list.push(['port', t('warnPort', { port, requested })]);
    banners.replaceChildren(...list.map(([kind, text]) => h('div', { class: 'tv-diagnostics-banner', role: 'alert', 'data-diag-warning': '', 'data-kind': kind },
      h('span', { class: 'tv-diagnostics-banner__mark', 'aria-hidden': 'true' }, '!'), h('p', null, text))));
  }

  function renderStatus(diag, devices) {
    const port = Number(diag.port) || 0;
    const requested = Number(diag.port_requested) || port;
    s.port.textContent = port ? String(port) : '';
    s.portSub.hidden = !(requested && requested !== port);
    s.portSub.textContent = t('stat.portRequested', { port: requested });
    const ids = Array.isArray(diag.connected) ? diag.connected.map(String) : [];
    s.connected.textContent = fmtInt(Number(diag.connected_count ?? ids.length) || 0);
    s.lastEvent.textContent = fmtWhen(diag.last_event_at) || t('never');
    s.rejected.textContent = fmtInt(Number(diag.rejected_count) || 0);
    s.rejected.classList.toggle('tv-num--hot', Number(diag.rejected_count) > 0);
    const queue = diag.queue || {};
    s.queue.textContent = t('stat.queueValue', { events: fmtInt(Number(queue.events) || 0), devices: fmtInt(Number(queue.devices) || 0) });
    s.db.replaceChildren(clipText('span', 'tv-installer-mono', String(diag.db_path ?? '')));
    fingerprint = String(diag.fingerprint ?? '');
    s.fp.textContent = shortFingerprint(fingerprint);
    s.fp.title = fingerprint;
    copyFp.disabled = !fingerprint;
    s.expires.textContent = fmtWhen(diag.cert_expires_at);
    const now = new Date();
    updated.textContent = t('updated', { time: `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}` });

    const byId = new Map((Array.isArray(devices) ? devices : []).map((d) => [String(d.device_id), d]));
    connectedList.replaceChildren(...ids.map((id) => {
      const d = byId.get(id);
      const host = d ? [d.hostname, d.windows_user].filter(Boolean).map(String).join(' · ') : '';
      return h('li', { dataset: { device: id } },
        h('span', { class: 'tv-dot tv-dot--on', 'aria-hidden': 'true' }),
        clipText('span', 'tv-diagnostics-connname', d ? deviceName(d) : id),
        d ? clipText('span', 'tv-diagnostics-connhost', host) : null,
        d?.team ? clipText('span', 'tv-diagnostics-connteam', String(d.team)) : null);
    }));
    connectedEmpty.hidden = ids.length > 0;
    const warnings = Array.isArray(diag.warnings) ? diag.warnings.map(String).filter(Boolean) : [];
    warningsList.replaceChildren(...warnings.map((w) => h('li', null, w)));
    warningsBox.hidden = !warnings.length;
  }

  function renderChecks(result) {
    const list = Array.isArray(result?.checks) ? result.checks : [];
    checks.replaceChildren(...list.map((c) => {
      const name = String(c?.name ?? '');
      const ok = !!c?.ok;
      return h('li', { class: `tv-diagnostics-check ${ok ? 'is-ok' : 'is-fail'}`, dataset: { check: name, ok: String(ok) } },
        h('span', { class: 'tv-diagnostics-check__icon', 'aria-hidden': 'true' }),
        h('div', { class: 'tv-diagnostics-check__text' },
          h('span', { class: 'tv-diagnostics-check__name' }, KNOWN_CHECKS.includes(name) ? t(`check.${name}`) : name),
          clipText('span', 'tv-diagnostics-check__detail', String(c?.detail ?? ''))),
        h('span', { class: 'tv-diagnostics-check__state' }, ok ? t('ok') : t('fail')));
    }));
    const allOk = !!result?.ok && list.every((c) => c?.ok);
    testSummary.textContent = list.length ? (allOk ? t('testOk') : t('testFail')) : '';
    testSummary.className = `tv-diagnostics-summary ${allOk ? 'is-ok' : 'is-fail'}`;
  }

  // ---------- data ----------
  async function runTest() {
    runButton.disabled = true;
    runButton.textContent = t('testing');
    checks.setAttribute('aria-busy', 'true');
    try {
      const result = await ctx.api.connectivity_test();
      if (ctx.signal.aborted) return;
      renderChecks(result);
    } catch (err) {
      if (ctx.signal.aborted) return;
      console.warn('[diagnostics] connectivity_test failed', err);
      checks.replaceChildren();
      testSummary.className = 'tv-diagnostics-summary is-fail';
      testSummary.textContent = describeError(err, { cert_error: t('certError') }, (detail) => t('testError', { detail })).text;
    } finally {
      runButton.disabled = false;
      runButton.textContent = t('runTest');
      checks.removeAttribute('aria-busy');
    }
    bump();
  }
  runButton.addEventListener('click', runTest);

  async function draw() {
    try {
      const [diag, devices] = await Promise.all([
        ctx.api.diagnostics(),
        ctx.api.devices().catch((err) => { console.warn('[diagnostics] devices failed', err); return []; }),
      ]);
      if (ctx.signal.aborted) return;
      loadError.replaceChildren();
      renderBanners(diag || {});
      renderStatus(diag || {}, devices);
    } catch (err) {
      if (ctx.signal.aborted) return;
      console.warn('[diagnostics] load failed', err);
      const { code, text } = describeError(err, { cert_error: t('certError') }, (detail) => t('loadError', { detail }));
      loadError.replaceChildren(errorBanner({ text, kind: code || 'generic', retryLabel: t('retry'), onRetry: () => refresh() }));
    }
    bump();
  }
  const refresh = coalesce(draw);

  async function regenerate() {
    if (regenButton.disabled) return;
    regenButton.disabled = true; // while the dialog is open and the call is in flight
    try {
      await regenerateFlow();
    } finally {
      regenButton.disabled = false;
    }
  }

  async function regenerateFlow() {
    dialogOpen = true;
    const ok = await confirmDialog({
      title: t('regenerateTitle'),
      body: [t('regenerateBody'), t('regenerateHint')],
      confirmLabel: t('regenerateConfirm'),
      cancelLabel: t('cancel'),
      danger: true,
      check: t('regenerateCheck'),
      signal: ctx.signal,
    });
    dialogOpen = false;
    if (!ok || ctx.signal.aborted) return;
    try {
      const result = await ctx.api.regenerate_certificate();
      if (ctx.signal.aborted) return;
      regenResult.replaceChildren(h('div', { class: 'tv-diagnostics-regenok', role: 'status', 'data-regen-result': '' },
        h('p', null, t('regenerateDone', { fingerprint: shortFingerprint(result?.fingerprint) })),
        result?.warning ? h('p', { class: 'tv-diagnostics-regenwarn' }, String(result.warning)) : null,
        h('a', { class: 'tv-btn tv-btn--primary', href: '#/installer' }, t('makeInstallers'))));
    } catch (err) {
      if (ctx.signal.aborted) return;
      console.warn('[diagnostics] regenerate_certificate failed', err);
      const { code, text } = describeError(err, { cert_error: t('certError') }, (detail) => t('regenerateError', { detail }));
      regenResult.replaceChildren(errorBanner({ text, attr: 'data-regen-error', kind: code || 'generic' }));
    }
    await refresh();
  }

  // ---------- refresh loop ----------
  const timer = setInterval(() => {
    if (document.visibilityState === 'visible' && !dialogOpen) refresh();
  }, REFRESH_MS);
  const onVisible = () => {
    if (document.visibilityState === 'visible') refresh();
  };
  document.addEventListener('visibilitychange', onVisible);
  ctx.onCleanup(() => {
    clearInterval(timer);
    document.removeEventListener('visibilitychange', onVisible);
  });
  ctx.onLive(debounce(() => refresh(), LIVE_DEBOUNCE_MS, ctx.signal));
  await Promise.all([refresh(), runTest()]);
}
