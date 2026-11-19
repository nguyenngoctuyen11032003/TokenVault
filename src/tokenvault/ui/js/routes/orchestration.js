// #/orchestration - tokens of the main thread vs sub-agents (is_sidechain), the model split of
// each side and the sessions that spent the most sub-agent tokens. Agent names are not on the
// wire, so "agents" are ranked per session by sidechain tokens (stated in an in-page note).
// Ranges 7d / 30d / 90d / all. All data is untrusted and rendered as text.

import { disposeDetached, donutChart, loadECharts } from '../charts.js';
import {
  coalesce,
  debounce,
  fmtInt,
  fmtPct,
  fmtTokens,
  h,
  LIVE_DEBOUNCE_MS,
} from '../components/homekit.js';
import {
  createErrorBox,
  createKpiStrip,
  createRangeBar,
  DEFAULT_RANGE,
  rankList,
} from '../components/skills-kit.js';

// The two sides use CSS tokens outside the categorical series (models / sessions use the palette).
const MAIN_COLOR = 'var(--tv-text)'; // neutral light grey
const SUB_COLOR = 'var(--tv-red-hot)'; // brand accent
const num = (v) => Math.max(0, Number(v) || 0);

export default async function render(container, ctx) {
  const t = (key, params) => ctx.t(`orchestration.${key}`, params);
  await Promise.all([
    ctx.useStylesheet('css/pages/people.css'),
    ctx.useStylesheet('css/pages/skills-shared.css'),
    ctx.useStylesheet('css/pages/orchestration-page.css'),
  ]);
  if (ctx.signal.aborted) return;
  const charts = loadECharts().catch((err) => {
    console.error('[orchestration] charts unavailable', err);
    return null;
  });

  let rev = 0;
  const range = createRangeBar({ t, value: DEFAULT_RANGE, label: t('rangeLabel'), onChange: () => refresh() });
  const errorBox = createErrorBox({ retryText: t('retry'), onRetry: () => refresh() });
  const kpis = createKpiStrip([
    { id: 'main', label: t('kpi.main') },
    { id: 'sub', label: t('kpi.sub'), accent: 'orange' },
    { id: 'share', label: t('kpi.share') },
    { id: 'sessions', label: t('kpi.sessions') },
  ]);

  const segMain = h('i', { class: 'tv-orch-split__seg', style: { background: MAIN_COLOR }, 'data-seg': 'main' });
  const segSub = h('i', { class: 'tv-orch-split__seg', style: { background: SUB_COLOR }, 'data-seg': 'sub' });
  const legendMain = h('span', { class: 'tv-orch-split__legend', 'data-side': 'main' });
  const legendSub = h('span', { class: 'tv-orch-split__legend', 'data-side': 'sub' });
  const split = h(
    'div',
    { class: 'tv-orch-split' },
    h('div', { class: 'tv-orch-split__bar', role: 'img' }, segMain, segSub),
    h(
      'div',
      { class: 'tv-orch-split__legends' },
      h('span', null, h('i', { class: 'tv-skills-rank__swatch', style: { background: MAIN_COLOR } }), legendMain),
      h('span', null, h('i', { class: 'tv-skills-rank__swatch', style: { background: SUB_COLOR } }), legendSub),
    ),
  );

  const donut = (side) => h('div', { class: 'tv-orch-donut', dataset: { chart: `models-${side}` } });
  const mainDonut = donut('main');
  const subDonut = donut('sub');
  const mainEmpty = h('p', { class: 'tv-skills-empty', 'data-empty': 'main', hidden: true }, t('emptyMain'));
  const subEmpty = h('p', { class: 'tv-skills-empty', 'data-empty': 'sidechain', hidden: true }, t('emptySidechain'));
  const agentsEmpty = h('p', { class: 'tv-skills-empty', 'data-empty': 'agents', hidden: true }, t('emptyAgents'));
  const agentsHost = h('div', { class: 'tv-skills-ranklist', role: 'list', 'aria-label': t('agentsTitle') });
  const allEmpty = h('p', { class: 'tv-skills-empty', 'data-empty': 'all', hidden: true }, t('emptyAll'));

  const note = h(
    'p',
    { class: 'tv-skills-note', 'data-note': 'limitation', role: 'note' },
    h('b', null, t('noteLead')),
    ' ',
    t('note'),
  );
  const explain = h(
    'details',
    { class: 'tv-orch-explain', 'data-explain': '' },
    h('summary', null, t('explain.title')),
    h('p', null, t('explain.main')),
    h('p', null, t('explain.sub')),
    h('p', null, t('explain.agents')),
  );

  const body = h(
    'div',
    { class: 'tv-skills-body' },
    h(
      'div',
      { class: 'tv-grid tv-orch-donuts' },
      h(
        'tv-panel',
        { heading: t('mainModels'), class: 'tv-orch-donutpanel' },
        h('div', { class: 'tv-chart-area tv-orch-chartbox' }, mainDonut),
        mainEmpty,
      ),
      h(
        'tv-panel',
        { heading: t('subModels'), class: 'tv-orch-donutpanel' },
        h('div', { class: 'tv-chart-area tv-orch-chartbox' }, subDonut),
        subEmpty,
      ),
    ),
    h('tv-panel', { heading: t('agentsTitle') }, h('p', { class: 'tv-muted tv-orch-agentsnote' }, t('agentsHint')), agentsHost, agentsEmpty),
  );

  const root = h(
    'div',
    { class: 'tv-skills-page tv-orch', dataset: { tvRev: '0' } },
    errorBox.el,
    h(
      'tv-panel',
      { heading: t('splitTitle'), class: 'tv-orch-head' },
      h('div', { slot: 'actions' }, range.el),
      kpis.el,
      split,
      allEmpty,
      note,
    ),
    body,
    explain,
  );
  body.hidden = true;
  container.append(root);

  // ---------- updates ----------

  function updateSplit(main, sub) {
    const total = main + sub;
    const pctMain = total ? (main / total) * 100 : 0;
    segMain.style.width = `${pctMain}%`;
    segSub.style.width = `${total ? 100 - pctMain : 0}%`;
    split.querySelector('.tv-orch-split__bar').setAttribute(
      'aria-label',
      t('splitLabel', { main: fmtInt(main), sub: fmtInt(sub) }),
    );
    legendMain.textContent = t('legendMain', { tokens: fmtTokens(main), pct: fmtPct(total ? main / total : 0) });
    legendSub.textContent = t('legendSub', { tokens: fmtTokens(sub), pct: fmtPct(total ? sub / total : 0) });
    legendMain.title = `${fmtInt(main)} ${t('tokensUnit')}`;
    legendSub.title = `${fmtInt(sub)} ${t('tokensUnit')}`;
    split.hidden = total === 0;
  }

  function updateKpis(main, sub, agents) {
    const total = main + sub;
    kpis.set('main', fmtTokens(main), `${fmtInt(main)} ${t('tokensUnit')}`);
    kpis.set('sub', fmtTokens(sub), `${fmtInt(sub)} ${t('tokensUnit')}`);
    kpis.set('share', total ? fmtPct(sub / total) : '-');
    kpis.set('sessions', fmtInt(agents.length), t('sessionsTitle'));
  }

  function updateAgents(agents, sub) {
    const rows = agents.map((a) => ({
      key: String(a.session_id ?? ''),
      name: String(a.title || '') || t('untitled'),
      sub: String(a.display_name || ''),
      value: num(a.sidechain_tokens),
      display: fmtTokens(a.sidechain_tokens),
      color: undefined,
      // Task 15 owns the `session` route (merged separately); the link is intentionally kept.
      href: `#/session/${encodeURIComponent(String(a.session_id ?? ''))}`,
    }));
    agentsEmpty.hidden = rows.length > 0;
    agentsHost.hidden = rows.length === 0;
    rankList(agentsHost, rows, { total: sub, unit: t('tokensUnit'), t });
  }

  async function updateDonuts(view, main, sub) {
    const echarts = await charts;
    if (!echarts || ctx.signal.aborted) return;
    const items = (list) =>
      (list || [])
        .filter((m) => num(m.tokens) > 0)
        .map((m) => ({ name: String(m.model || ''), value: num(m.tokens) }));
    mainEmpty.hidden = main > 0;
    subEmpty.hidden = sub > 0;
    mainDonut.parentElement.hidden = main === 0;
    subDonut.parentElement.hidden = sub === 0;
    if (main > 0) {
      donutChart(mainDonut, items(view.main_by_model), {
        label: t('mainModelsLabel'), unit: t('tokensUnit'), legend: 'bottom', signal: ctx.signal,
      });
    }
    if (sub > 0) {
      donutChart(subDonut, items(view.sidechain_by_model), {
        label: t('subModelsLabel'), unit: t('tokensUnit'), legend: 'bottom', signal: ctx.signal,
      });
    }
  }

  async function draw() {
    const rng = range.get();
    try {
      const view = await ctx.api.orchestration(rng);
      if (ctx.signal.aborted) return;
      if (rng !== range.get()) return; // superseded; a re-run is queued
      if (!view || typeof view !== 'object') throw new Error('empty response');
      errorBox.hide();
      const main = num(view.main_tokens);
      const sub = num(view.sidechain_tokens);
      const agents = Array.isArray(view.agents_by_tokens) ? view.agents_by_tokens : [];
      root.dataset.range = rng;
      allEmpty.hidden = main + sub > 0;
      body.hidden = main + sub === 0;
      kpis.el.hidden = main + sub === 0;
      updateKpis(main, sub, agents);
      updateSplit(main, sub);
      updateAgents(agents, sub);
      await updateDonuts(view, main, sub);
    } catch (err) {
      if (ctx.signal.aborted) return;
      console.error('[orchestration] load failed', err);
      errorBox.show(t('loadError', { detail: err?.message || String(err) }));
    }
    if (ctx.signal.aborted) return;
    rev += 1;
    root.dataset.tvRev = String(rev);
  }

  const refresh = coalesce(draw);
  ctx.onLive(debounce(() => refresh(), LIVE_DEBOUNCE_MS, ctx.signal));
  await refresh();
}

export function destroy() {
  disposeDetached();
}
