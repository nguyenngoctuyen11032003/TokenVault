// Test-only: fake report API (report_options / export_report / report_progress / open_path) on
// top of the pywebview stub. State in window.__tvReport: {options, fail, delay, progress, result}.
(() => {
  const api = window.pywebview && window.pywebview.api;
  if (!api) return;
  const calls = window.__tvCalls || (window.__tvCalls = []);
  const clone = (v) => JSON.parse(JSON.stringify(v ?? null));
  const R = () => window.__tvReport;
  window.__tvReport = {
    options: { default_dir: 'C:\\Users\\x\\Documents\\TokenVault Reports', can_pick_path: true, can_open: true,
               can_reveal: true, teams: ['Dev', 'QA'], max_employees: 200, page_cap: 120, ranges: [] },
    fail: {}, delay: 0, progress: { running: true, fraction: 0.4, message: 'Đang vẽ biểu đồ' },
    result: { path: 'C:\\r\\TokenVault-BaoCao-TatCa.pdf', file_name: 'TokenVault-BaoCao-TatCa.pdf',
              folder: 'C:\\r', pages: 7, bytes: 250000, scope_label: 'Tất cả', range_label: '01/11 – 07/11' },
  };
  const rec = (name, fn) => (...args) => {
    calls.push({ name, args: clone(args) });
    const failure = R().fail[name];
    const wait = name === 'export_report' ? Number(R().delay || 0) : 0;
    return new Promise((resolve, reject) => setTimeout(() => {
      if (failure) reject(new Error(`${name}: ${failure}`));
      else resolve(clone(fn(...args)));
    }, wait));
  };
  api.report_options = rec('report_options', () => R().options);
  api.export_report = rec('export_report', () => R().result);
  api.report_progress = rec('report_progress', () => R().progress);
  api.open_path = rec('open_path', () => ({ ok: true }));
})();
