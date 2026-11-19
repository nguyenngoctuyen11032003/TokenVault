// Shell bootstrap: strings -> components -> window mode -> router.
// Sets <html data-tv-ready="1"> once the first route has rendered (used by UI tests).

import { api } from './api.js';
import { applyI18n, loadI18n, t } from './i18n.js';
import { filters } from './filters.js';
import * as live from './live.js';
import { createRouter } from './router.js';
import { installScale } from './uiscale.js';
import { installShortcuts } from './wincommands.js';
import * as winstate from './winstate.js';

live.installGlobal();
installScale(); // before the first layout: large windows are zoomed (--tv-scale)

/** Transparent chamfered window by default; opaque rectangle when settings say so. */
async function applyWindowMode(win) {
  let settings = null;
  try {
    settings = await api.settings();
  } catch (err) {
    console.warn('[app] settings unavailable, keeping transparent window', err);
  }
  const opaque = settings?.transparent_window === false;
  win.classList.toggle('window--opaque', opaque);
  document.documentElement.classList.toggle('tv-opaque', opaque);
  const mode = await api.mode();
  document.documentElement.dataset.tvHost = mode;
}

async function boot() {
  await loadI18n('vi');
  document.title = t('app.documentTitle');
  applyI18n(document);

  // Components need strings at construction time, so they are loaded after i18n.
  await Promise.all([
    import('./components/paneltab.js'),
    import('./components/gauge.js'),
    import('./components/titlebar.js'),
    import('./components/navmenu.js'),
  ]);

  // Window state (maximised / full screen) + controls. Not awaited: the first route renders
  // while Python answers window_state(); tv.onWindowState is installed synchronously.
  const winReady = winstate.init(api);
  installShortcuts();
  const { mountResizeHandles } = await import('./components/resize-handles.js');
  mountResizeHandles(api);

  const win = document.querySelector('.tv-window');
  const view = document.getElementById('tv-view');
  // Skip link is a button: an anchor to #tv-view would be read as a route by the hash router.
  document.querySelector('.tv-skip')?.addEventListener('click', () => view.focus());
  const router = createRouter({ container: view, api, t, filters, live });
  globalThis.tv.router = router;
  globalThis.tv.filters = filters;

  await Promise.all([applyWindowMode(win), router.start()]);
  winReady.catch(() => {}); // never blocks the first render (init itself never throws)
  globalThis.tv.bootMs = Math.round(performance.now()); // diagnostics: time to first render
  document.documentElement.dataset.tvReady = '1';
}

// Shown when boot fails. Deliberately hardcoded (English, inline styles, textContent only):
// the strings file or the components may be exactly what failed to load.
const BOOT_ERROR_TITLE = 'TokenVault could not start the interface.';
const BOOT_ERROR_HINT = 'Reload the window. If this keeps happening, reinstall the Manager.';

export function showBootError(err) {
  const host = document.getElementById('tv-view') ?? document.body;
  const panel = document.createElement('section');
  panel.className = 'tv-boot-error';
  panel.setAttribute('role', 'alert');
  panel.style.cssText =
    'margin:24px;padding:20px 24px;max-width:720px;background:#151518;color:#e8e8eb;' +
    'border:1px solid #3b3b41;border-left:3px solid #ff2d3f;font:14px/1.5 "Segoe UI",system-ui,sans-serif;';
  const title = document.createElement('h2');
  title.textContent = BOOT_ERROR_TITLE;
  title.style.cssText = 'margin:0 0 8px;font-size:16px;color:#f6f6f8;';
  const detail = document.createElement('pre');
  detail.className = 'tv-boot-error__detail';
  detail.textContent = String(err?.stack || err?.message || err);
  detail.style.cssText =
    'margin:0 0 10px;white-space:pre-wrap;word-break:break-word;color:#a1a1a9;font-size:12px;';
  const hint = document.createElement('p');
  hint.textContent = BOOT_ERROR_HINT;
  hint.style.cssText = 'margin:0 0 12px;';
  const reload = document.createElement('button');
  reload.type = 'button';
  reload.textContent = 'Reload';
  reload.style.cssText =
    'padding:7px 18px;border:0;background:#e10b1f;color:#fff;font:inherit;cursor:pointer;';
  reload.addEventListener('click', () => location.reload());
  panel.append(title, detail, hint, reload);
  host.replaceChildren(panel);
}

boot().catch((err) => {
  console.error('[app] boot failed', err);
  try {
    showBootError(err);
  } finally {
    document.documentElement.dataset.tvReady = 'error';
  }
});
