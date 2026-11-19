// Test-only: extends the fake pywebview bridge (conftest.stub_script) with the Manager data API
// used by the overview / projects / sessions / session pages. window.__tvOvData (built in
// tests/ui/conftest_overview.py) holds a list of sessions; this stub filters and aggregates them
// like manager/aggregates.py would. Every call is recorded in window.__tvCalls.
// window.__tvFail = {method: true} makes that method reject; window.__tvOvDelay delays answers.
(() => {
  const api = window.pywebview && window.pywebview.api;
  if (!api) return;
  const calls = window.__tvCalls || (window.__tvCalls = []);
  const clone = (v) => JSON.parse(JSON.stringify(v ?? null));
  const rec = (name, fn) => (...args) => {
    calls.push({ name, args: clone(args) });
    if (window.__tvFail && window.__tvFail[name]) {
      return Promise.reject(new Error(`stub failure: ${name}`));
    }
    const delay = Number(window.__tvOvDelay || 0);
    let value;
    try {
      value = clone(fn(...args));
    } catch (err) {
      return Promise.reject(err);
    }
    return delay ? new Promise((r) => setTimeout(() => r(value), delay)) : Promise.resolve(value);
  };
  const D = () => window.__tvOvData;
  const TOK = [
    'input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_create_5m_tokens',
    'cache_create_1h_tokens', 'total_tokens',
  ];
  const zero = () => Object.fromEntries(TOK.map((k) => [k, 0]));
  const add = (acc, s) => {
    for (const k of TOK) acc[k] += s[k] || 0;
    return acc;
  };
  const STUB_ONLY = ['team', 'day', 'tools', 'turn_count'];
  const row = (s) => {
    const out = { ...s };
    for (const k of STUB_ONLY) delete out[k];
    return out;
  };

  function filtered(f = {}) {
    return D().sessions.filter((s) =>
      (!f.team || s.team === f.team) &&
      (!f.email || s.employee_key === f.email) &&
      (f.project === null || f.project === undefined || s.projects.includes(f.project)) &&
      (!f.model || s.models.includes(f.model)) &&
      (!f.start || s.day >= f.start) &&
      (!f.end || s.day <= f.end));
  }

  function costOf(list) {
    const unknown = new Set();
    let cost = 0;
    let priced = 0;
    for (const s of list) {
      for (const m of s.models) if (D().unpriced.includes(m)) unknown.add(m);
      if (s.cost !== null) {
        cost += s.cost;
        priced += 1;
      }
    }
    const partial = unknown.size > 0 && priced > 0;
    return {
      cost: list.length && !priced ? null : Math.round(cost * 1e6) / 1e6,
      cost_partial: partial,
      unknown_cost_models: [...unknown].sort(),
    };
  }

  function groupBy(list, keyOf) {
    const map = new Map();
    for (const s of list) {
      const k = keyOf(s);
      if (!map.has(k)) map.set(k, []);
      map.get(k).push(s);
    }
    return map;
  }

  const pad = (n) => String(n).padStart(2, '0');
  const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  function daysBetween(start, end) {
    const out = [];
    const d = new Date(`${start}T00:00:00`);
    const last = new Date(`${end}T00:00:00`);
    while (d <= last) {
      out.push(iso(d));
      d.setDate(d.getDate() + 1);
    }
    return out;
  }

  function projectRows(list) {
    return [...groupBy(list, (s) => s.project)].map(([project, items]) => ({
      ...items.reduce(add, zero()),
      project,
      events: items.reduce((a, s) => a + s.events, 0),
      sessions: items.length,
      employees: new Set(items.map((s) => s.employee_key)).size,
      ...costOf(items),
      last_active: items.map((s) => s.last_at).sort().pop() || null,
    })).sort((a, b) => b.total_tokens - a.total_tokens);
  }

  function overview(f = {}) {
    const list = filtered(f);
    const end = f.end || D().today;
    const first = list.map((s) => s.day).sort()[0] || end;
    const start = f.start || first;
    let days = daysBetween(start, end);
    const months = [...new Set(days.map((d) => d.slice(0, 7)))];
    days = days.slice(-90);
    const byDay = groupBy(list, (s) => s.day);
    const daily = days.map((day) => {
      const items = byDay.get(day) || [];
      return { ...items.reduce(add, zero()), day, ...costOf(items) };
    });
    const monthly = months.map((month) => {
      const items = list.filter((s) => s.day.slice(0, 7) === month);
      return { ...items.reduce(add, zero()), month, ...costOf(items) };
    });
    const cache_daily = daily.map((d) => ({
      day: d.day,
      cache_read_tokens: d.cache_read_tokens,
      cache_create_5m_tokens: d.cache_create_5m_tokens,
      cache_create_1h_tokens: d.cache_create_1h_tokens,
      hit_ratio: d.total_tokens ? d.cache_read_tokens / d.total_tokens : 0,
    }));
    const hourly = [];
    for (let h = 0; h < 24; h += 1) {
      const items = list.filter((s) => s.day === end && Number(s.started_at.slice(11, 13)) === h);
      hourly.push({ ...items.reduce(add, zero()), hour: `${end}T${pad(h)}:00`, ...costOf(items) });
    }
    const by_model = [...groupBy(list, (s) => s.models[0])].map(([model, items]) => ({
      ...items.reduce(add, zero()),
      model,
      events: items.length,
      cost: D().unpriced.includes(model) ? null : costOf(items).cost,
    })).sort((a, b) => b.total_tokens - a.total_tokens);
    const tools = new Map();
    for (const s of list) for (const [tool, n] of Object.entries(s.tools)) tools.set(tool, (tools.get(tool) || 0) + n);
    const recent = [...list].sort((a, b) => (a.started_at < b.started_at ? 1 : -1)).slice(0, 10);
    const c = costOf(list);
    return {
      totals: {
        ...list.reduce(add, zero()),
        events: list.reduce((a, s) => a + s.events, 0),
        sessions: list.length,
        cost: c.cost,
        cost_partial: c.cost_partial,
      },
      hourly,
      daily,
      monthly,
      cache_daily,
      by_project: projectRows(list),
      by_model,
      top_tools: [...tools].map(([tool, n]) => ({ tool, calls: n })).sort((a, b) => b.calls - a.calls),
      recent_sessions: recent.map(row),
      unknown_cost_models: c.unknown_cost_models,
    };
  }

  function turnsFor(id) {
    const explicit = D().turns[id];
    if (explicit) return explicit;
    const s = D().sessions.find((x) => x.session_id === id);
    if (!s) throw new Error(`unknown session ${id}`);
    const t0 = new Date(s.started_at).getTime();
    const out = [];
    for (let n = 0; n < s.turn_count; n += 1) {
      const total = 2000 + ((n * 7919) % 30000);
      const ts = new Date(t0 + n * 15000);
      out.push({
        input_tokens: Math.round(total * 0.1),
        output_tokens: Math.round(total * 0.08),
        cache_read_tokens: Math.round(total * 0.7),
        cache_create_5m_tokens: Math.round(total * 0.07),
        cache_create_1h_tokens: 0,
        total_tokens: Math.round(total * 0.1) + Math.round(total * 0.08) + Math.round(total * 0.7) + Math.round(total * 0.07),
        timestamp: ts.toISOString(), // UTC instant, like the raw event timestamp
        device_id: s.device_id,
        model: s.models[n % s.models.length],
        reasoning_effort: null,
        is_sidechain: n % 11 === 3,
        tool_names: n % 4 === 0 ? ['Edit', 'Bash'] : [],
        cost: D().unpriced.includes(s.models[n % s.models.length]) ? null : 0.002,
      });
    }
    return out;
  }

  api.teams = rec('teams', () => D().teams);
  api.overview = rec('overview', (f) => overview(f || {}));
  api.projects = rec('projects', (f) => projectRows(filtered(f || {})));
  api.sessions = rec('sessions', (f) =>
    filtered(f || {}).sort((a, b) => (a.started_at < b.started_at ? 1 : -1)).map(row));
  api.session_turns = rec('session_turns', (id) => turnsFor(id));
  if (!D().noSessionMethod) {
    api.session = rec('session', (id) => {
      const s = D().sessions.find((x) => x.session_id === id);
      return s ? row(s) : null;
    });
  }
  api.employees = rec('employees', (f) => {
    const list = filtered({ ...(f || {}), email: null });
    return D().people
      .filter((p) => !f || !f.team || p.team === f.team)
      .map((p) => {
        const items = list.filter((s) => s.employee_key === p.key);
        return {
          ...items.reduce(add, zero()),
          key: p.key,
          email: p.key.startsWith('device:') ? null : p.key,
          display_name: p.display_name,
          devices: [],
          teams: [p.team],
          online: true,
          last_seen: null,
          events: 0,
          sessions: items.length,
          cost: costOf(items).cost,
          cost_partial: false,
          share: 0,
        };
      });
  });
})();
