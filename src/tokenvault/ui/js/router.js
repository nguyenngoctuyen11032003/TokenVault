// Hash router with dynamically imported route modules (see ui/README.md for the contract).
//
//   #/<name>/<arg1>/<arg2>  ->  import('./routes/<name>.js')
//
// A route module's default export is either `render(container, ctx)` or
// `{ render(container, ctx), destroy() }`; it may also export a named `destroy()` or return a
// cleanup function from render. ctx = { route, args, api, t, filters, onLive(cb), navigate,
// useStylesheet }. Subscriptions made through ctx.filters.subscribe / ctx.onLive are removed
// automatically when the route is left. Missing modules render a "not built yet" panel.
// Optional page strings in i18n/<lang>/<name>.json are merged before render.

import { DEFAULT_ROUTE, navEntryFor } from './nav.js';
import { esc, useStylesheet } from './dom.js';
import { loadI18nExtension } from './i18n.js';

const NAME_RE = /^[a-z0-9][a-z0-9-]*$/;

export function parseHash(hash) {
  const raw = String(hash || '').replace(/^#\/?/, '');
  const [first = '', ...rest] = raw.split('/');
  const decode = (s) => {
    try {
      return decodeURIComponent(s);
    } catch {
      return s;
    }
  };
  const name = decode(first).toLowerCase() || DEFAULT_ROUTE;
  return { name, args: rest.filter((s) => s !== '').map(decode) };
}

export function hrefFor(name, ...args) {
  return `#/${[name, ...args].map((s) => encodeURIComponent(String(s))).join('/')}`;
}

async function moduleExists(url) {
  try {
    const response = await fetch(url, { method: 'HEAD', cache: 'no-store' });
    return response.ok;
  } catch {
    return false;
  }
}

export function createRouter({ container, api, t, filters, live, onRoute }) {
  let current = null; // { name, cleanups: [] }
  let navToken = 0;

  function teardown() {
    if (!current) return;
    for (const fn of current.cleanups.splice(0).reverse()) {
      try {
        fn();
      } catch (err) {
        console.error(`[router] destroy failed for ${current.name}`, err);
      }
    }
    current = null;
  }

  function makeCtx(name, args, cleanups) {
    const scopedFilters = {
      get: () => filters.get(),
      set: (patch) => filters.set(patch),
      reset: () => filters.reset(),
      subscribe(cb) {
        const off = filters.subscribe(cb);
        cleanups.push(off);
        return off;
      },
    };
    return {
      route: name,
      args,
      api,
      t,
      filters: scopedFilters,
      onLive(cb) {
        const off = live.onLive(cb);
        cleanups.push(off);
        return off;
      },
      navigate: (route, ...routeArgs) => {
        location.hash = hrefFor(route, ...routeArgs);
      },
      useStylesheet,
      onCleanup: (fn) => cleanups.push(fn),
    };
  }

  function pageLabel(name) {
    const entry = navEntryFor(name);
    return entry ? t(entry.i18nKey) : name;
  }

  function renderNotice(kind, name, detail) {
    const page = pageLabel(name);
    const title = kind === 'missing' ? t('route.notBuiltTitle') : t('route.errorTitle');
    const body =
      kind === 'missing'
        ? t('route.notBuiltBody', { page })
        : t('route.errorBody', { page, detail: detail || '' });
    container.innerHTML = `
      <tv-panel class="tv-notice tv-not-built tv-notice--${kind}" heading="${esc(title)}">
        <div class="tv-notice__body">
          <div class="tv-notice__mark" aria-hidden="true"></div>
          <div>
            <p class="tv-notice__lead">${esc(body)}</p>
            ${kind === 'missing' ? `<p class="tv-muted">${esc(t('route.notBuiltHint'))}</p>` : ''}
            <a class="tv-btn" href="#/${DEFAULT_ROUTE}">${esc(t('route.backHome'))}</a>
          </div>
        </div>
      </tv-panel>`;
    if (kind === 'error') container.querySelector('tv-panel').classList.remove('tv-not-built');
  }

  async function go() {
    const token = ++navToken;
    const { name, args } = parseHash(location.hash);
    teardown();
    container.replaceChildren();
    container.dataset.route = name;
    delete container.dataset.routeState;
    container.setAttribute('aria-busy', 'true');
    container.scrollTop = 0;
    onRoute?.(name, args);

    const cleanups = [];
    let state = 'ready';
    if (!NAME_RE.test(name)) {
      state = 'missing';
      renderNotice('missing', name);
    } else {
      const url = new URL(`./routes/${name}.js`, import.meta.url).href;
      try {
        const [mod] = await Promise.all([import(url), loadI18nExtension(name)]);
        if (token !== navToken) return;
        const def = mod.default;
        const render = typeof def === 'function' ? def : def?.render;
        if (typeof render !== 'function') throw new Error('route module has no render()');
        current = { name, cleanups };
        const result = await render(container, makeCtx(name, args, cleanups));
        if (token !== navToken) return;
        if (typeof result === 'function') cleanups.push(result);
        const destroy = typeof def?.destroy === 'function' ? def.destroy : mod.destroy;
        if (typeof destroy === 'function') cleanups.unshift(() => destroy.call(def));
      } catch (err) {
        if (token !== navToken) return;
        const exists = await moduleExists(url);
        if (token !== navToken) return;
        current = null;
        for (const fn of cleanups.splice(0)) fn?.();
        container.replaceChildren();
        state = exists ? 'error' : 'missing';
        if (exists) console.error(`[router] route ${name} failed`, err);
        renderNotice(state, name, err?.message);
      }
    }
    container.removeAttribute('aria-busy');
    container.dataset.routeState = state;
  }

  return {
    start() {
      window.addEventListener('hashchange', go);
      if (!location.hash) history.replaceState(null, '', `#/${DEFAULT_ROUTE}`);
      return go();
    },
    reload: go,
    current: () => current?.name ?? null,
  };
}
