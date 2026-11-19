// #/home — single-screen cockpit (mockup option A).
// Left: team list. Right: today's gauges (tokens, API-equivalent cost estimate, sessions).
// Below: employee table (online dot, tokens, share bar in the employee's palette colour, cost)
// and "Monitoring": live tokens-per-hour line with peak + current rate, top projects strip.
// Team selection lives in the global filter store (ctx.filters.team). Live events re-fetch the
// numbers and update everything in place (no rebuild: scroll, focus and selection are kept).
// Empty state (no employee devices yet) explains how to create the employee installer.

import { disposeDetached, lineChart, loadECharts } from '../charts.js';
import { colorFor } from '../palette.js';
import { createEmployeeTable } from '../components/employeetable.js';
import {
  apiFilter,
  clipText,
  coalesce,
  debounce,
  deriveTeams,
  fmtCost,
  fmtCostPartial,
  fmtInt,
  fmtTokens,
  h,
  LIVE_DEBOUNCE_MS,
  localHour,
  niceCeil,
  shortHour,
} from '../components/homekit.js';
import { createReportButton } from '../components/report-dialog.js';
import { createTeamList } from '../components/teamlist.js';

const TOP_PROJECTS = 5;


export default async function render(container, ctx) {
  const t = (key, params) => ctx.t(`home.${key}`, params);
  await Promise.all([
    ctx.useStylesheet('css/pages/people.css'),
    ctx.useStylesheet('css/pages/home.css'),
  ]);
  if (ctx.signal.aborted) return;

  const exportBtn = await createReportButton({
    api: ctx.api,
    signal: ctx.signal,
    attrs: { 'data-report-open': 'all' },
    preset: () => {
      const team = ctx.filters.get().team;
      return { scope: team ? 'team' : 'all', team: team ?? undefined, range: '7d' };
    },
  });
  const root = h('div', { class: 'tv-home', dataset: { tvRev: '0' } });
  container.append(root);
  const charts = loadECharts().catch((err) => {
    console.error('[home] charts unavailable', err);
    return null;
  });

  let mode = null; // 'full' | 'empty'
  let ui = null; // element refs of the full view
  let reference = { team: undefined, data: null };
  let rev = 0;

  const bump = () => {
    rev += 1;
    root.dataset.tvRev = String(rev);
  };

  const selectTeam = (team) => ctx.filters.set({ team });

  // ---------- views ----------

  function buildFull() {
    const teamList = createTeamList({ t, onSelect: selectTeam });
    teamList.el.setAttribute('aria-label', t('teams.label'));
    const gauges = {
      tokens: h('tv-gauge', { 'data-kpi': 'tokens', label: t('gauge.tokens'), unit: t('gauge.tokensUnit') }),
      cost: h('tv-gauge', { 'data-kpi': 'cost', label: t('gauge.cost'), unit: t('gauge.costUnit') }),
      sessions: h('tv-gauge', {
        'data-kpi': 'sessions',
        label: t('gauge.sessions'),
        unit: t('gauge.sessionsUnit'),
      }),
    };
    const costInfo = h('p', { class: 'tv-home-costinfo', hidden: true });
    const table = createEmployeeTable({
      t,
      columns: ['account', 'tokens', 'share', 'cost'],
      onOpen: (key) => ctx.navigate('employee', key),
    });
    table.el.setAttribute('aria-label', t('employeesLabel'));
    const noEmployees = h('p', { class: 'tv-muted tv-home-noemp', hidden: true }, t('noEmployees'));
    const hourlyHost = h('div', { class: 'tv-home-hourly', dataset: { chart: 'hourly' } });
    const peak = h('span', { dataset: { peak: '' } });
    const rate = h('div', { class: 'tv-kpi__value tv-num--hot' });
    const rateCost = h('span', { class: 'tv-num--orange' });
    const projects = h('div', { class: 'tv-chips tv-home-projects', role: 'list' });

    const teamsPanel = h('tv-panel', { heading: t('teamsTitle'), class: 'tv-home-teams' }, teamList.el);
    const todayPanel = h(
      'tv-panel',
      { heading: t('todayTitle'), class: 'tv-home-today' },
      h('div', { slot: 'actions', class: 'tv-home-actions' }, exportBtn, h('a', { class: 'tv-btn tv-home-overviewlink', href: '#/overview' }, t('overviewLink'))),
      h('div', { class: 'tv-gauge-row' }, gauges.tokens, gauges.cost, gauges.sessions),
      h(
        'div',
        { class: 'tv-home-today__foot' },
        h('p', { class: 'tv-costnote tv-home-costnote', 'data-cost-note': '' }, t('costNote')),
        costInfo,
        h('p', { class: 'tv-home-scale' }, t('scaleNote')),
      ),
    );
    const employeesPanel = h(
      'tv-panel',
      { heading: t('employeesTitle'), class: 'tv-home-employees' },
      h('div', { class: 'tv-home-tablewrap' }, table.el),
      noEmployees,
      h('a', { class: 'tv-home-seeall', href: '#/employees' }, t('seeAll')),
    );
    const monitorPanel = h(
      'tv-panel',
      { heading: t('monitorTitle'), class: 'tv-home-monitor' },
      h('div', { class: 'tv-meta' }, h('span', null, t('hourly')), peak),
      h(
        'div',
        { class: 'tv-split tv-home-split' },
        h('div', { class: 'tv-chart-area tv-home-chartbox' }, hourlyHost),
        h(
          'div',
          { class: 'tv-kpi tv-home-rate' },
          h('div', { class: 'tv-home-rate__label' }, t('rateLabel')),
          rate,
          h('div', { class: 'tv-kpi__sub' }, rateCost, ' ', h('span', null, t('perHour'))),
        ),
      ),
      h('div', { class: 'tv-meta tv-home-projects__head' }, h('span', null, t('topProjects'))),
      projects,
    );
    root.replaceChildren(
      h('div', { class: 'tv-grid tv-home-top' }, teamsPanel, todayPanel),
      h('div', { class: 'tv-grid tv-home-bottom' }, employeesPanel, monitorPanel),
    );
    return { teamList, gauges, costInfo, table, noEmployees, hourlyHost, peak, rate, rateCost, projects };
  }

  function buildEmpty() {
    root.replaceChildren(
      h(
        'tv-panel',
        { heading: t('empty.title'), class: 'tv-home-empty' },
        h(
          'div',
          { class: 'tv-home-empty__body' },
          h('div', { class: 'tv-home-empty__mark', 'aria-hidden': 'true' }),
          h(
            'div',
            null,
            h('p', { class: 'tv-home-empty__lead' }, t('empty.lead')),
            h('ol', { class: 'tv-home-empty__steps' }, h('li', null, t('empty.step1')), h('li', null, t('empty.step2')), h('li', null, t('empty.step3'))),
            h('a', { class: 'tv-btn tv-btn--primary', href: '#/installer' }, t('empty.action')),
          ),
        ),
      ),
    );
  }

  function setMode(next) {
    if (mode === next) return;
    mode = next;
    ui = null;
    if (next === 'full') ui = buildFull();
    else buildEmpty();
    disposeDetached();
  }

  // ---------- updates ----------

  function costInfoText(overview) {
    const models = (overview.unknown_cost_models || []).join(', ');
    if (!models) return '';
    if (overview.totals?.cost === null || overview.totals?.cost === undefined) {
      return t('costUnknown', { models });
    }
    return overview.totals?.cost_partial ? t('costPartial', { models }) : '';
  }

  function updateGauges(overview, ref) {
    const totals = overview.totals || {};
    const daily = ref?.daily || [];
    const maxDay = (field) => Math.max(0, ...daily.map((d) => Number(d[field]) || 0));
    const tokens = Number(totals.total_tokens) || 0;
    const g = ui.gauges;
    g.tokens.setAttribute('max', String(niceCeil(Math.max(tokens, maxDay('total_tokens')))));
    g.tokens.setAttribute('value', String(tokens));
    g.tokens.setAttribute('display', fmtTokens(tokens));

    const unknown = t('unknownCost');
    if (totals.cost === null || totals.cost === undefined) {
      g.cost.setAttribute('value', '');
      g.cost.setAttribute('max', '1');
      g.cost.setAttribute('display', unknown);
      g.cost.dataset.unknown = '';
    } else {
      delete g.cost.dataset.unknown;
      const cost = Number(totals.cost);
      g.cost.setAttribute('max', String(niceCeil(Math.max(cost, maxDay('cost')))));
      g.cost.setAttribute('value', String(cost));
      g.cost.setAttribute('display', fmtCostPartial(cost, totals.cost_partial, unknown));
    }

    const sessions = Number(totals.sessions) || 0;
    const activeDays = daily.filter((d) => Number(d.total_tokens) > 0).length || 1;
    const typical = ((Number(ref?.totals?.sessions) || 0) / activeDays) * 1.5;
    g.sessions.setAttribute('max', String(niceCeil(Math.max(sessions, typical, 10))));
    g.sessions.setAttribute('value', String(sessions));
    g.sessions.setAttribute('display', fmtInt(sessions));

    const info = costInfoText(overview);
    ui.costInfo.textContent = info;
    ui.costInfo.hidden = !info;
  }

  async function updateHourly(overview) {
    const points = overview.hourly || [];
    const nowHour = localHour();
    const today = nowHour.slice(0, 10);
    const labels = points.map((p) => shortHour(p.hour));
    const values = points.map((p) => {
      const hour = String(p.hour || '');
      return hour.slice(0, 10) === today && hour > nowHour ? null : Number(p.total_tokens) || 0;
    });
    let peakIndex = -1;
    values.forEach((v, i) => {
      if (v !== null && v > 0 && (peakIndex < 0 || v > values[peakIndex])) peakIndex = i;
    });
    ui.peak.textContent =
      peakIndex >= 0 ? t('peak', { value: fmtTokens(values[peakIndex]), hour: labels[peakIndex] }) : t('noPeak');

    let current = points.findIndex((p) => p.hour === nowHour);
    if (current < 0) {
      for (let i = values.length - 1; i >= 0; i -= 1) {
        if (values[i] !== null) {
          current = i;
          break;
        }
      }
    }
    const point = current >= 0 ? points[current] : null;
    ui.rate.textContent = fmtTokens(point ? point.total_tokens : 0);
    ui.rate.title = point ? `${fmtInt(point.total_tokens)} ${t('tokensUnit')}` : '';
    ui.rateCost.textContent = point
      ? fmtCostPartial(point.cost, point.cost_partial, t('unknownCost'))
      : fmtCost(0, t('unknownCost'));
    ui.rateCost.classList.toggle('tv-home-unknown', ui.rateCost.textContent === t('unknownCost'));

    const echarts = await charts;
    if (!echarts || ctx.signal.aborted || !ui) return;
    lineChart(
      ui.hourlyHost,
      { x: labels, series: [{ name: t('gauge.tokens'), values }] },
      { label: t('hourlyLabel'), unit: t('tokensUnit'), signal: ctx.signal, peak: true },
    );
  }

  function updateProjects(overview) {
    const list = [...(overview.by_project || [])]
      .filter((p) => Number(p.total_tokens) > 0)
      .sort((a, b) => Number(b.total_tokens) - Number(a.total_tokens))
      .slice(0, TOP_PROJECTS);
    if (!list.length) {
      ui.projects.replaceChildren(h('span', { class: 'tv-muted' }, t('noProjects')));
      return;
    }
    ui.projects.replaceChildren(
      ...list.map((p) => {
        const name = String(p.project || '') || t('noProjectName');
        return h(
          'a',
          {
            class: 'tv-home-chip',
            role: 'listitem',
            href: p.project ? `#/project/${encodeURIComponent(String(p.project))}` : '#/projects',
            dataset: { project: String(p.project || '') },
            title: `${name}: ${fmtInt(p.total_tokens)} ${t('tokensUnit')}`,
          },
          h('i', { class: 'tv-home-chip__swatch', style: { background: colorFor(p.project || '') }, 'aria-hidden': 'true' }),
          clipText('span', 'tv-home-chip__name', name),
          h('b', { class: 'tv-num--orange' }, fmtTokens(p.total_tokens)),
        );
      }),
    );
  }

  function showError(err) {
    let banner = root.querySelector('.tv-home-error');
    if (!banner) {
      banner = h(
        'div',
        { class: 'tv-loaderror tv-home-error', role: 'alert', 'data-load-error': '' },
        h('span'),
        h('button', { type: 'button', class: 'tv-btn', on: { click: () => refresh() } }, t('retry')),
      );
      root.prepend(banner);
    }
    banner.firstChild.textContent = t('loadError', { detail: err?.message || String(err) });
  }

  // ---------- data ----------

  async function draw() {
    const team = ctx.filters.get().team ?? null;
    const needRef = reference.team !== team;
    try {
      const [teams, overview, employees, ref] = await Promise.all([
        ctx.api.teams().catch((err) => {
          console.warn('[home] teams() failed, deriving teams from employees', err);
          return null;
        }),
        ctx.api.overview(apiFilter({ team }, 'today')),
        ctx.api.employees(apiFilter({ team }, 'today')),
        needRef ? ctx.api.overview(apiFilter({ team }, '30d')) : Promise.resolve(reference.data),
      ]);
      if (ctx.signal.aborted) return;
      if (team !== (ctx.filters.get().team ?? null)) return; // superseded; a re-run is queued
      reference = { team, data: ref };
      const people = Array.isArray(employees) ? employees : [];
      const teamList = Array.isArray(teams) ? teams : deriveTeams(people);
      const devices = teamList.reduce((a, x) => a + (Number(x?.devices) || 0), 0);
      root.querySelector('.tv-home-error')?.remove();
      if (devices === 0 && people.length === 0) {
        setMode('empty');
      } else {
        setMode('full');
        ui.teamList.update(teamList, team);
        updateGauges(overview || {}, ref);
        ui.table.update(people);
        ui.noEmployees.hidden = people.length > 0;
        updateProjects(overview || {});
        await updateHourly(overview || {});
      }
    } catch (err) {
      if (ctx.signal.aborted) return;
      console.error('[home] load failed', err);
      showError(err);
    }
    if (!ctx.signal.aborted) bump();
  }

  const refresh = coalesce(draw);
  ctx.filters.subscribe((_state, changed) => {
    if (changed.includes('team')) refresh();
  });
  ctx.onLive(debounce(() => refresh(), LIVE_DEBOUNCE_MS, ctx.signal));
  await refresh();
}

export function destroy() {
  disposeDetached();
}
