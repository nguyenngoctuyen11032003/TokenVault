// Shared helpers for the home / employees / employee pages: safe DOM building, number and cost
// formatting, date ranges and API filters. Data is untrusted: h() only ever sets textContent
// and attributes, never HTML.

import { compactNumber } from '../dom.js';

/**
 * h('div', {class: 'x', title: name, dataset: {key}, on: {click}}, child, 'text', ...)
 * Children: Node, string/number (text), arrays, null/false (skipped).
 */
export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (name === 'dataset') Object.assign(el.dataset, value);
    else if (name === 'on') for (const [ev, fn] of Object.entries(value)) el.addEventListener(ev, fn);
    else if (name === 'style' && typeof value === 'object') {
      for (const [prop, v] of Object.entries(value)) el.style.setProperty(prop, v);
    } else el.setAttribute(name, value === true ? '' : String(value));
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) append(el, child);
    else el.append(child instanceof Node ? child : String(child));
  }
}

/** Text element truncated with an ellipsis; the full text is kept in the tooltip. */
export function clipText(tag, cls, text) {
  const value = String(text ?? '');
  return h(tag, { class: `tv-clip ${cls || ''}`.trim(), title: value }, value);
}

/** 4820000 -> "4.82M"; null/undefined -> "—". */
export function fmtTokens(value) {
  return compactNumber(value ?? 0) ?? '0';
}

const intFmt = new Intl.NumberFormat('vi-VN');
/** Full integer with Vietnamese grouping: 1234567 -> "1.234.567". */
export function fmtInt(value) {
  const n = Number(value);
  return Number.isFinite(n) ? intFmt.format(Math.round(n)) : '';
}

/**
 * USD API-equivalent estimate. `null` (unpriced) -> the UNKNOWN text, never 0.
 * 61.4 -> "$61.40", 0.7 -> "$0.70", 1234 -> "$1.23K".
 */
export function fmtCost(value, unknownText) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return unknownText;
  const n = Number(value);
  if (Math.abs(n) >= 1000) return `$${compactNumber(n)}`;
  if (n > 0 && n < 0.01) return '<$0.01';
  return `$${n.toFixed(2)}`;
}

/** Cost text with a "+" suffix when part of the tokens has no price (cost is a lower bound). */
export function fmtCostPartial(value, partial, unknownText) {
  const text = fmtCost(value, unknownText);
  return partial && text !== unknownText ? `${text}+` : text;
}

/** Percentage 0..1 -> "62%" (or "<1%"). */
export function fmtPct(fraction) {
  const p = Number(fraction) * 100;
  if (!Number.isFinite(p) || p <= 0) return '0%';
  if (p < 1) return '<1%';
  return `${Math.round(p)}%`;
}

/** Round up to a "nice" gauge maximum (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8 x 10^n). */
export function niceCeil(value) {
  const v = Number(value);
  if (!Number.isFinite(v) || v <= 0) return 1;
  const exp = 10 ** Math.floor(Math.log10(v));
  for (const step of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) {
    if (step * exp >= v) return step * exp;
  }
  return 10 * exp;
}

function pad(n) {
  return String(n).padStart(2, '0');
}

/** Local calendar day "YYYY-MM-DD", `offsetDays` from today. */
export function localDay(offsetDays = 0, now = new Date()) {
  const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + offsetDays);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** Local "YYYY-MM-DDTHH:00" of the current hour (matches HourPoint.hour). */
export function localHour(now = new Date()) {
  return `${localDay(0, now)}T${pad(now.getHours())}:00`;
}

export const RANGES = Object.freeze(['today', '7d', '30d']);
const RANGE_DAYS = { today: 1, '7d': 7, '30d': 30 };

export function normalizeRange(range) {
  return RANGES.includes(range) ? range : 'today';
}

/** API filter (Manager `Filter` fields only) for a range of whole local days. */
export function apiFilter({ team = null, email = null, project = null, model = null } = {}, range = 'today') {
  const days = RANGE_DAYS[normalizeRange(range)];
  return { team, email, project, model, start: localDay(-(days - 1)), end: localDay(0) };
}

/** "2026-11-19" -> "19/11"; "2026-11-19T10:00" -> "10:00". */
export function shortDay(day) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(day ?? ''));
  return m ? `${m[3]}/${m[2]}` : String(day ?? '');
}

export function shortHour(hour) {
  const m = /T(\d{2}):(\d{2})/.exec(String(hour ?? ''));
  return m ? `${m[1]}:${m[2]}` : String(hour ?? '');
}

/** "2026-11-19T09:10:00+07:00" -> "19/11 09:10". */
export function shortTime(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(String(iso ?? ''));
  return m ? `${m[3]}/${m[2]} ${m[4]}:${m[5]}` : '';
}

const COMBINING_MARKS = new RegExp(`[${String.fromCharCode(0x300)}-${String.fromCharCode(0x36f)}]`, 'g');
const D_STROKE = new RegExp(`[${String.fromCharCode(0x110)}${String.fromCharCode(0x111)}]`, 'g');

/** Lowercase, accent-free text for search (Vietnamese "Ke toan" with marks -> "ke toan"). */
export function fold(text) {
  return String(text ?? '')
    .normalize('NFD')
    .replace(COMBINING_MARKS, '')
    .replace(D_STROKE, 'd')
    .toLowerCase();
}

/**
 * Run `task()` serially, coalescing calls made while one is running into a single re-run.
 * Returns a function; each call resolves when a run that started after it finished.
 */
export function coalesce(task) {
  let running = null;
  let again = false;
  const run = async () => {
    do {
      again = false;
      await task();
    } while (again);
  };
  return () => {
    if (running) {
      again = true;
      return running;
    }
    running = run().finally(() => {
      running = null;
    });
    return running;
  };
}
