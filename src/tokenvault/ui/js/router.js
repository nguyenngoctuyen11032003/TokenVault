// Hash router with dynamically imported route modules (see ui/README.md for the contract).
//
//   #/<name>/<arg1>/<arg2>  ->  import('./routes/<name>.js')
//
// A route module's default export is either `render(container, ctx)` or
// `{ render(container, ctx), destroy() }`; it may also export a named `destroy()` or return a
// cleanup function from render. ctx = { route, args, api, t, filters, onLive(cb), navigate,
// useStylesheet, onCleanup(fn), signal, container }. Missing modules render a "not built yet"
// panel. Optional page strings in i18n/<lang>/<name>.json are merged before render.
//
// Lifetime: every navigation gets its own scope { alive, AbortController, cleanups } and its own
// wrapper element <div class="tv-route"> inside #tv-view; `container` is that wrapper. Leaving
// the route (even while an async render is still running) marks the scope dead, aborts
// ctx.signal, runs cleanups + destroy() once, and detaches the wrapper — so a stale render can
// only write into a detached node, and any ctx.onLive / ctx.filters.subscribe / ctx.onCleanup
// registered after that is undone immediately.

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
  let current = null; // scope of the live navigation

  function runCleanup(scope, fn) {
    try {
      fn();
    } catch (err) {
      console.error(`[router] cleanup failed for ${scope.name}`, err);
    }
  }

  function teardown(scope) {
    if (!scope || !scope.alive) return;
    scope.alive = false;
    scope.controller.abort();
    for (const fn of scope.cleanups.splice(0).reverse()) runCleanup(scope, fn);
    scope.wrapper.remove();
    if (current === scope) current = null;
  }

  /** Register a cleanup; if the scope is already dead, run it now. */
  function register(scope, fn) {
    if (typeof fn !== 'function') return;
    if (scope.alive) scope.cleanups.push(fn);
    else runCleanup(scope, fn);
  }

  function subscribeScoped(scope, subscribe, cb) {
    if (!scope.alive) return () => {};
    const off = subscribe((...a) => {
      if (scope.alive) cb(...a);
    });
    register(scope, off);
    return off;
  }

  function makeCtx(scope, args) {
    return {
      route: scope.name,
      args,
      api,
      t,
      container: scope.wrapper,
      signal: scope.controller.signal,
      filters: {
        get: () => filters.get(),
        set: (patch) => (scope.alive ? filters.set(patch) : false),
        reset: () => (scope.alive ? filters.reset() : false),
        subscribe: (cb) => subscribeScoped(scope, (fn) => filters.subscribe(fn), cb),
      },
      onLive: (cb) => subscribeScoped(scope, (fn) => live.onLive(fn), cb),
      navigate: (route, ...routeArgs) => {
        if (scope.alive) location.hash = hrefFor(route, ...routeArgs);
      },
      useStylesheet,
      onCleanup: (fn) => register(scope, fn),
    };
  }

  function pageLabel(name) {
    const entry = navEntryFor(name);
    return entry ? t(entry.i18nKey) : name;
  }

  function renderNotice(target, kind, name, detail) {
    const page = pageLabel(name);
    const title = kind === 'missing' ? t('route.notBuiltTitle') : t('route.errorTitle');
    const body =
      kind === 'missing'
        ? t('route.notBuiltBody', { page })
        : t('route.errorBody', { page, detail: detail || '' });
    target.innerHTML = `
      <tv-panel class="tv-notice ${kind === 'missing' ? 'tv-not-built' : 'tv-route-error'} tv-notice--${kind}"
                heading="${esc(title)}">
        <div class="tv-notice__body">
          <div class="tv-notice__mark" aria-hidden="true"></div>
          <div>
            <p class="tv-notice__lead">${esc(body)}</p>
            ${kind === 'missing' ? `<p class="tv-muted">${esc(t('route.notBuiltHint'))}</p>` : ''}
            <a class="tv-btn" href="#/${DEFAULT_ROUTE}">${esc(t('route.backHome'))}</a>
          </div>
        </div>
      </tv-panel>`;
  }

  async function go() {
    const { name, args } = parseHash(location.hash);
    teardown(current);
    const wrapper = document.createElement('div');
    wrapper.className = 'tv-route';
    const scope = {
      name,
      alive: true,
      controller: new AbortController(),
      cleanups: [],
      wrapper,
    };
    current = scope;
    container.replaceChildren(wrapper);
    container.dataset.route = name;
    delete container.dataset.routeState;
    container.setAttribute('aria-busy', 'true');
    container.scrollTop = 0;
    onRoute?.(name, args);

    let state = 'ready';
    if (!NAME_RE.test(name)) {
      state = 'missing';
      renderNotice(wrapper, 'missing', name);
    } else {
      const url = new URL(`./routes/${name}.js`, import.meta.url).href;
      try {
        const [mod] = await Promise.all([import(url), loadI18nExtension(name)]);
        if (!scope.alive) return;
        const def = mod.default;
        const render = typeof def === 'function' ? def : def?.render;
        if (typeof render !== 'function') throw new Error('route module has no render()');
        const destroy = typeof def?.destroy === 'function' ? def.destroy : mod.destroy;
        // Registered first so it runs last (cleanups run in reverse) and exactly once, even if
        // the user leaves while render is still pending.
        if (typeof destroy === 'function') register(scope, () => destroy.call(def));
        const result = await render(wrapper, makeCtx(scope, args));
        register(scope, result); // runs immediately if the route was left meanwhile
        if (!scope.alive) return;
      } catch (err) {
        if (!scope.alive) return;
        const exists = await moduleExists(url);
        if (!scope.alive) return;
        for (const fn of scope.cleanups.splice(0).reverse()) runCleanup(scope, fn);
        state = exists ? 'error' : 'missing';
        if (exists) console.error(`[router] route ${name} failed`, err);
        renderNotice(wrapper, state, name, err?.message);
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
