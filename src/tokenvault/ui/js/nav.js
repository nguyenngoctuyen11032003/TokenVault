// Navigation manifest: the single list of pages shown in the left menu.
// `route` -> hash `#/<route>` and module `js/routes/<route>.js`; `i18nKey` -> label;
// `icon` -> key in components/icons.js; `also` -> other routes that highlight this entry
// (e.g. the employee detail page `#/employee/<email>` highlights "employees").
// Pages whose module does not exist yet render a "not built yet" panel.

export const NAV = Object.freeze([
  { route: 'home', i18nKey: 'nav.home', icon: 'home' },
  { route: 'employees', i18nKey: 'nav.employees', icon: 'users', also: ['employee'] },
  { route: 'projects', i18nKey: 'nav.projects', icon: 'folder', also: ['project'] },
  { route: 'sessions', i18nKey: 'nav.sessions', icon: 'sessions', also: ['session'] },
  { route: 'skills', i18nKey: 'nav.skills', icon: 'skills' },
  { route: 'orchestration', i18nKey: 'nav.orchestration', icon: 'orchestration' },
  { route: 'installer', i18nKey: 'nav.installer', icon: 'installer' },
  { route: 'settings', i18nKey: 'nav.settings', icon: 'settings' },
  { route: 'diagnostics', i18nKey: 'nav.diagnostics', icon: 'diagnostics' },
]);

export const DEFAULT_ROUTE = 'home';

/** Nav entry that should be highlighted for a route name (or undefined). */
export function navEntryFor(route) {
  return NAV.find((item) => item.route === route || item.also?.includes(route));
}
