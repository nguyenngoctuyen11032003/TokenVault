// Test-only: extends the fake pywebview bridge (conftest.stub_script) with the Manager data API
// used by the home / employees / employee pages. Data comes from window.__tvHomeData (built in
// tests/ui/conftest_home.py); tests may mutate it at runtime to simulate new data.
// Every call is recorded in window.__tvCalls like the base stub does.
(() => {
  const api = window.pywebview && window.pywebview.api;
  if (!api) return;
  const calls = window.__tvCalls || (window.__tvCalls = []);
  const clone = (v) => JSON.parse(JSON.stringify(v ?? null));
  const rec = (name, fn) => (...args) => {
    calls.push({ name, args: clone(args) });
    const delay = Number(window.__tvHomeDelay || 0);
    const value = clone(fn(...args));
    return delay ? new Promise((r) => setTimeout(() => r(value), delay)) : Promise.resolve(value);
  };
  const D = () => window.__tvHomeData;
  const isToday = (f) => !!f && f.start && f.start === f.end;

  api.teams = rec('teams', () => D().teams);
  api.overview = rec('overview', (f) => {
    const key = `${(f && f.team) || ''}|${isToday(f) ? 'today' : 'ref'}`;
    return D().overview[key] ?? D().overview[`|${isToday(f) ? 'today' : 'ref'}`];
  });
  api.employees = rec('employees', (f) => {
    let rows = D().employees;
    if (f && f.team) rows = rows.filter((r) => r.teams.includes(f.team));
    const sum = rows.reduce((a, r) => a + r.total_tokens, 0) || 1;
    return rows.map((r) => ({ ...r, share: r.total_tokens / sum }));
  });
  api.employee = rec('employee', (key, range) => {
    const views = D().employee[key];
    if (!views) throw new Error(`unknown employee ${key}`);
    return views[range] ?? views.today;
  });
})();
