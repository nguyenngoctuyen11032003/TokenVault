// Shared helpers for the installer / settings / diagnostics pages (Task 17): validation of
// team names and host names, error banners with retry, clipboard copy with feedback, local
// date-time formatting and fingerprint formatting. Data is untrusted: everything goes through
// h() (textContent / attributes only).

import { h } from './homekit.js';
import { errorDetail } from './installer-errors.js';

export { clipText, coalesce, debounce, h, LIVE_DEBOUNCE_MS } from './homekit.js';

export const TEAM_MAX = 40;
const CONTROL = /[\u0000-\u001f\u007f-\u009f]/u;
const SEPARATOR = /[\\/]/;
const INVALID = /[<>:"|?*]/;
const RESERVED = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i;

/**
 * Validate a team name typed by the owner. It ends up in a Windows file name
 * (TokenVault-Adapter-<team>.exe), so path separators, Windows-invalid characters and control
 * characters are refused. Vietnamese letters, spaces, '-', '_', '.', '&', '(' ')' are fine.
 * Returns {ok: true, value} (trimmed, NFC) or {ok: false, reason}.
 */
export function validateTeam(raw) {
  const value = String(raw ?? '').normalize('NFC').trim();
  if (!value) return { ok: false, reason: 'empty' };
  if (CONTROL.test(value)) return { ok: false, reason: 'control' };
  if (SEPARATOR.test(value)) return { ok: false, reason: 'separator' };
  if (INVALID.test(value) || RESERVED.test(value) || /^\.+$/.test(value) || value.endsWith('.')) {
    return { ok: false, reason: 'invalid' };
  }
  if ([...value].length > TEAM_MAX) return { ok: false, reason: 'tooLong' };
  return { ok: true, value };
}

export const HOSTS_MAX = 8;
const HOST_LEN_MAX = 253;
const LABEL_RE = /^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/;
const OCTET_RE = /^(0|[1-9][0-9]{0,2})$/;
const HEXGROUP_RE = /^[0-9A-Fa-f]{1,4}$/;

/** Dotted-quad IPv4: exactly 4 decimal octets 0..255, no leading zeros. */
export function isIPv4(text) {
  const parts = String(text).split('.');
  return parts.length === 4 && parts.every((p) => OCTET_RE.test(p) && Number(p) <= 255);
}

/**
 * IPv6 literal (RFC 4291 text form): up to 8 hex groups of 1..4 digits, at most one "::"
 * standing for one or more zero groups, optional trailing dotted IPv4 (counts as 2 groups).
 * No zone id, no brackets.
 */
export function isIPv6(text) {
  const value = String(text);
  if (!value.includes(':')) return false;
  const halves = value.split('::');
  if (halves.length > 2) return false;
  const groupsOf = (part) => (part === '' ? [] : part.split(':'));
  const head = groupsOf(halves[0]);
  const tail = halves.length === 2 ? groupsOf(halves[1]) : [];
  const all = [...head, ...tail];
  let count = 0;
  for (let i = 0; i < all.length; i += 1) {
    const g = all[i];
    const last = i === all.length - 1;
    if (last && g.includes('.')) {
      if (!isIPv4(g)) return false;
      count += 2;
    } else if (HEXGROUP_RE.test(g)) {
      count += 1;
    } else {
      return false; // empty group (":::" / leading or trailing ":") or bad digits
    }
  }
  return halves.length === 2 ? count <= 7 : count === 8;
}

/** Host name (labels 1..63 of [A-Za-z0-9-], no leading/trailing '-', total <= 253). */
export function isHostname(text) {
  const value = String(text);
  if (!value || value.length > HOST_LEN_MAX) return false;
  const labels = value.split('.');
  if (labels.every((l) => /^[0-9]+$/.test(l))) return false; // numeric-only: must be IPv4
  return labels.every((l) => LABEL_RE.test(l));
}

/**
 * Host the adapters may connect to: IPv4, IPv6 or host name; `existing` is the current list
 * (duplicates are refused case-insensitively, at most HOSTS_MAX entries).
 * Returns {ok: true, value} or {ok: false, reason} with reason in
 * empty | tooLong | tooMany | duplicate | ipv4 | ipv6 | hostname.
 */
export function validateHost(raw, existing = []) {
  const value = String(raw ?? '').trim();
  if (!value) return { ok: false, reason: 'empty' };
  if (value.length > HOST_LEN_MAX) return { ok: false, reason: 'tooLong' };
  if (value.includes(':')) {
    if (!isIPv6(value)) return { ok: false, reason: 'ipv6' };
  } else if (/^[0-9.]+$/.test(value)) {
    if (!isIPv4(value)) return { ok: false, reason: 'ipv4' };
  } else if (!isHostname(value)) {
    return { ok: false, reason: 'hostname' };
  }
  const list = Array.isArray(existing) ? existing.map((x) => String(x).toLowerCase()) : [];
  if (list.includes(value.toLowerCase())) return { ok: false, reason: 'duplicate' };
  if (list.length >= HOSTS_MAX) return { ok: false, reason: 'tooMany' };
  return { ok: true, value };
}

/** "2026-11-19T10:02:00+07:00" -> "19/11/2026 10:02" (local time); invalid -> ''. */
export function fmtWhen(iso) {
  if (!iso) return '';
  const d = new Date(String(iso));
  if (Number.isNaN(d.getTime())) return '';
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getDate())}/${p(d.getMonth() + 1)}/${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** First 16 hex digits of a SHA-256 fingerprint, grouped by 4: "9f86 d081 884c 7d65". */
export function shortFingerprint(fp) {
  const hex = String(fp ?? '').replace(/[^0-9a-f]/gi, '').toLowerCase().slice(0, 16);
  return hex.match(/.{1,4}/g)?.join(' ') ?? '';
}

/** Owner-facing error detail (machine [prefix] stripped, see installer-errors.js). */
export function errText(err) {
  return errorDetail(err);
}

/** Copy text to the clipboard; falls back to a hidden textarea + execCommand. */
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = h('textarea', { class: 'tv-installer-copybuf', 'aria-hidden': 'true', readonly: true });
    area.value = text;
    document.body.append(area);
    area.select();
    let ok = false;
    try {
      ok = document.execCommand('copy');
    } catch {
      ok = false;
    }
    area.remove();
    return ok;
  }
}

/** Select the text of `el` (so the owner can press Ctrl+C); true when something is selected. */
export function selectContents(el) {
  if (!el || !el.isConnected) return false;
  const selection = window.getSelection();
  if (!selection) return false;
  const range = document.createRange();
  range.selectNodeContents(el);
  selection.removeAllRanges();
  selection.addRange(range);
  return !selection.isCollapsed;
}

/**
 * Button that copies `getText()` and briefly shows `copiedLabel` (data-copied while shown).
 * If the clipboard API and the execCommand fallback both fail, the text of `target()` (the
 * element showing that text) is selected and `hint` ("press Ctrl+C") is shown next to the
 * button. Labels come from the calling page's strings.
 */
export function copyButton({ label, copiedLabel, failedLabel, hint, target, getText, action, cls = 'tv-btn' }) {
  let timer = 0;
  const text = h('span', null, label);
  const button = h('button', { type: 'button', class: `${cls} tv-installer-copy`, dataset: { action } }, text);
  const hintEl = h('span', { class: 'tv-installer-copyhint', role: 'status', 'data-copy-hint': action, hidden: true }, hint || '');
  button.addEventListener('click', async () => {
    const ok = await copyText(String(getText() ?? ''));
    clearTimeout(timer);
    text.textContent = ok ? copiedLabel : failedLabel;
    if (ok) {
      button.dataset.copied = '';
      hintEl.hidden = true;
    } else if (hint) {
      if (!hintEl.isConnected) button.after(hintEl);
      hintEl.hidden = false;
      const el = typeof target === 'function' ? target() : target;
      if (el && !el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1');
      el?.focus?.({ preventScroll: true });
      selectContents(el);
    }
    timer = setTimeout(() => {
      text.textContent = label;
      delete button.dataset.copied;
    }, 1800);
  });
  return button;
}

/** Load-error banner (role=alert) with a retry button. */
export function errorBanner({ text, retryLabel, onRetry, attr = 'data-load-error', kind }) {
  const span = h('span', null, text);
  const banner = h(
    'div',
    { class: 'tv-loaderror tv-installer-error', role: 'alert', [attr]: '', 'data-kind': kind },
    span,
    onRetry
      ? h('button', { type: 'button', class: 'tv-btn', dataset: { action: 'retry' }, on: { click: onRetry } }, retryLabel)
      : null,
  );
  banner.setText = (value) => {
    span.textContent = value;
  };
  return banner;
}

/**
 * Toggle switch (button role=switch). onChange(next) may return a Promise; reverts on reject.
 * Create it `disabled` until the real value is known, then `set(value)` + `disabled = false`.
 */
export function toggleSwitch({ setting, label, checked, onChange, disabled = false }) {
  const button = h(
    'button',
    { type: 'button', role: 'switch', class: 'tv-installer-switch', 'aria-checked': String(!!checked), dataset: { setting }, disabled },
    h('span', { class: 'tv-installer-switch__track', 'aria-hidden': 'true' }, h('i')),
    h('span', { class: 'tv-installer-switch__label' }, label),
  );
  button.addEventListener('click', async () => {
    if (button.dataset.busy !== undefined) return;
    const next = button.getAttribute('aria-checked') !== 'true';
    button.setAttribute('aria-checked', String(next));
    button.dataset.busy = '';
    try {
      await onChange(next);
    } catch {
      button.setAttribute('aria-checked', String(!next));
    } finally {
      delete button.dataset.busy;
    }
  });
  button.set = (value) => button.setAttribute('aria-checked', String(!!value));
  return button;
}

/** Employee label of a device: display name, else email, else windows_user@hostname. */
export function deviceName(d) {
  return String(d?.display_name || d?.email || `${d?.windows_user ?? ''}@${d?.hostname ?? ''}`);
}

/** Bump the page's data-tv-rev (tests and live refreshes wait on it). */
export function revCounter(root) {
  let rev = 0;
  return () => {
    rev += 1;
    root.dataset.tvRev = String(rev);
  };
}
