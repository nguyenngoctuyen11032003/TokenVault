// #/employee/<key> — one employee (mockup option B).
// Header: Claude account e-mail (or "Unknown account (user@host)") · team, range switch
// (today / 7d / 30d, shared ctx.filters.range). Body: range-total gauge, cost estimate (or
// UNKNOWN), per-project breakdown bars (palette colours + share %), devices with online state,
// token trend (hourly for today, daily otherwise), model donut, session list.
// `key` is the EmployeeRow.key (e-mail or "device:<id>"), URL-encoded in the hash; the router
// hands it over decoded in ctx.args[0]. All data is untrusted and rendered as text.

import { disposeDetached, donutChart, lineChart, loadECharts } from '../charts.js';
import { colorFor } from '../palette.js';
import {
  clipText,
  coalesce,
  debounce,
  fmtCostPartial,
  fmtInt,
  fmtPct,
  fmtTokens,
  h,
  LIVE_DEBOUNCE_MS,
  localHour,
  niceCeil,
  RANGES,
  RANGE_DAYS,
  shortDay,
  shortHour,
  shortTime,
} from '../components/homekit.js';
import { createRangeSwitch } from '../components/rangeswitch.js';

// filters.range may hold a longer range set on #/overview (90d, all, custom "a..b"): this page
// only has today/7d/30d, so such a range is clamped to 30d with a visible note (never "today").
const isClamped = (range) => !!range && !RANGES.includes(range);
const pageRange = (range) => (RANGES.includes(range) ? range : isClamped(range) ? '30d' : 'today');

export default async function render(container, ctx) {
  const t = (key, params) => ctx.t(`employee.${key}`, params);
  const key = String(ctx.args[0] ?? '');
  await Promise.all([
    ctx.useStylesheet('css/pages/people.css'),
    ctx.useStylesheet('css/pages/employee.css'),
  ]);
  if (ctx.signal.aborted) return;
  const charts = loadECharts().catch((err) => {
    console.error('[employee] charts unavailable', err);
    return null;
  });

  let rev = 0;
  const unknown = t('unknownCost');
  const range = createRangeSwitch({
    t,
    value: pageRange(ctx.filters.get().range),
    label: t('rangeLabel'),
    onChange: (value) => ctx.filters.set({ range: value }),
  });

  // ---------- static layout ----------
  const gauge = h('tv-gauge', { 'data-kpi': 'range-tokens', label: t('gauge.label') });
  const costValue = h('b', { class: 'tv-employee-kpi__value tv-num--orange' });
  const costInfo = h('p', { class: 'tv-employee-costinfo', hidden: true });
  const scaleNote = h('p', { class: 'tv-employee-scale', 'data-scale-note': '', hidden: true });
  const rangeNote = h('p', { class: 'tv-employee-costinfo', 'data-range-note': '', hidden: true }, t('rangeClamped'));
  const alias = clipText('p', 'tv-employee-alias', '');
  const kpiSessions = h('b', { class: 'tv-employee-kpi__value' });
  const kpiEvents = h('b', { class: 'tv-employee-kpi__value' });
  const kpiCache = h('b', { class: 'tv-employee-kpi__value' });
  const projects = h('div', { class: 'tv-employee-projects', role: 'list', 'aria-label': t('projectsTitle') });
  const devices = h('ul', { class: 'tv-employee-devices', 'aria-label': t('devicesTitle') });
  const trendHost = h('div', { class: 'tv-employee-trend', dataset: { chart: 'daily' } });
  const modelHost = h('div', { class: 'tv-employee-models', dataset: { chart: 'models' } });
  const noModels = h('p', { class: 'tv-muted tv-employee-nomodels', hidden: true }, t('noModels'));
  const sessionBody = h('tbody');
  const noSessions = h('p', { class: 'tv-muted', hidden: true }, t('sessions.none'));
  const errorBox = h(
    'div',
    { class: 'tv-loaderror', role: 'alert', 'data-load-error': '', hidden: true },
    h('span'),
    h('button', { type: 'button', class: 'tv-btn', on: { click: () => refresh() } }, t('retry')),
  );

  const kpi = (label, value, attrs) =>
    h('div', { class: 'tv-employee-kpi', ...attrs }, h('span', { class: 'tv-employee-kpi__label' }, label), value);

  const head = h(
    'tv-panel',
    { class: 'tv-employee-head', heading: t('headingLoading') },
    h('div', { slot: 'actions' }, range.el),
    h(
      'div',
      { class: 'tv-employee-hero' },
      h('div', { class: 'tv-employee-gauge' }, gauge, scaleNote),
      h(
        'div',
        { class: 'tv-employee-main' },
        alias,
        h(
          'div',
          { class: 'tv-employee-kpis' },
          kpi(t('kpi.cost'), costValue, { 'data-kpi': 'range-cost' }),
          kpi(t('kpi.sessions'), kpiSessions),
          kpi(t('kpi.events'), kpiEvents),
          kpi(t('kpi.cache'), kpiCache),
        ),
        h('p', { class: 'tv-costnote', 'data-cost-note': '' }, t('costNote')),
        costInfo,
        rangeNote,
        h('div', { class: 'tv-employee-sub' }, t('projectsTitle')),
        projects,
      ),
      h('div', { class: 'tv-employee-side' }, h('div', { class: 'tv-employee-sub' }, t('devicesTitle')), devices),
    ),
  );

  const sessionsTable = h(
    'table',
    { class: 'tv-table tv-employee-sessions' },
    h(
      'colgroup',
      null,
      ['title', 'project', 'model', 'tokens', 'cost', 'started'].map((c) => h('col', { class: `tv-employee-sessions__col--${c}` })),
    ),
    h(
      'thead',
      null,
      h(
        'tr',
        null,
        h('th', { scope: 'col' }, t('sessions.title')),
        h('th', { scope: 'col' }, t('sessions.project')),
        h('th', { scope: 'col' }, t('sessions.model')),
        h('th', { scope: 'col', class: 'tv-num' }, t('sessions.tokens')),
        h('th', { scope: 'col', class: 'tv-num', title: t('costNote') }, t('sessions.cost')),
        h('th', { scope: 'col' }, t('sessions.started')),
      ),
    ),
    sessionBody,
  );

  const root = h(
    'div',
    { class: 'tv-employee', dataset: { tvRev: '0' } },
    errorBox,
    head,
    h(
      'div',
      { class: 'tv-grid tv-employee-charts' },
      h('tv-panel', { heading: t('trendTitle'), class: 'tv-employee-trendpanel' }, h('div', { class: 'tv-chart-area tv-employee-chartbox' }, trendHost)),
      h('tv-panel', { heading: t('modelsTitle'), class: 'tv-employee-modelpanel' }, h('div', { class: 'tv-employee-donutbox' }, modelHost), noModels),
    ),
    h(
      'tv-panel',
      { heading: t('sessionsTitle'), class: 'tv-employee-sessionspanel' },
      h('div', { class: 'tv-employee-tablewrap' }, sessionsTable),
      noSessions,
    ),
  );
  root.hidden = false;
  root.querySelector('.tv-employee-charts').hidden = true;
  root.querySelector('.tv-employee-sessionspanel').hidden = true;
  container.append(root);

  // ---------- updates ----------

  let reference = null; // {maxDay} from the employee's 30-day daily series; null = not loaded
  const referenceFrom = (view) => ({
    maxDay: Math.max(0, ...(view?.daily || []).map((d) => Number(d.total_tokens) || 0)),
  });

  function setHeading(view) {
    const name = String(view.email || view.display_name || view.key || key);
    const teams = [...new Set((view.devices || []).map((d) => String(d?.team || '')).filter(Boolean))];
    const heading = `${name} · ${teams.length ? teams.join(', ') : t('noTeam')}`;
    head.setAttribute('heading', heading);
    const tab = head.querySelector('.tv-panel__tab');
    if (tab) tab.title = heading;
    // A display name that differs from the e-mail (e.g. a real name) is shown under the header.
    const other = String(view.display_name || '');
    alias.textContent = other && other !== name ? other : '';
    alias.title = alias.textContent;
    alias.hidden = !alias.textContent;
  }

  // Gauge scale: the employee's busiest day in the last 30 days x the number of days in the
  // range (today = 1). So the arc shows "how close to a peak-pace range", not a fixed 87%.
  function gaugeMax(tokens, rng) {
    const days = RANGE_DAYS[rng];
    const peak = reference?.maxDay || 0;
    scaleNote.textContent = peak ? t(days === 1 ? 'scaleToday' : 'scaleRange', { days }) : '';
    scaleNote.hidden = !peak;
    return niceCeil(Math.max(tokens, peak * days));
  }

  function updateKpis(view, rng) {
    const totals = view.totals || {};
    const tokens = Number(totals.total_tokens) || 0;
    gauge.setAttribute('unit', t(`gauge.unit.${rng}`));
    gauge.setAttribute('max', String(gaugeMax(tokens, rng)));
    gauge.setAttribute('value', String(tokens));
    gauge.setAttribute('display', fmtTokens(tokens));
    gauge.title = `${fmtInt(tokens)} ${t('tokensUnit')}`;

    const costKnown = totals.cost !== null && totals.cost !== undefined;
    costValue.textContent = fmtCostPartial(totals.cost, totals.cost_partial, unknown);
    costValue.classList.toggle('tv-num--orange', costKnown);
    costValue.classList.toggle('tv-employee-kpi__unknown', !costKnown);
    const models = (view.unknown_cost_models || []).join(', ');
    let info = '';
    if (models && !costKnown) info = t('costUnknown', { models });
    else if (models && totals.cost_partial) info = t('costPartial', { models });
    costInfo.textContent = info;
    costInfo.hidden = !info;

    kpiSessions.textContent = fmtInt(totals.sessions);
    kpiEvents.textContent = fmtInt(totals.events);
    kpiCache.textContent = fmtTokens(totals.cache_read_tokens);
    kpiCache.title = fmtInt(totals.cache_read_tokens);
  }

  function updateProjects(view) {
    const total = Number(view.totals?.total_tokens) || 0;
    const list = [...(view.by_project || [])]
      .filter((p) => Number(p.total_tokens) > 0)
      .sort((a, b) => Number(b.total_tokens) - Number(a.total_tokens));
    if (!list.length) {
      projects.replaceChildren(h('p', { class: 'tv-muted' }, t('noProjects')));
      return;
    }
    projects.replaceChildren(
      ...list.map((p) => {
        const name = String(p.project || '') || t('noProjectName');
        const share = total ? Number(p.total_tokens) / total : 0;
        const color = colorFor(p.project || '');
        return h(
          'div',
          { class: 'tv-employee-project', role: 'listitem', dataset: { project: String(p.project || '') } },
          h(
            'div',
            { class: 'tv-employee-project__row' },
            h('i', { class: 'tv-employee-project__swatch', style: { background: color }, 'aria-hidden': 'true' }),
            clipText('span', 'tv-employee-project__name', name),
            h(
              'span',
              { class: 'tv-employee-project__val', title: `${fmtInt(p.total_tokens)} ${t('tokensUnit')}` },
              t('projectShare', { tokens: fmtTokens(p.total_tokens), pct: fmtPct(share) }),
            ),
          ),
          h('span', { class: 'tv-bar tv-employee-project__bar' }, h('i', { style: { width: `${Math.max(1, share * 100)}%`, background: color } })),
        );
      }),
    );
  }

  function updateDevices(view) {
    const list = view.devices || [];
    if (!list.length) {
      devices.replaceChildren(h('li', { class: 'tv-muted' }, t('noDevices')));
      return;
    }
    devices.replaceChildren(
      ...list.map((d) =>
        h(
          'li',
          { class: 'tv-employee-device', dataset: { device: String(d.device_id ?? '') } },
          h('span', { class: `tv-dot${d.online ? ' tv-dot--on' : ''}`, 'aria-hidden': 'true' }),
          h(
            'div',
            { class: 'tv-employee-device__text' },
            h(
              'div',
              { class: 'tv-employee-device__top' },
              clipText('b', 'tv-employee-device__host', d.hostname || d.device_id || ''),
              h('span', { class: d.online ? 'tv-employee-device__on' : 'tv-employee-device__off' }, t(d.online ? 'online' : 'offline')),
            ),
            clipText('span', 'tv-employee-device__meta', t('deviceUser', { user: d.windows_user || '' })),
            clipText('span', 'tv-employee-device__meta', t('deviceTeam', { team: d.team || t('noTeam') })),
            d.last_seen ? h('span', { class: 'tv-employee-device__meta' }, t('lastSeen', { time: shortTime(d.last_seen) })) : null,
          ),
        ),
      ),
    );
  }

  function updateSessions(view) {
    const list = view.sessions || [];
    noSessions.hidden = list.length > 0;
    sessionBody.replaceChildren(
      ...list.map((s) => {
        const title = s.title ? String(s.title) : t('sessions.untitled');
        const costKnown = s.cost !== null && s.cost !== undefined;
        const link = h('a', { class: 'tv-employee-session__link', href: `#/session/${encodeURIComponent(String(s.session_id ?? ''))}` }, clipText('span', s.title ? '' : 'tv-employee-session__untitled', title));
        return h(
          'tr',
          { dataset: { session: String(s.session_id ?? '') } },
          h('td', { dataset: { col: 'title' } }, link),
          h('td', { dataset: { col: 'project' } }, clipText('span', '', s.project || '')),
          h('td', { dataset: { col: 'model' } }, clipText('span', '', (s.models || []).join(', '))),
          h('td', { class: 'tv-num', dataset: { col: 'tokens' }, title: fmtInt(s.total_tokens) }, fmtTokens(s.total_tokens)),
          h(
            'td',
            { class: `tv-num ${costKnown ? 'tv-num--orange' : 'tv-emptable__unknown'}`, dataset: { col: 'cost' } },
            fmtCostPartial(s.cost, s.cost_partial, unknown),
          ),
          h('td', { class: 'tv-employee-session__time', dataset: { col: 'started' } }, shortTime(s.started_at)),
        );
      }),
    );
  }

  async function updateCharts(view, rng) {
    const echarts = await charts;
    if (!echarts || ctx.signal.aborted) return;
    let labels;
    let values;
    if (rng === 'today') {
      const nowHour = localHour();
      const points = view.hourly || [];
      labels = points.map((p) => shortHour(p.hour));
      values = points.map((p) => (String(p.hour) > nowHour ? null : Number(p.total_tokens) || 0));
      trendHost.dataset.series = 'hourly';
    } else {
      const points = view.daily || [];
      labels = points.map((p) => shortDay(p.day));
      values = points.map((p) => Number(p.total_tokens) || 0);
      trendHost.dataset.series = 'daily';
    }
    lineChart(
      trendHost,
      { x: labels, series: [{ name: t('gauge.label'), values }] },
      {
        label: t(rng === 'today' ? 'hourlyLabel' : 'dailyLabel'),
        unit: t('tokensUnit'),
        signal: ctx.signal,
        peak: true,
      },
    );
    const models = (view.by_model || [])
      .filter((m) => Number(m.total_tokens) > 0)
      .sort((a, b) => Number(b.total_tokens) - Number(a.total_tokens))
      .map((m) => ({ name: String(m.model || ''), value: Number(m.total_tokens) }));
    noModels.hidden = models.length > 0;
    donutChart(modelHost, models, {
      label: t('modelsLabel'),
      unit: t('tokensUnit'),
      signal: ctx.signal,
    });
  }

  async function draw() {
    const rawRange = ctx.filters.get().range;
    const rng = pageRange(rawRange);
    rangeNote.hidden = !isClamped(rawRange);
    try {
      // The 30-day reference (gauge scale) is fetched once; for the 30d range the view is it.
      const needRef = reference === null && rng !== '30d';
      const [ref, view] = await Promise.all([
        needRef ? ctx.api.employee(key, '30d').catch(() => undefined) : Promise.resolve(null),
        ctx.api.employee(key, rng),
      ]);
      if (ctx.signal.aborted) return;
      if (rng !== pageRange(ctx.filters.get().range)) return; // superseded; re-run queued
      if (!view || typeof view !== 'object') throw new Error('empty response');
      if (rng === '30d') reference = referenceFrom(view);
      else if (needRef) reference = referenceFrom(ref);
      errorBox.hidden = true;
      head.hidden = false;
      root.querySelector('.tv-employee-charts').hidden = false;
      root.querySelector('.tv-employee-sessionspanel').hidden = false;
      range.set(rng);
      setHeading(view);
      updateKpis(view, rng);
      updateProjects(view);
      updateDevices(view);
      updateSessions(view);
      await updateCharts(view, rng);
    } catch (err) {
      if (ctx.signal.aborted) return;
      console.error('[employee] load failed', err);
      errorBox.firstChild.textContent = t('loadError', { detail: err?.message || String(err) });
      errorBox.hidden = false;
    }
    if (ctx.signal.aborted) return;
    rev += 1;
    root.dataset.tvRev = String(rev);
  }

  const refresh = coalesce(draw);
  ctx.filters.subscribe((_state, changed) => {
    if (changed.includes('range')) refresh();
  });
  ctx.onLive(debounce(() => refresh(), LIVE_DEBOUNCE_MS, ctx.signal));
  await refresh();
}

export function destroy() {
  disposeDetached();
}
