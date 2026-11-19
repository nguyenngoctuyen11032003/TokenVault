// Test-only: extends the fake pywebview bridge with api.skills(range) / api.orchestration(range).
// Data comes from window.__tvSkillsData = {skills: {<range>: view}, orchestration: {<range>: view}}
// (built in tests/ui/conftest_skills.py). Setting window.__tvSkillsFail = true makes both reject.
// Calls are recorded in window.__tvCalls like the base stub does.
(() => {
  const api = window.pywebview && window.pywebview.api;
  if (!api) return;
  const calls = window.__tvCalls || (window.__tvCalls = []);
  const clone = (v) => JSON.parse(JSON.stringify(v ?? null));
  const rec = (name, fn) => (...args) => {
    calls.push({ name, args: clone(args) });
    if (window.__tvSkillsFail) return Promise.reject(new Error('boom: backend down'));
    const delay = Number(window.__tvSkillsDelay || 0);
    const value = clone(fn(...args));
    return delay ? new Promise((r) => setTimeout(() => r(value), delay)) : Promise.resolve(value);
  };
  const D = () => window.__tvSkillsData;
  api.skills = rec('skills', (range) => {
    const view = D().skills[range];
    if (!view) throw new Error(`bad range ${range}`);
    return view;
  });
  api.orchestration = rec('orchestration', (range) => {
    const view = D().orchestration[range];
    if (!view) throw new Error(`bad range ${range}`);
    return view;
  });
})();
