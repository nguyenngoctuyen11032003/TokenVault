// Shell bootstrap: strings -> components -> window mode -> router.
// Sets <html data-tv-ready="1"> once the first route has rendered (used by UI tests).

import { api } from './api.js';
import { applyI18n, loadI18n, t } from './i18n.js';
import { filters } from './filters.js';
import * as live from './live.js';
import { createRouter } from './router.js';

live.installGlobal();

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

  const win = document.querySelector('.tv-window');
  const view = document.getElementById('tv-view');
  // Skip link is a button: an anchor to #tv-view would be read as a route by the hash router.
  document.querySelector('.tv-skip')?.addEventListener('click', () => view.focus());
  const router = createRouter({ container: view, api, t, filters, live });
  globalThis.tv.router = router;
  globalThis.tv.filters = filters;

  await Promise.all([applyWindowMode(win), router.start()]);
  document.documentElement.dataset.tvReady = '1';
}

boot().catch((err) => {
  console.error('[app] boot failed', err);
  document.documentElement.dataset.tvReady = 'error';
});
