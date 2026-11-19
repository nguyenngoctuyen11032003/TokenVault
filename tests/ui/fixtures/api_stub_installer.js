// Test-only: extends the fake pywebview bridge (conftest.stub_script) with the Manager
// management API used by the installer / settings / diagnostics pages (Task 17). State lives in
// window.__tvMgr (built in tests/ui/conftest_installer.py); tests may mutate it at runtime.
// Failure injection: window.__tvMgr.fail[method] = 'message' rejects every call of that method
// (`failOnce` rejects only the next one). window.__tvMgr.delay[method] = ms delays the answer.
// Every call is recorded in window.__tvCalls like the base stub does.
(() => {
  const api = window.pywebview && window.pywebview.api;
  if (!api) return;
  const calls = window.__tvCalls || (window.__tvCalls = []);
  const clone = (v) => JSON.parse(JSON.stringify(v ?? null));
  const M = () => window.__tvMgr;
  const rec = (name, fn) => (...args) => {
    calls.push({ name, args: clone(args) });
    const mgr = M();
    const once = mgr.failOnce && mgr.failOnce[name];
    if (once) delete mgr.failOnce[name];
    const failure = once || (mgr.fail && mgr.fail[name]);
    const delay = Number((mgr.delay && mgr.delay[name]) || 0);
    const run = () => {
      if (failure) throw new Error(failure);
      return clone(fn(...args));
    };
    return new Promise((resolve, reject) => {
      const go = () => {
        try {
          resolve(run());
        } catch (err) {
          reject(err);
        }
      };
      if (delay) setTimeout(go, delay);
      else go();
    });
  };

  const teamsOf = () => {
    const map = new Map();
    for (const d of M().devices) {
      const t = map.get(d.team) || { name: d.team, devices: 0, online: 0 };
      t.devices += 1;
      t.online += d.online ? 1 : 0;
      map.set(d.team, t);
    }
    return [...map.values()];
  };

  api.teams = rec('teams', () => teamsOf());
  api.devices = rec('devices', () => M().devices);
  api.set_device_team = rec('set_device_team', (id, team) => {
    const d = M().devices.find((x) => x.device_id === id);
    if (!d) throw new Error(`unknown device ${id}`);
    d.team = team;
    return { ok: true };
  });
  api.revoke_device = rec('revoke_device', (id) => {
    M().devices = M().devices.filter((x) => x.device_id !== id);
    return { ok: true };
  });
  api.reset_device = rec('reset_device', (id) => {
    if (!M().devices.some((x) => x.device_id === id)) throw new Error(`unknown device ${id}`);
    return { ok: true };
  });
  api.packages = rec('packages', () => M().packages);
  api.revoke_package = rec('revoke_package', (id) => {
    const p = M().packages.find((x) => x.package_id === id);
    if (!p) throw new Error(`unknown package ${id}`);
    p.revoked = true;
    return { ok: true };
  });
  api.create_installer = rec('create_installer', (team, label, target) => {
    const mgr = M();
    const fileName = target === 'mac' ? `TokenVault-Adapter-${team}-mac.zip`
      : target === 'windows-portable' ? `TokenVault-Adapter-${team}-portable.zip`
        : `TokenVault-Adapter-${team}.exe`;
    const path = `${mgr.outDir}\\${fileName}`;
    mgr.packages.unshift({
      package_id: `pkg-${mgr.packages.length + 1}`,
      team,
      label,
      created_at: '2026-11-19T10:30:00+07:00',
      revoked: false,
      devices: 0,
    });
    return { path, file_name: fileName, team, bytes: 18432000 };
  });
  api.reveal_in_explorer = rec('reveal_in_explorer', () => ({ ok: true }));
  // installer_hosts like Task 12: the configured list, or the detected one when it is empty.
  const withInstallerHosts = (st) => ({
    ...st,
    installer_hosts: st.installer_hosts ?? ((st.hosts || []).length ? st.hosts : (st.hosts_auto || [])),
  });
  api.settings = rec('settings', () => withInstallerHosts(M().settings));
  api.save_settings = rec('save_settings', (patch) => {
    const allowed = ['transparent_window', 'start_with_windows', 'hosts'];
    for (const key of Object.keys(patch || {})) {
      if (!allowed.includes(key)) throw new Error(`not allowed: ${key}`);
      M().settings[key] = patch[key];
    }
    return withInstallerHosts(M().settings);
  });
  api.diagnostics = rec('diagnostics', () => M().diagnostics);
  api.connectivity_test = rec('connectivity_test', () => M().connectivity);
  api.regenerate_certificate = rec('regenerate_certificate', () => {
    const fp = 'ab12cd34ef56ab78cd90ef12ab34cd56ef78ab90cd12ef34ab56cd78ef90ab12';
    M().settings.fingerprint = fp;
    M().diagnostics.fingerprint = fp;
    return { fingerprint: fp, warning: 'All adapters must be reinstalled.' };
  });
})();
