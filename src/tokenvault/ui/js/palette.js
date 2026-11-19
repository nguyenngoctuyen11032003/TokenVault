// Categorical data palette shared by every chart and page.
//
//   colorFor('erp-api')   -> '#2dd4bf'  (same key -> same colour everywhere, every visit)
//   SERIES_COLORS         -> the 8 series colours (= CSS tokens --tv-series-1..8 in theme.css)
//   contrastRatio(a, b)   -> WCAG 2.x contrast ratio of two #rrggbb colours
//   ensureLegible(c, bg)  -> c, or c lightened until it reaches 4.5:1 on bg
//
// Assignment (what is guaranteed, honestly):
// - Every key has a home slot: FNV-1a hash of the key mod 8. Same key -> same home slot on any
//   machine, any launch.
// - With working localStorage (pywebview needs private_mode=False + a storage_path, Task 12):
//   a new key takes its home slot unless that colour is already used by more keys than another
//   one; then it takes the least-used colour, probing from its home slot. The choice is
//   persisted, so the first 8 distinct keys get 8 distinct colours and every key keeps its
//   colour across launches. Persisted choices win over the hash.
// - Without storage (private mode, blocked, or the API throws): every key uses its home slot.
//   Colours are then stable across launches by hash alone, but two keys may share a colour
//   (no distinctness guarantee).
// - Corrupt stored JSON is ignored (never throws) and overwritten by the next assignment.
// Never rely on colour alone: charts and tables always show the name next to the swatch.

export const SERIES_COLORS = Object.freeze([
  '#ff4d5e', // red (lighter than the accent so a series never reads as the "primary" line)
  '#ffb020', // amber
  '#2dd4bf', // teal
  '#4da3ff', // blue
  '#a78bfa', // violet
  '#7ee787', // green
  '#f472b6', // pink
  '#94a3b8', // slate
]);

const STORAGE_KEY = 'tv.palette.v1';
const MAX_KEYS = 2000;

let assigned = null; // Map key -> index (memo for this page load)
let persistent = false; // localStorage usable: assignments are remembered across launches
const counts = new Array(SERIES_COLORS.length).fill(0);

function storageWorks() {
  try {
    const probe = `${STORAGE_KEY}.probe`;
    localStorage.setItem(probe, '1');
    localStorage.removeItem(probe);
    return true;
  } catch {
    return false;
  }
}

function load() {
  if (assigned) return assigned;
  assigned = new Map();
  persistent = storageWorks();
  if (!persistent) return assigned;
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
    if (Array.isArray(raw)) {
      for (const entry of raw) {
        if (!Array.isArray(entry) || typeof entry[0] !== 'string') continue;
        const index = Number(entry[1]);
        if (!Number.isInteger(index) || index < 0 || index >= SERIES_COLORS.length) continue;
        if (assigned.has(entry[0])) continue;
        assigned.set(entry[0], index);
        counts[index] += 1;
      }
    }
  } catch {
    /* corrupt JSON: start fresh (overwritten on the next save) */
  }
  return assigned;
}

function save() {
  try {
    const entries = [...assigned].slice(-MAX_KEYS);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
  } catch {
    persistent = false; // quota or revoked: fall back to hash slots from now on
  }
}

/** FNV-1a 32-bit hash of the UTF-16 code units of `text`. */
function hash(text) {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** Stable series colour for a key (project, model, employee, tool, team...). */
export function colorFor(key) {
  const text = String(key ?? '');
  const map = load();
  const known = map.get(text);
  if (known !== undefined) return SERIES_COLORS[known];
  const n = SERIES_COLORS.length;
  const home = hash(text) % n;
  let index = home;
  if (persistent) {
    const least = Math.min(...counts);
    for (let step = 0; step < n; step += 1) {
      const candidate = (home + step) % n;
      if (counts[candidate] === least) {
        index = candidate;
        break;
      }
    }
    counts[index] += 1;
  }
  map.set(text, index);
  if (persistent) save();
  return SERIES_COLORS[index];
}

function parseHex(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex).trim());
  if (!m) throw new Error(`not a #rrggbb colour: ${hex}`);
  const v = Number.parseInt(m[1], 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}

function toHex(rgb) {
  return `#${rgb.map((c) => Math.round(c).toString(16).padStart(2, '0')).join('')}`;
}

function luminance(rgb) {
  const [r, g, b] = rgb.map((v) => {
    const c = v / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG contrast ratio (1..21) between two #rrggbb colours. */
export function contrastRatio(hexA, hexB) {
  const a = luminance(parseHex(hexA));
  const b = luminance(parseHex(hexB));
  const [hi, lo] = a >= b ? [a, b] : [b, a];
  return (hi + 0.05) / (lo + 0.05);
}

/** `hex` if it already has `min` contrast on `bg`, else the closest lighter mix that does. */
export function ensureLegible(hex, bg, min = 4.5) {
  if (contrastRatio(hex, bg) >= min) return toHex(parseHex(hex));
  const base = parseHex(hex);
  for (let t = 0.05; t <= 1.0001; t += 0.05) {
    const mixed = base.map((c) => c + (255 - c) * t);
    if (contrastRatio(toHex(mixed), bg) >= min) return toHex(mixed);
  }
  return '#ffffff';
}
