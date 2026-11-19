// Window commands (Task 20): minimise / maximise / restore / close / full screen, F11 + Esc
// shortcuts, the "only in the app" toast and the full-screen hint chip. Everything goes through
// the Python window API; when a call fails (plain browser, old bridge) the commands never throw:
// they show the "only in the app" toast (i18n titlebar.appOnly). Only in http mode (dev
// server, no pywebview) F11 falls back to the browser Fullscreen API.
//
// The toast and the hint are aria-live regions created ONCE at boot (empty, faded out); showing
// a message only changes their text, so screen readers announce it.

import { api } from './api.js';
import { t } from './i18n.js';
import * as winstate from './winstate.js';

const HINT_MS = 2500;
const TOAST_MS = 2500;

function region(cls) {
  let el = document.querySelector(`.${cls}`);
  if (!el) {
    el = document.createElement('div');
    el.className = `${cls} ${cls}--out`;
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    document.body.append(el);
  }
  return el;
}

/** Show `message` in a live region, fade it out after `ms`. */
function flash(cls, message, ms) {
  const el = region(cls);
  el.textContent = message;
  el.classList.remove(`${cls}--out`);
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.add(`${cls}--out`), ms);
  return el;
}

function fade(cls) {
  const el = document.querySelector(`.${cls}`);
  if (!el) return;
  clearTimeout(el._timer);
  el.classList.add(`${cls}--out`);
}

/** Brief bottom-centre message (role=status). One at a time. */
export function toast(message) {
  return flash('tv-toast', message, TOAST_MS);
}

function appOnly(err) {
  console.info('[window] window API unavailable', err?.message ?? err);
  toast(t('titlebar.appOnly'));
}

/** Run a window API call; apply the WindowState it returns; never rejects. */
async function run(method, ...args) {
  try {
    const result = await api[method](...args);
    if (result != null && typeof result === 'object' && 'maximized' in result) {
      winstate.applyState(result);
    }
    return true;
  } catch (err) {
    return err;
  }
}

async function command(method, ...args) {
  const res = await run(method, ...args);
  if (res !== true) appOnly(res);
}

export const minimize = () => command('window_min');
export const closeWindow = () => command('window_close');

export function toggleMaximize() {
  return winstate.getState().fullscreen
    ? command('window_set_fullscreen', false)
    : command('window_toggle_maximize');
}

/** Browser Fullscreen API (dev server without pywebview). */
async function browserFullscreen(on) {
  const doc = document;
  try {
    if (on && !doc.fullscreenElement) await doc.documentElement.requestFullscreen?.();
    else if (!on && doc.fullscreenElement) await doc.exitFullscreen?.();
    return true;
  } catch (err) {
    return err;
  }
}

let usingBrowserFullscreen = false;

export async function toggleFullscreen() {
  const res = await run('window_toggle_fullscreen');
  if (res === true) return;
  if ((await api.mode()) === 'http') {
    usingBrowserFullscreen = true;
    if ((await browserFullscreen(!document.fullscreenElement)) === true) return;
  }
  appOnly(res);
}

export async function leaveFullscreen() {
  if (usingBrowserFullscreen && document.fullscreenElement) {
    await browserFullscreen(false);
    return;
  }
  await command('window_set_fullscreen', false);
}

const TYPING = 'input, textarea, select, [contenteditable=""], [contenteditable="true"]';
const OVERLAY_OPEN =
  'dialog[open], [role="dialog"], [role="alertdialog"], [aria-modal="true"], [role="menu"], ' +
  '[role="listbox"][data-open], [role="combobox"][aria-expanded="true"]';

function overlayOpen() {
  return [...document.querySelectorAll(OVERLAY_OPEN)].some(
    (el) => !el.closest('[hidden]') && el.getClientRects().length > 0,
  );
}

function onKeydown(event) {
  if (event.key === 'F11') {
    // F11 always works, even while typing; a held key (auto-repeat) toggles only once.
    event.preventDefault();
    if (!event.repeat) toggleFullscreen();
    return;
  }
  if (event.key !== 'Escape' || event.defaultPrevented) return;
  if (!winstate.getState().fullscreen) return;
  if (event.target instanceof Element && event.target.closest(TYPING)) return;
  if (overlayOpen()) return;
  event.preventDefault();
  leaveFullscreen();
}

/** Full-screen hint chip: shown on ENTERING full screen, fades after ~2.5 s. */
let wasFullscreen = false;
function syncHint(state) {
  if (state.fullscreen && !wasFullscreen) {
    flash('tv-fullscreen-hint', t('titlebar.fullscreenHint'), HINT_MS);
  } else if (!state.fullscreen) {
    fade('tv-fullscreen-hint');
  }
  wasFullscreen = state.fullscreen;
}

let installed = false;

export function installShortcuts() {
  if (installed) return;
  installed = true;
  region('tv-toast');
  region('tv-fullscreen-hint');
  wasFullscreen = winstate.getState().fullscreen;
  // Bubble phase on window: a dialog or menu that handled Esc first (preventDefault) wins.
  window.addEventListener('keydown', onKeydown);
  winstate.subscribe(syncHint);
  document.addEventListener('fullscreenchange', () => {
    if (!usingBrowserFullscreen) return;
    winstate.patchState({ fullscreen: !!document.fullscreenElement });
    if (!document.fullscreenElement) usingBrowserFullscreen = false;
  });
}
