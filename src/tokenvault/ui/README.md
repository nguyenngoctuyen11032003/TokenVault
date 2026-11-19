# TokenVault UI shell

Plain HTML/CSS/ES modules hosted by pywebview (WebView2). No framework, no build step, no CDN:
everything the app loads is in this folder (offline app).

```
index.html            shell markup (titlebar, nav, <main id="tv-view">)
css/theme.css         tokens (--tv-*) + base styles, focus ring, reduced motion
css/window.css        chamfered window, hatch plate, title hood, layout grid
css/components.css    <tv-panel>, nav, buttons, notices
css/primitives.css    shared page primitives (.tv-table, .tv-bar, .tv-seg, ...)
css/gauge.css         <tv-gauge>
css/pages.css         reserved (see "Page styles" below)
js/app.js             boot: i18n -> components -> window mode -> router
js/api.js             Python bridge (pywebview, http fallback)
js/router.js          hash router, dynamic route modules
js/nav.js             nav manifest (single list of pages)
js/filters.js         global filter store
js/live.js            window.tv live bus
js/i18n.js            t(key, params)
js/dom.js             esc(), useStylesheet(), compactNumber()
js/components/*.js    tv-titlebar, tv-navmenu, tv-panel, tv-gauge, icons
js/routes/<name>.js   one module per page (added by page tasks)
i18n/vi.json          every visible string
vendor/               echarts.min.js (Apache-2.0, see LICENSE-echarts.txt)
```

## Adding a page (no shared-file edits)

1. Create `js/routes/<name>.js` (`<name>` matches `[a-z0-9-]+`). Hash `#/<name>/<arg>/...`
   loads it on demand; until the file exists the router shows a "page not built yet" panel.
2. Default-export `render(container, ctx)` — or an object `{ render, destroy }`. Optional
   named export `destroy()`; `render` may also return a cleanup function. `render` may be async.
3. Put the page's strings in `i18n/vi/<name>.json` under a page namespace
   (e.g. `{"home": {"tokens": "Tổng token"}}`) and use `ctx.t('home.tokens')`. The router merges
   that file before calling `render`, so page tasks never edit the shared `i18n/vi.json`.
   Strings needed outside the route itself (components shared by several pages) go in the file
   of the route that owns the component. Never hardcode Vietnamese in js/html/css —
   `tests/ui/test_shell.py` fails on it.

```js
// js/routes/projects.js
export default async function render(container, ctx) {
  await ctx.useStylesheet('css/pages/projects.css');
  container.innerHTML = `<tv-panel heading="${ctx.t('projects.title')}"></tv-panel>`;
  const draw = async () => { /* ... await ctx.api.projects(ctx.filters.get()) ... */ };
  ctx.filters.subscribe(draw);       // auto-removed when the route is left
  ctx.onLive(() => draw());          // auto-removed when the route is left
  await draw();
}
export function destroy() { /* dispose charts, timers */ }
```

`ctx` contents:

| key | meaning |
| --- | --- |
| `route`, `args` | route name and decoded path args (`#/employee/a%40b.vn` -> `['a@b.vn']`) |
| `api` | `api.<method>(...args)` -> Promise (any Python `Api` method; `ApiError` on failure) |
| `t(key, params?)` | i18n lookup, `{name}` placeholders |
| `filters` | `get()`, `set(patch)`, `reset()`, `subscribe(cb(state, changedKeys))` — shared by all pages, persisted (`localStorage['tv.filters.v1']`); keys `team, email, project, model, range` (`range` defaults to `'today'`) |
| `onLive(cb)` | live push subscription (payload from Python) |
| `navigate(route, ...args)` | sets the hash |
| `useStylesheet(href)` | links a stylesheet once (path relative to ui/); resolves on load |
| `onCleanup(fn)` | run `fn` when the route is left |

The container is `<main id="tv-view" data-route="<name>">`; it is emptied between routes and gets
`data-route-state="ready|missing|error"` after rendering. Use `esc()` from `js/dom.js` when
interpolating data (emails, session titles) into HTML strings.

## Navigation

`js/nav.js` already lists every page: home, employees, projects, sessions, skills, orchestration,
installer, settings, diagnostics. Detail routes highlight their parent through `also`
(`employee -> employees`, `project -> projects`, `session -> sessions`).

## Page styles

Do not edit `theme.css`, `window.css`, `components.css`, `primitives.css`, `gauge.css` or `pages.css` from page
tasks. Ship `css/pages/<name>.css`, load it with `ctx.useStylesheet`, and scope every rule under
`.tv-view[data-route="<name>"]` or a `tv-<name>-` class prefix. Reuse the tokens (`--tv-red`,
`--tv-red-hot`, `--tv-orange`, `--tv-panel`, `--tv-line`, `--tv-text`, `--tv-muted`,
`--tv-series-1..8`, ...) and primitives: `.tv-grid`, `.tv-list`/`.tv-list__item`,
`.tv-table`, `.tv-num`, `.tv-num--hot`, `.tv-num--orange`, `.tv-bar > i`, `.tv-dot(--on)`,
`.tv-seg > button[aria-pressed]`, `.tv-chart-area`, `.tv-split`, `.tv-kpi__value`, `.tv-meta`,
`.tv-chips`, `.tv-btn(--primary)`, `.tv-gauge-row`.

## Components

- `<tv-panel heading="...">` (alias `title`, converted to avoid a native tooltip): chamfered panel
  with an angled tab. Children with `slot="actions"` go to the tab row; the rest into
  `.tv-panel__body` (also reachable as `panel.body`).
- `<tv-gauge value max [min] label unit [display]>`: 270° tick gauge; `display` overrides the
  number text (e.g. `$61.4`, `UNKNOWN`). Sets `--tv-gauge-p` and `role="meter"`.
- `<tv-titlebar>`, `<tv-navmenu>`: used once by the shell.

## Python contract

- `api.js` uses `window.pywebview.api.<method>` (waits for `pywebviewready`); without pywebview it
  POSTs the JSON argument array to `/api/<method>` and expects a JSON response.
- Window buttons call `window_min()` and `window_close()` (close = hide to tray).
- `settings().transparent_window === false` switches the window to `.window--opaque` (rectangle,
  chamfer drawn inside). Missing setting or a failed call keeps the transparent window.
- Live push: `window.evaluate_js("tv.onLive(<json>)")` (object or JSON string; `tv.emitLive` is
  the same). The titlebar reads `{online, total}` or `{devices: {online, total}}`.
- Frameless drag: elements with class `pywebview-drag-region` (titlebar brand, title, spacer).
- Serve this folder over http (pywebview's built-in server): ES modules do not load from
  `file://`, and `.js` must be served as `text/javascript`.
