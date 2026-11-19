// Window commands (Task 20): maximise / restore / full screen, F11 + Esc shortcuts, the
// "only in the app" toast and the full-screen hint chip. Everything goes through the Python
// window API; when it is missing (plain browser, old bridge) the commands never throw: they
// show a small toast, and F11 falls back to the browser Fullscreen API.

import { api } from './api.js';
import { t } from './i18n.js';
import * as winstate from './winstate.js';

const HINT_MS = 2500;
const TOAST_MS = 2500;

function reducedMotion() {
  return globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
}

/** Brief bottom-centre message (role=status). One at a time. */
export function toast(message) {
  let el = document.querySelector('.tv-toast');
  if (!el) {
    el = document.createElement('div');
    el.className = 'tv-toast';
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    document.body.append(el);
  }
  el.textContent = message;
  el.hidden = false;
  el.classList.remove('tv-toast--out');
  clearTimeout(el._timer);
  el._timer = setTimeout(() => {
    el.classList.add('tv-toast--out');
    el._timer = setTimeout(() => {
      el.hidden = true;
    }, reducedMotion() ? 0 : 300);
  }, TOAST_MS);
  return el;
}

function appOnly(err) {
  console.info('[window] window API unavailable', err?.message ?? err);
  toast(t('titlebar.appOnly'));
}

/** Run a window API call; apply the WindowState it returns; never rejects. */
async function run(method, ...args) {
  try {
    const result = await api[method](...args);
    if (result != null) winstate.applyState(result);
    return true;
  } catch (err) {
    return err;
  }
}

export async function toggleMaximize() {
  const s = winstate.getState();
  const res = s.fullscreen ? await run('window_set_fullscreen', false) : await run('window_toggle_maximize');
  if (res !== true) appOnly(res);
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
  usingBrowserFullscreen = true;
  const fallback = await browserFullscreen(!document.fullscreenElement);
  if (fallback !== true) appOnly(res);
}

export async function leaveFullscreen() {
  if (usingBrowserFullscreen && document.fullscreenElement) {
    await browserFullscreen(false);
    return;
  }
  const res = await run('window_set_fullscreen', false);
  if (res !== true) appOnly(res);
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
    // F11 always works, even while typing.
    event.preventDefault();
    toggleFullscreen();
    return;
  }
  if (event.key !== 'Escape' || event.defaultPrevented) return;
  if (!winstate.getState().fullscreen) return;
  if (event.target instanceof Element && event.target.closest(TYPING)) return;
  if (overlayOpen()) return;
  event.preventDefault();
  leaveFullscreen();
}

/** Full-screen hint chip: shown on entering full screen, fades after ~2.5 s. */
function syncHint(state) {
  let hint = document.querySelector('.tv-fullscreen-hint');
  if (!state.fullscreen) {
    if (hint) hint.hidden = true;
    return;
  }
  if (hint && !hint.hidden) return;
  if (!hint) {
    hint = document.createElement('div');
    hint.className = 'tv-fullscreen-hint';
    hint.setAttribute('role', 'status');
    document.body.append(hint);
  }
  hint.textContent = t('titlebar.fullscreenHint');
  hint.hidden = false;
  hint.classList.remove('tv-fullscreen-hint--out');
  clearTimeout(hint._timer);
  hint._timer = setTimeout(() => hint.classList.add('tv-fullscreen-hint--out'), HINT_MS);
}

let installed = false;

export function installShortcuts() {
  if (installed) return;
  installed = true;
  // Bubble phase on window: a dialog or menu that handled Esc first (preventDefault) wins.
  window.addEventListener('keydown', onKeydown);
  winstate.subscribe(syncHint);
  document.addEventListener('fullscreenchange', () => {
    if (!usingBrowserFullscreen) return;
    winstate.patchState({ fullscreen: !!document.fullscreenElement });
    if (!document.fullscreenElement) usingBrowserFullscreen = false;
  });
}
