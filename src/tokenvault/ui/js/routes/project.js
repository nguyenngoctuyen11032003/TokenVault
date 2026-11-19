// #/project/<url-encoded name> — shortcut used by the home page's top-project chips.
// Sets the shared project filter to the decoded name and continues to #/sessions (history entry
// replaced, so Back does not bounce through this page). An empty name, or a name the Manager
// does not know (api.projects over all time), goes to #/projects instead. "(no project)" maps
// to the NO_PROJECT filter sentinel. Shows no text of its own.

import { NO_PROJECT } from '../components/overview-kit.js';

const ALL_TIME = { team: null, email: null, project: null, model: null, start: null, end: null };

function go(route) {
  location.replace(`${location.pathname}${location.search}#/${route}`);
}

export default async function render(_container, ctx) {
  const name = String(ctx.args[0] ?? '');
  if (!name) {
    go('projects');
    return;
  }
  if (name !== NO_PROJECT) {
    let known = true;
    try {
      const rows = await ctx.api.projects(ALL_TIME);
      if (Array.isArray(rows)) known = rows.some((p) => String(p?.project ?? '') === name);
    } catch (err) {
      console.warn('[project] projects() failed; opening sessions anyway', err);
    }
    if (ctx.signal.aborted) return;
    if (!known) {
      go('projects');
      return;
    }
  }
  if (ctx.signal.aborted) return;
  ctx.filters.set({ project: name });
  go('sessions');
}
