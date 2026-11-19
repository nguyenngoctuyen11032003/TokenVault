// Shared by the Skills and Orchestration pages (Task 16): range switch (7d / 30d / 90d / all, the
// range strings Aggregates.range_filter accepts), load-error banner, KPI strip, ranked bar list
// and day-gap filling. All data is untrusted: only textContent / attributes, never HTML.

import { colorFor } from '../palette.js';
import { clipText, fmtInt, fmtPct, h } from './homekit.js';

export const RANGES = Object.freeze(['7d', '30d', '90d', 'all']);
export const DEFAULT_RANGE = '30d';

export function createRangeBar({ t, value = DEFAULT_RANGE, onChange, label }) {
  let current = RANGES.includes(value) ? value : DEFAULT_RANGE;
  const buttons = RANGES.map((range) =>
    h(
      'button',
      {
        type: 'button',
        dataset: { range },
        'aria-pressed': String(range === current),
        on: {
          click: () => {
            if (range === current) return;
            set(range);
            onChange?.(range);
          },
        },
      },
      t(`range.${range}`),
    ),
  );
  const el = h('div', { class: 'tv-seg tv-rangeswitch', role: 'group', 'aria-label': label }, buttons);
  function set(range) {
    current = RANGES.includes(range) ? range : DEFAULT_RANGE;
    for (const b of buttons) b.setAttribute('aria-pressed', String(b.dataset.range === current));
  }
  return { el, set, get: () => current };
}

/** Error banner with a retry button; `show(message)` / `hide()`. */
export function createErrorBox({ retryText, onRetry }) {
  const text = h('span');
  const el = h(
    'div',
    { class: 'tv-loaderror', role: 'alert', 'data-load-error': '', hidden: true },
    text,
    h('button', { type: 'button', class: 'tv-btn', on: { click: onRetry } }, retryText),
  );
  return {
    el,
    show(message) {
      text.textContent = message;
      el.hidden = false;
    },
    hide() {
      el.hidden = true;
    },
  };
}

/** KPI strip: items = [{id, label, accent?}] -> one bordered row of cells; set(id, text, title). */
export function createKpiStrip(items) {
  const values = {};
  const el = h(
    'div',
    { class: 'tv-skills-kpis' },
    items.map((item) => {
      const value = h('b', { class: `tv-skills-kpi__value${item.accent ? ` tv-num--${item.accent}` : ''}` });
      values[item.id] = value;
      return h(
        'div',
        { class: 'tv-skills-kpi', dataset: { kpi: item.id } },
        h('span', { class: 'tv-skills-kpi__label' }, item.label),
        value,
      );
    }),
  );
  return {
    el,
    set(id, text, title = '') {
      const node = values[id];
      node.textContent = text;
      node.title = title;
    },
  };
}

/**
 * Ranked bar list (role=list). rows = [{key, name, value, display?, href?, sub?, color?}].
 * Bars are scaled to the largest value; each row shows swatch, ellipsised name, value + share.
 * `t('rankValue', {value, pct})` formats the right-hand text.
 */
export function rankList(host, rows, { total, unit, t }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  host.replaceChildren(
    ...rows.map((r) => {
      const color = r.color || colorFor(r.key);
      const name = r.href
        ? h('a', { class: 'tv-clip tv-skills-rank__name tv-skills-rank__link', href: r.href, title: r.name }, r.name)
        : clipText('span', 'tv-skills-rank__name', r.name);
      return h(
        'div',
        { class: 'tv-skills-rank', role: 'listitem', dataset: { key: String(r.key) } },
        h(
          'div',
          { class: 'tv-skills-rank__row' },
          h('i', { class: 'tv-skills-rank__swatch', style: { background: color }, 'aria-hidden': 'true' }),
          name,
          h(
            'span',
            { class: 'tv-skills-rank__val', title: `${fmtInt(r.value)} ${unit}` },
            t('rankValue', { value: r.display ?? fmtInt(r.value), pct: fmtPct(total ? r.value / total : 0) }),
          ),
        ),
        r.sub ? clipText('span', 'tv-skills-rank__sub', r.sub) : null,
        h(
          'span',
          { class: 'tv-bar tv-skills-rank__bar' },
          h('i', { style: { width: `${Math.max(1, (r.value / max) * 100)}%`, background: color } }),
        ),
      );
    }),
  );
}

function shiftDay(day, delta) {
  const [y, m, d] = day.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d + delta)).toISOString().slice(0, 10);
}

/**
 * Fill the gaps BETWEEN the first and last server-provided YYYY-MM-DD day with zero counts.
 * Pure string date arithmetic: the browser clock/timezone is never used (the server buckets
 * days at the Manager's UTC offset), so the axis is exactly the server's days.
 */
export function fillDays(daily) {
  const map = new Map();
  for (const d of daily || []) {
    if (/^\d{4}-\d{2}-\d{2}$/.test(String(d?.day))) {
      map.set(d.day, (map.get(d.day) || 0) + (Number(d.count) || 0));
    }
  }
  if (!map.size) return [];
  const days = [...map.keys()].sort();
  const last = days[days.length - 1];
  const out = [];
  for (let d = days[0], guard = 0; d <= last && guard < 800; d = shiftDay(d, 1), guard += 1) {
    out.push({ day: d, count: map.get(d) || 0 });
  }
  return out;
}
