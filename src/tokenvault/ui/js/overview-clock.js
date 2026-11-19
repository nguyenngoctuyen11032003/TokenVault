// The ONLY place the Task 15 pages read the browser clock.
//
// Contract ("Time zones"): the UI runs on the Manager's machine, so browser-local time equals the
// server's bucketing offset. The browser clock is therefore used only for the CURRENT local day
// and hour (range bounds, masking future hours of today, the date-input max). Every axis label
// and series bucket comes from the server's strings verbatim; nothing here builds buckets.
// Day arithmetic works on 'YYYY-MM-DD' components (UTC-based epoch-day maths), never on
// `new Date('YYYY-MM-DDT00:00:00')`, so DST and time-zone parsing cannot shift a day.

const DAY_RE = /^(\d{4})-(\d{2})-(\d{2})$/;
const pad = (n) => String(n).padStart(2, '0');

/** Current local day 'YYYY-MM-DD'. */
export function today(now = new Date()) {
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** Current local hour 'YYYY-MM-DDTHH:00' (same shape as HourPoint.hour). */
export function currentHour(now = new Date()) {
  return `${today(now)}T${pad(now.getHours())}:00`;
}

/** 'YYYY-MM-DD' -> {y, m, d, epochDay} or null (validated by component arithmetic). */
export function parseLocalDay(text) {
  const m = DAY_RE.exec(String(text ?? ''));
  if (!m) return null;
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const d = Number(m[3]);
  const ms = Date.UTC(y, mo - 1, d);
  const check = new Date(ms);
  if (check.getUTCFullYear() !== y || check.getUTCMonth() !== mo - 1 || check.getUTCDate() !== d) {
    return null;
  }
  return { y, m: mo, d, epochDay: Math.round(ms / 86_400_000) };
}

/** 'YYYY-MM-DD' shifted by n days (component arithmetic). */
export function addDays(day, n) {
  const p = parseLocalDay(day);
  if (!p) return null;
  const dt = new Date((p.epochDay + n) * 86_400_000);
  return `${dt.getUTCFullYear()}-${pad(dt.getUTCMonth() + 1)}-${pad(dt.getUTCDate())}`;
}

/** Inclusive number of days from start to end (both 'YYYY-MM-DD'), or null. */
export function daySpan(start, end) {
  const a = parseLocalDay(start);
  const b = parseLocalDay(end);
  return a && b ? Math.abs(b.epochDay - a.epochDay) + 1 : null;
}

export const PRESET_DAYS = Object.freeze({ today: 1, '7d': 7, '30d': 30, '90d': 90 });
export const RANGE_PRESETS = Object.freeze(['today', '7d', '30d', '90d', 'all']);
const CUSTOM_RE = /^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$/;

/** Custom range "a..b" -> {start, end} (ordered, both valid days) or null. */
export function customRange(range) {
  const m = CUSTOM_RE.exec(String(range ?? ''));
  if (!m || !parseLocalDay(m[1]) || !parseLocalDay(m[2])) return null;
  return m[1] <= m[2] ? { start: m[1], end: m[2] } : { start: m[2], end: m[1] };
}

/** {start, end} local days for a range; 'all' -> {null, null}; unknown -> today. */
export function rangeBounds(range, now = new Date()) {
  if (range === 'all') return { start: null, end: null };
  const custom = customRange(range);
  if (custom) return custom;
  const days = PRESET_DAYS[range] ?? 1;
  const end = today(now);
  return { start: addDays(end, -(days - 1)), end };
}
