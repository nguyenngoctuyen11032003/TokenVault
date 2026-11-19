// #/session/<id> — one session, turn by turn. Header: title, project, employee, models, time
// span and totals (tokens, API-equivalent cost, turns, cache, sub-agent tokens). Body: stacked
// bar chart of tokens per turn (downsampled for long sessions; sub-agent turns as their own
// series) and a paginated, time-ordered turn table with sidechain turns marked.
// The API has no "one session" method: the header uses the SessionRow seen on the list pages
// (cache) or, failing that, api.sessions() over all time; it falls back to what the turns say.

import { disposeDetached, loadECharts } from '../charts.js';
import {
  cachedSession,
  costCell,
  errorBanner,
  modelLabel,
  rememberSessions,
  swatchName,
} from '../components/overview-kit.js';
import { cacheWrite, createTurnsTable, drawTurnsChart, orderTurns } from '../components/sessions-turns.js';
import {
  clipText,
  coalesce,
  debounce,
  fmtInt,
  fmtTokens,
  h,
  LIVE_DEBOUNCE_MS,
  shortTime,
} from '../components/homekit.js';
import { colorFor } from '../palette.js';

const ALL_TIME = { team: null, email: null, project: null, model: null, start: null, end: null };

export default async function render(container, ctx) {
  const t = (key, params) => ctx.t(`session.${key}`, params);
  const id = String(ctx.args[0] ?? '');
  await Promise.all([
    ctx.useStylesheet('css/pages/people.css'),
    ctx.useStylesheet('css/pages/overview-shared.css'),
    ctx.useStylesheet('css/pages/sessions.css'),
  ]);
  if (ctx.signal.aborted) return;
  const charts = loadECharts().catch((err) => {
    console.error('[session] charts unavailable', err);
    return null;
  });

  const unknown = t('unknownCost');
  const noModel = t('noModel');
  let rev = 0;
  let info = cachedSession(id);
  let lookedUp = !!info;

  // ---------- layout ----------
  const title = h('p', { class: 'tv-session-title' });
  const facts = h('dl', { class: 'tv-session-facts' });
  const kpis = {};
  const kpi = (key, cls = '') => {
    kpis[key] = h('b', { class: `tv-session-kpi__value ${cls}`.trim() });
    return h('div', { class: 'tv-session-kpi', dataset: { kpi: key } }, h('span', { class: 'tv-session-kpi__label' }, t(`kpi.${key}`)), kpis[key]);
  };
  const head = h(
    'tv-panel',
    { heading: t('heading'), class: 'tv-session-head' },
    h('a', { slot: 'actions', class: 'tv-session-back', href: '#/sessions' }, t('back')),
    h(
      'div',
      { class: 'tv-session-hero' },
      h('div', { class: 'tv-session-main' }, title, facts),
      h(
        'div',
        { class: 'tv-session-kpis' },
        kpi('tokens', 'tv-session-kpi__value--tokens'),
        kpi('cost'),
        kpi('turns'),
        kpi('io'),
        kpi('cache'),
        kpi('side', 'tv-session-kpi__value--side'),
      ),
    ),
    h('p', { class: 'tv-costnote', 'data-cost-note': '' }, t('costNote')),
  );
  const chartHost = h('div', { class: 'tv-session-chart', dataset: { chart: 'turns' } });
  const chartMeta = h('span', { class: 'tv-ov-meta' });
  const turnsTable = createTurnsTable({ t, unknownText: unknown, noModel });
  const noTurns = h('p', { class: 'tv-muted', hidden: true }, t('noTurns'));
  const body = h(
    'div',
    { class: 'tv-session-body' },
    h(
      'tv-panel',
      { heading: t('chart.title'), class: 'tv-session-chartpanel' },
      h('div', { class: 'tv-meta' }, h('span', null, t('chart.sub')), chartMeta),
      h('div', { class: 'tv-chart-area tv-session-chartbox' }, chartHost),
    ),
    h('tv-panel', { heading: t('turnsTitle'), class: 'tv-session-turnspanel' }, turnsTable.el, noTurns),
  );
  body.style.display = 'contents';
  head.hidden = true;
  body.hidden = true;
  const error = errorBanner((detail) => t('loadError', { detail }), t('retry'), () => refresh());
  const root = h('div', { class: 'tv-session', dataset: { tvRev: '0' } }, error.el, head, body);
  container.append(root);

  // ---------- header ----------

  function fact(label, value) {
    return [h('dt', null, label), h('dd', null, value)];
  }

  function summarize(turns) {
    const sum = { total: 0, input: 0, output: 0, read: 0, write: 0, side: 0, cost: 0, priced: 0, unpriced: 0 };
    const models = [];
    for (const x of turns) {
      sum.total += Number(x.total_tokens) || 0;
      sum.input += Number(x.input_tokens) || 0;
      sum.output += Number(x.output_tokens) || 0;
      sum.read += Number(x.cache_read_tokens) || 0;
      sum.write += cacheWrite(x);
      if (x.is_sidechain) sum.side += Number(x.total_tokens) || 0;
      if (x.cost === null || x.cost === undefined) sum.unpriced += 1;
      else {
        sum.cost += Number(x.cost) || 0;
        sum.priced += 1;
      }
      const m = modelLabel(x.model, noModel);
      if (!models.includes(m)) models.push(m);
    }
    return { sum, models };
  }

  function updateHead(turns) {
    const { sum, models } = summarize(turns);
    const row = info;
    const name = row?.title ? String(row.title) : t('untitled');
    title.textContent = name;
    title.title = name;
    title.classList.toggle('tv-ov-untitled', !row?.title);
    const allModels = row?.models?.length ? row.models.map((m) => modelLabel(m, noModel)) : models;
    const started = turns[0]?.timestamp || row?.started_at;
    const ended = turns[turns.length - 1]?.timestamp || row?.last_at;
    facts.replaceChildren(
      ...(row ? fact(t('fact.project'), swatchName(row.project, row.project || t('noProject'))) : []),
      ...(row ? fact(t('fact.employee'), clipText('span', '', row.display_name || row.employee_key || '')) : []),
      ...fact(
        t('fact.models'),
        h(
          'span',
          { class: 'tv-session-models' },
          allModels.map((m) =>
            h('span', { class: 'tv-session-chip', title: m }, h('i', { class: 'tv-ov-swatch', dataset: { key: m }, style: { background: colorFor(m) }, 'aria-hidden': 'true' }), clipText('span', '', m)),
          ),
          allModels.length ? null : t('noModel'),
        ),
      ),
      ...fact(t('fact.time'), started ? t('timeSpan', { start: shortTime(started), end: shortTime(ended) }) : '—'),
      ...(row ? [] : fact(t('fact.note'), t('noRow'))),
      ...fact(t('fact.id'), clipText('span', 'tv-session-id', id)),
    );

    // Totals from the turns (the whole session; stays right while a live session grows).
    kpis.tokens.textContent = fmtTokens(sum.total);
    kpis.tokens.title = `${fmtInt(sum.total)} ${t('tokensUnit')}`;
    const cost = costCell(sum.priced ? sum.cost : turns.length ? null : 0, sum.priced > 0 && sum.unpriced > 0, unknown);
    kpis.cost.textContent = cost.text;
    kpis.cost.className = `tv-session-kpi__value ${cost.known ? 'tv-num--orange' : 'tv-ov-unknown'}`;
    kpis.turns.textContent = fmtInt(turns.length);
    kpis.io.textContent = `${fmtTokens(sum.input)} / ${fmtTokens(sum.output)}`;
    kpis.io.title = `${fmtInt(sum.input)} / ${fmtInt(sum.output)}`;
    kpis.cache.textContent = `${fmtTokens(sum.read)} / ${fmtTokens(sum.write)}`;
    kpis.cache.title = `${fmtInt(sum.read)} / ${fmtInt(sum.write)}`;
    kpis.side.textContent = fmtTokens(sum.side);
    kpis.side.title = fmtInt(sum.side);
  }

  async function lookupRow() {
    if (lookedUp) return;
    try {
      const rows = await ctx.api.sessions(ALL_TIME);
      if (Array.isArray(rows)) {
        rememberSessions(rows);
        info = cachedSession(id);
      }
      lookedUp = true;
    } catch (err) {
      console.warn('[session] sessions() lookup failed', err);
    }
  }

  async function draw() {
    try {
      const [turnsRaw] = await Promise.all([ctx.api.session_turns(id), lookupRow()]);
      if (ctx.signal.aborted) return;
      if (!Array.isArray(turnsRaw)) throw new Error(t('badResponse'));
      error.hide();
      head.hidden = false;
      body.hidden = false;
      const turns = orderTurns(turnsRaw);
      updateHead(turns);
      noTurns.hidden = turns.length > 0;
      turnsTable.setTurns(turns);
      const echarts = await charts;
      if (echarts && !ctx.signal.aborted) {
        const b = drawTurnsChart(chartHost, turns, { t, signal: ctx.signal });
        chartHost.hidden = !b;
        chartMeta.textContent = b && b.size > 1 ? t('chart.bucket', { n: fmtInt(b.size) }) : '';
      }
    } catch (err) {
      if (ctx.signal.aborted) return;
      console.error('[session] load failed', err);
      error.show(err);
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
