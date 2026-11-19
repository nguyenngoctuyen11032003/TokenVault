// Helpers shared by the overview / projects / sessions / session pages (Task 15): date ranges
// beyond today/7d/30d, the Manager API filter built from the global filter store, error banner,
// empty state, project swatch, cost cells and a small cache of SessionRows seen on the list
// pages (the API has no "one session" method; the detail page uses it for its header).
// Everything renders untrusted data as text (h() never parses HTML).

import { colorFor } from '../palette.js';
import { clipText, fmtCostPartial, h, localDay } from './homekit.js';

/** Range presets of the overview filter bar. Home/employee pages understand the first three. */
export const OV_RANGES = Object.freeze(['today', '7d', '30d', '90d', 'all']);
const PRESET_DAYS = Object.freeze({ today: 1, '7d': 7, '30d': 30, '90d': 90 });
const CUSTOM_RE = /^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$/;
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;

/** "2026-01-01..2026-02-01" (custom range stored in filters.range) or null. */
export function customRange(range) {
  const m = CUSTOM_RE.exec(String(range ?? ''));
  if (!m) return null;
  const [start, end] = m[1] <= m[2] ? [m[1], m[2]] : [m[2], m[1]];
  return { start, end };
}

export function makeCustomRange(start, end) {
  if (!DAY_RE.test(String(start)) || !DAY_RE.test(String(end))) return null;
  return start <= end ? `${start}..${end}` : `${end}..${start}`;
}

/** Normalised range: a preset, a custom "a..b" range, or 'today' for anything else. */
export function ovRange(range) {
  if (OV_RANGES.includes(range)) return range;
  return customRange(range) ? range : 'today';
}

/** {start, end} local days for a range ('all' -> both null = no bound). */
export function rangeBounds(range) {
  const r = ovRange(range);
  if (r === 'all') return { start: null, end: null };
  const custom = customRange(r);
  if (custom) return custom;
  return { start: localDay(-(PRESET_DAYS[r] - 1)), end: localDay(0) };
}

/** Number of days a range spans (Infinity for 'all'). */
export function rangeDays(range) {
  const r = ovRange(range);
  if (r === 'all') return Infinity;
  const custom = customRange(r);
  if (!custom) return PRESET_DAYS[r];
  const ms = new Date(`${custom.end}T00:00:00`) - new Date(`${custom.start}T00:00:00`);
  return Math.round(ms / 86_400_000) + 1;
}

/** Manager `Filter` from the global filter state. `omit` drops keys (e.g. ['project']). */
export function ovFilter(state, omit = []) {
  const s = state || {};
  const out = {
    team: s.team ?? null,
    email: s.email ?? null,
    project: s.project ?? null,
    model: s.model ?? null,
    ...rangeBounds(s.range),
  };
  for (const key of omit) out[key] = null;
  return out;
}

/** True when a filter narrows beyond team/range (used by empty states to offer "clear"). */
export function hasNarrowing(state) {
  const s = state || {};
  return !!(s.team || s.email || s.project || s.model);
}

/** Filter keys reset by "clear filters" (the range is kept). */
export const CLEAR_PATCH = Object.freeze({ team: null, email: null, project: null, model: null });

/** Error banner with a retry button. Returns {el, show(err), hide()}. */
export function errorBanner(text, retryText, onRetry) {
  const msg = h('span');
  const el = h(
    'div',
    { class: 'tv-loaderror', role: 'alert', 'data-load-error': '', hidden: true },
    msg,
    h('button', { type: 'button', class: 'tv-btn', on: { click: () => onRetry() } }, retryText),
  );
  return {
    el,
    show(err) {
      msg.textContent = text(err?.message || String(err));
      el.hidden = false;
    },
    hide() {
      el.hidden = true;
    },
  };
}

/** Empty-state block: mark, lead text, hint and an optional action button. */
export function emptyState({ lead, hint, action, onAction }) {
  const hintEl = h('p', { class: 'tv-ovempty__hint' }, hint || '');
  const button = action
    ? h('button', { type: 'button', class: 'tv-btn tv-btn--primary', on: { click: () => onAction?.() } }, action)
    : null;
  const el = h(
    'div',
    { class: 'tv-ovempty', 'data-empty-state': '', role: 'status', hidden: true },
    h('div', { class: 'tv-ovempty__mark', 'aria-hidden': 'true' }),
    h('div', { class: 'tv-ovempty__text' }, h('p', { class: 'tv-ovempty__lead' }, lead), hintEl, button),
  );
  return {
    el,
    set(visible, { hintText, showAction = true } = {}) {
      el.hidden = !visible;
      if (hintText !== undefined) hintEl.textContent = hintText;
      if (button) button.hidden = !showAction;
    },
  };
}

/** Coloured swatch + clipped name for a palette key (project, model...). */
export function swatchName(key, label, cls = '') {
  const k = String(key ?? '');
  return h(
    'span',
    { class: `tv-ov-name ${cls}`.trim() },
    h('i', { class: 'tv-ov-swatch', dataset: { key: k }, style: { background: colorFor(k) }, 'aria-hidden': 'true' }),
    clipText('span', 'tv-ov-name__text', label ?? k),
  );
}

/** Display label for a model ("" -> "(no model)"). */
export function modelLabel(model, noModel) {
  const m = String(model ?? '').trim();
  return m || noModel;
}

/** Cost cell text + class: UNKNOWN (never 0) for null, "+" for partial. */
export function costCell(cost, partial, unknownText) {
  const known = cost !== null && cost !== undefined && Number.isFinite(Number(cost));
  return {
    text: fmtCostPartial(cost, partial, unknownText),
    cls: known ? 'tv-num--orange' : 'tv-ov-unknown',
    known,
  };
}

/** href of a session detail page (id URL-encoded). */
export function sessionHref(id) {
  return `#/session/${encodeURIComponent(String(id ?? ''))}`;
}

// ---------- session row cache (detail header) ----------

const SESSION_CACHE_MAX = 5000;
const sessionCache = new Map();

export function rememberSessions(rows) {
  for (const row of Array.isArray(rows) ? rows : []) {
    if (!row || typeof row.session_id !== 'string') continue;
    sessionCache.delete(row.session_id);
    sessionCache.set(row.session_id, row);
  }
  while (sessionCache.size > SESSION_CACHE_MAX) {
    sessionCache.delete(sessionCache.keys().next().value);
  }
}

export function cachedSession(id) {
  return sessionCache.get(String(id)) || null;
}
