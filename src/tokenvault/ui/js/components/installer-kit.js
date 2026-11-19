// Shared helpers for the installer / settings / diagnostics pages (Task 17): validation of
// team names and host names, error banners with retry, clipboard copy with feedback, local
// date-time formatting and fingerprint formatting. Data is untrusted: everything goes through
// h() (textContent / attributes only).

import { h } from './homekit.js';

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

const HOST_RE = /^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$/;
const IPV6_RE = /^[0-9A-Fa-f:]{2,39}$/;

/** Host name, IPv4 or IPv6 literal the adapters may connect to. */
export function validateHost(raw) {
  const value = String(raw ?? '').trim();
  if (!value) return { ok: false, reason: 'empty' };
  if (HOST_RE.test(value) || (value.includes(':') && IPV6_RE.test(value))) return { ok: true, value };
  return { ok: false, reason: 'invalid' };
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

/** Readable error detail of an ApiError / Error / anything. */
export function errText(err) {
  return String(err?.message ?? err ?? '');
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

/**
 * Button that copies `getText()` and briefly shows `copiedLabel` (data-copied while shown).
 * `label`/`copiedLabel`/`failedLabel` come from the calling page's strings.
 */
export function copyButton({ label, copiedLabel, failedLabel, getText, action, cls = 'tv-btn' }) {
  let timer = 0;
  const text = h('span', null, label);
  const button = h('button', { type: 'button', class: `${cls} tv-installer-copy`, dataset: { action } }, text);
  button.addEventListener('click', async () => {
    const ok = await copyText(String(getText() ?? ''));
    clearTimeout(timer);
    text.textContent = ok ? copiedLabel : failedLabel;
    if (ok) button.dataset.copied = '';
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

/** Toggle switch (button role=switch). onChange(next) may return a Promise; reverts on reject. */
export function toggleSwitch({ setting, label, checked, onChange }) {
  const button = h(
    'button',
    { type: 'button', role: 'switch', class: 'tv-installer-switch', 'aria-checked': String(!!checked), dataset: { setting } },
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
