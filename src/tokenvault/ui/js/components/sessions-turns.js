// Turn-by-turn view for #/session/<id> (Task 15): time-ordered turns as a downsampled stacked
// bar chart (input / output / cache read / cache write per bucket; sub-agent turns as their own
// series) and a paginated table (PAGE_SIZE rows, first/prev/next/last). Sessions with thousands
// of turns stay smooth: at most MAX_BARS bars and PAGE_SIZE rows are ever in the DOM.
// t is scoped to the `session.*` namespace.

import { dispose, stackedBarChart } from '../charts.js';
import { colorFor } from '../palette.js';
import { clipText, fmtInt, fmtTokens, h } from './homekit.js';
import { KIND_COLORS } from './overview-charts.js';
import { costCell, modelLabel } from './overview-kit.js';

export const PAGE_SIZE = 100;
export const MAX_BARS = 240;
const COLUMNS = ['n', 'time', 'model', 'input', 'output', 'cache_read', 'cache_write', 'total', 'cost', 'tools'];
const NUMERIC = new Set(['input', 'output', 'cache_read', 'cache_write', 'total', 'cost']);

const num = (v) => Number(v) || 0;
export const cacheWrite = (x) => num(x.cache_create_5m_tokens) + num(x.cache_create_1h_tokens);

/** Turns sorted by instant (stable for equal timestamps; unparsable ones keep their place last). */
export function orderTurns(turns) {
  return (Array.isArray(turns) ? turns : [])
    .filter((x) => x && typeof x === 'object')
    .map((x, i) => {
      const ms = Date.parse(String(x.timestamp ?? ''));
      return { x, i, ms: Number.isFinite(ms) ? ms : Infinity };
    })
    .sort((a, b) => (a.ms < b.ms ? -1 : a.ms > b.ms ? 1 : a.i - b.i))
    .map((e) => e.x);
}

/** Sum turns into at most MAX_BARS buckets. Returns {size, categories, series values}. */
export function bucketTurns(turns) {
  const n = turns.length;
  const size = Math.max(1, Math.ceil(n / MAX_BARS));
  const count = Math.ceil(n / size);
  const out = {
    size,
    categories: new Array(count),
    input: new Array(count).fill(0),
    output: new Array(count).fill(0),
    cacheRead: new Array(count).fill(0),
    cacheWrite: new Array(count).fill(0),
    sidechain: new Array(count).fill(0),
  };
  for (let b = 0; b < count; b += 1) {
    const from = b * size;
    const to = Math.min(n, from + size);
    out.categories[b] = size === 1 ? `#${from + 1}` : `#${from + 1}–${to}`;
    for (let i = from; i < to; i += 1) {
      const x = turns[i];
      if (x.is_sidechain) {
        out.sidechain[b] += num(x.total_tokens);
      } else {
        out.input[b] += num(x.input_tokens);
        out.output[b] += num(x.output_tokens);
        out.cacheRead[b] += num(x.cache_read_tokens);
        out.cacheWrite[b] += cacheWrite(x);
      }
    }
  }
  return out;
}

export function drawTurnsChart(host, turns, { t, signal }) {
  if (!turns.length) {
    dispose(host);
    return null;
  }
  const b = bucketTurns(turns);
  const series = [
    { name: t('kind.input'), values: b.input, color: KIND_COLORS.input },
    { name: t('kind.output'), values: b.output, color: KIND_COLORS.output },
    { name: t('kind.cacheRead'), values: b.cacheRead, color: KIND_COLORS.cacheRead },
    { name: t('kind.cacheWrite'), values: b.cacheWrite, color: KIND_COLORS.cacheWrite },
  ];
  if (b.sidechain.some((v) => v > 0)) {
    series.push({ name: t('kind.sidechain'), values: b.sidechain, color: KIND_COLORS.sidechain });
  }
  stackedBarChart(host, { categories: b.categories, series }, {
    label: t('chart.label'),
    unit: t('tokensUnit'),
    signal,
    animation: turns.length <= MAX_BARS,
  });
  return b;
}

const pad = (n) => String(n).padStart(2, '0');

/**
 * A turn timestamp is a UTC instant (ISO-8601 with Z or an offset); show it in browser-local
 * time. Returns {ms, day, time, short, full}; empty strings when it cannot be parsed.
 */
export function localTime(ts) {
  const ms = Date.parse(String(ts ?? ''));
  if (!Number.isFinite(ms)) return { ms: NaN, day: '', time: '', short: '', full: '' };
  const d = new Date(ms);
  const day = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  const dm = `${pad(d.getDate())}/${pad(d.getMonth() + 1)}`;
  return { ms, day, time, short: `${dm} ${time.slice(0, 5)}`, full: `${dm}/${d.getFullYear()} ${time}` };
}

/** Paginated turns table. Returns {el, setTurns(ordered), page()}. */
export function createTurnsTable({ t, unknownText, noModel, onPage }) {
  let turns = [];
  let page = 0;
  let multiDay = false;

  const body = h('tbody');
  const table = h(
    'table',
    { class: 'tv-table tv-turntable' },
    h('colgroup', null, COLUMNS.map((c) => h('col', { class: `tv-turntable__col--${c}` }))),
    h(
      'thead',
      null,
      h('tr', null, COLUMNS.map((c) => h('th', { scope: 'col', class: NUMERIC.has(c) ? 'tv-num' : null, title: c === 'cost' ? t('costNote') : null }, t(`col.${c}`)))),
    ),
    body,
  );
  const info = h('span', { class: 'tv-session-pager__info' });
  const btn = (key, attr, go) =>
    h('button', { type: 'button', class: 'tv-btn', [attr]: '', on: { click: () => setPage(go()) } }, t(`pager.${key}`));
  const first = btn('first', 'data-page-first', () => 0);
  const prev = btn('prev', 'data-page-prev', () => page - 1);
  const next = btn('next', 'data-page-next', () => page + 1);
  const last = btn('last', 'data-page-last', () => pages() - 1);
  const pager = h('div', { class: 'tv-session-pager' }, info, first, prev, next, last);
  const el = h('div', { class: 'tv-session-turns' }, h('div', { class: 'tv-session-tablewrap' }, table), pager);

  const pages = () => Math.max(1, Math.ceil(turns.length / PAGE_SIZE));

  function row(x, index) {
    const side = !!x.is_sidechain;
    const model = modelLabel(x.model, noModel);
    const cost = costCell(x.cost, false, unknownText);
    const tools = (x.tool_names || []).map(String).join(', ');
    const lt = localTime(x.timestamp);
    const nCell = h('td', { class: 'tv-session-n', dataset: { col: 'n' } }, String(index + 1));
    if (side) nCell.append(h('span', { class: 'tv-session-badge', title: t('sidechainHint') }, t('sidechain')));
    return h(
      'tr',
      {
        dataset: { turn: String(index + 1), ts: String(x.timestamp || '') },
        'data-sidechain': side ? '' : null,
      },
      nCell,
      h('td', { class: 'tv-ov-time', dataset: { col: 'time' }, title: lt.full }, multiDay ? `${lt.short.slice(0, 5)} ${lt.time}` : lt.time),
      h(
        'td',
        { dataset: { col: 'model' } },
        h('span', { class: 'tv-ov-name' }, h('i', { class: 'tv-ov-swatch', dataset: { key: model }, style: { background: colorFor(model) }, 'aria-hidden': 'true' }), clipText('span', 'tv-ov-name__text', model)),
      ),
      h('td', { class: 'tv-num', title: fmtInt(x.input_tokens) }, fmtTokens(x.input_tokens)),
      h('td', { class: 'tv-num', title: fmtInt(x.output_tokens) }, fmtTokens(x.output_tokens)),
      h('td', { class: 'tv-num', title: fmtInt(x.cache_read_tokens) }, fmtTokens(x.cache_read_tokens)),
      h('td', { class: 'tv-num', title: fmtInt(cacheWrite(x)) }, fmtTokens(cacheWrite(x))),
      h('td', { class: 'tv-num tv-num--hot', title: fmtInt(x.total_tokens) }, fmtTokens(x.total_tokens)),
      h('td', { class: `tv-num ${cost.cls}`, dataset: { col: 'cost' } }, cost.text),
      h('td', { dataset: { col: 'tools' } }, clipText('span', 'tv-session-tools', tools)),
    );
  }

  function setPage(p) {
    page = Math.min(Math.max(0, p), pages() - 1);
    const from = page * PAGE_SIZE;
    const to = Math.min(turns.length, from + PAGE_SIZE);
    body.replaceChildren(...turns.slice(from, to).map((x, i) => row(x, from + i)));
    info.textContent = turns.length
      ? t('pager.info', { from: fmtInt(from + 1), to: fmtInt(to), total: fmtInt(turns.length) })
      : t('noTurns');
    first.disabled = prev.disabled = page === 0;
    next.disabled = last.disabled = page >= pages() - 1;
    pager.hidden = !turns.length;
    onPage?.(page);
  }

  return {
    el,
    setTurns(ordered) {
      turns = ordered;
      const days = new Set(turns.map((x) => localTime(x.timestamp).day));
      multiDay = days.size > 1;
      setPage(Math.min(page, pages() - 1));
    },
    page: () => page,
  };
}
