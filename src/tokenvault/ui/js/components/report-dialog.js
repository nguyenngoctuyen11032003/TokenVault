// PDF report export dialog (Task 22) and the export button used by the Employees, Employee,
// Home and Overview pages.
//
//   const btn = await createReportButton({ api: ctx.api, signal: ctx.signal, labelKey: 'button',
//                                          preset: () => ({ scope: 'all', range: '7d' }) });
//   panel.append(btn);                       // slot="actions" is set by the caller if needed
//   const result = await openReportDialog({ api, preset, signal });   // null when closed
//
// preset: { scope: 'all'|'team'|'employee', team, employee_keys: [], range, label }.
// The dialog never sends a file path: the destination is the default folder or the native save
// dialog, both decided by Python. Result / errors are classified by the `[code]` prefix of the
// error message (never by words). All strings: i18n/vi/report.json (`report.*`).

import { useStylesheet } from '../dom.js';
import { loadI18nExtension, t as tt } from '../i18n.js';
import { h } from './homekit.js';

const t = (key, params) => tt(`report.${key}`, params);
const FOCUSABLE = 'button:not([disabled]), input:not([disabled]), select:not([disabled]), [href]';
const RANGES = ['today', '7d', '30d', 'custom'];
const CUSTOM = /^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$/;
const POLL_MS = 350;
let seq = 0;

const prepare = () => Promise.all([
  loadI18nExtension('report'),
  useStylesheet('css/pages/installer-kit.css'),
  useStylesheet('css/pages/report-dialog.css'),
]);

/** `...: [empty_scope] text` -> 'empty_scope' (null when the message has no prefix). */
export function errorCode(err) {
  const m = /\[([a-z_]+)\]/.exec(String(err?.message ?? err ?? ''));
  return m ? m[1] : null;
}

function errorText(err) {
  const code = errorCode(err);
  const detail = String(err?.message ?? err ?? '').replace(/^.*?\[[a-z_]+\]\s*/, '');
  if (code && tt(`report.err.${code}`) !== `report.err.${code}`) return t(`err.${code}`, { detail });
  return t('err.generic', { detail: detail || String(err) });
}

export function normalizeRange(range) {
  if (RANGES.includes(range) && range !== 'custom') return { range };
  const m = CUSTOM.exec(String(range ?? ''));
  if (m) return { range: 'custom', start: m[1], end: m[2] };
  return { range: '30d', fellBack: true };
}

function fmtBytes(n) {
  const v = Number(n) || 0;
  return v >= 1048576 ? `${(v / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(v / 1024))} KB`;
}

export async function openReportDialog({ api, preset = {}, signal = null }) {
  await prepare();
  let options;
  try {
    options = await api.report_options();
  } catch (err) {
    options = { teams: [], can_pick_path: false, default_dir: '', max_employees: 200, _error: err };
  }
  seq += 1;
  const id = `tv-report-dlg-${seq}`;
  const opener = document.activeElement;
  const keys = Array.isArray(preset.employee_keys) ? preset.employee_keys : [];
  const start = normalizeRange(preset.range);

  return new Promise((resolve) => {
    // ---------- scope ----------
    const scopes = [{ id: 'all', label: t('scope.all') }];
    if ((options.teams || []).length) scopes.push({ id: 'team', label: t('scope.team') });
    if (keys.length) {
      scopes.push({ id: 'employee', label: keys.length === 1 && preset.single ? t('scope.one') : t('scope.selected', { n: keys.length }) });
    }
    let scope = scopes.some((s) => s.id === preset.scope) ? preset.scope : 'all';
    const teamSelect = h('select', { class: 'tv-report-dlg__select', 'aria-label': t('scope.teamLabel'), dataset: { report: 'team' } },
      (options.teams || []).map((name) => h('option', { value: name }, name)));
    if (preset.team && (options.teams || []).includes(preset.team)) teamSelect.value = preset.team;
    const scopeRadios = scopes.map((s) => h('input', { type: 'radio', name: `${id}-scope`, value: s.id, checked: s.id === scope, dataset: { report: 'scope' } }));

    // ---------- range ----------
    let range = start.range;
    const rangeRadios = RANGES.map((r) => h('input', { type: 'radio', name: `${id}-range`, value: r, checked: r === range, dataset: { report: 'range' } }));
    const startInput = h('input', { type: 'date', value: start.start ?? '', 'aria-label': t('range.start'), dataset: { report: 'start' } });
    const endInput = h('input', { type: 'date', value: start.end ?? '', 'aria-label': t('range.end'), dataset: { report: 'end' } });
    const rangeNote = h('p', { class: 'tv-report-dlg__path', hidden: !start.fellBack, dataset: { report: 'range-note' } }, t('range.fallback'));
    const customRow = h('div', { class: 'tv-report-dlg__custom', hidden: range !== 'custom' }, startInput, h('span', null, '–'), endInput);

    // ---------- options ----------
    const perEmployee = h('input', { type: 'checkbox', checked: true, dataset: { report: 'per-employee' } });
    const perRow = h('label', { class: 'tv-report-dlg__check' }, perEmployee, h('span', null, t('perEmployee')));
    const destRadios = [
      h('input', { type: 'radio', name: `${id}-dest`, value: 'default', checked: true, dataset: { report: 'dest' } }),
      h('input', { type: 'radio', name: `${id}-dest`, value: 'dialog', disabled: !options.can_pick_path, dataset: { report: 'dest' } }),
    ];

    // ---------- status ----------
    const capNote = h('p', { class: 'tv-report-dlg__error', role: 'status', hidden: true, dataset: { report: 'cap-note' } }, t('tooMany', { max: options.max_employees, n: keys.length }));
    const errorBox = h('p', { class: 'tv-report-dlg__error', role: 'alert', hidden: true, dataset: { report: 'error' } });
    const bar = h('progress', { max: 100, value: 0, 'aria-label': t('progress.label'), dataset: { report: 'progress' } });
    const progressMsg = h('span', { 'aria-live': 'polite' }, t('progress.start'));
    const progressRow = h('div', { class: 'tv-report-dlg__progress', hidden: true }, bar, progressMsg);
    const formBody = h('div', { class: 'tv-report-dlg__form' });
    const resultBox = h('div', { class: 'tv-report-dlg__result', hidden: true, dataset: { report: 'result' } });

    const choice = (input, label, extra) => h('label', { class: 'tv-report-dlg__choice' }, input, h('span', null, label), extra);
    const group = (legend, ...kids) => h('fieldset', { class: 'tv-report-dlg__group' }, h('legend', null, legend), ...kids);
    formBody.append(
      group(t('scope.legend'), scopes.map((s, i) => choice(scopeRadios[i], s.label, s.id === 'team' ? teamSelect : null))),
      group(t('range.legend'), h('div', { class: 'tv-report-dlg__ranges' }, RANGES.map((r, i) => choice(rangeRadios[i], t(`range.${r}`)))), customRow, rangeNote),
      perRow,
      group(t('dest.legend'),
        choice(destRadios[0], t('dest.default')),
        h('p', { class: 'tv-report-dlg__path', dataset: { report: 'default-dir' } }, options.default_dir || ''),
        choice(destRadios[1], t('dest.choose'))),
    );

    const submit = h('button', { type: 'submit', class: 'tv-btn tv-btn--primary', dataset: { report: 'export' } }, t('export'));
    const cancel = h('button', { type: 'button', class: 'tv-btn', dataset: { report: 'cancel' } }, t('cancel'));
    const actions = h('div', { class: 'tv-installer-dlg__actions' }, cancel, submit);
    const form = h('form', { class: 'tv-installer-dlg tv-report-dlg', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': `${id}-title`, novalidate: true },
      h('h2', { class: 'tv-installer-dlg__title', id: `${id}-title` }, t('title')),
      preset.label ? h('p', { class: 'tv-report-dlg__for' }, preset.label) : null,
      formBody, progressRow, capNote, errorBox, resultBox, actions);
    const backdrop = h('div', { class: 'tv-installer-dlg-backdrop' }, form);

    let busy = false;
    let finished = null; // last result, returned on close
    let done = false;
    let timer = null;

    function close() {
      if (done) return;
      done = true;
      clearInterval(timer);
      document.removeEventListener('keydown', onKey, true);
      signal?.removeEventListener('abort', onAbort);
      backdrop.remove();
      if (opener && opener.isConnected && typeof opener.focus === 'function') opener.focus();
      resolve(finished);
    }
    let aborted = false;
    const onAbort = () => {
      if (busy) aborted = true; // finish the running export first
      else close();
    };

    function onKey(event) {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        if (!busy) close();
        return;
      }
      if (event.key !== 'Tab') return;
      const items = [...form.querySelectorAll(FOCUSABLE)].filter((el) => el.getClientRects().length);
      if (!items.length) return;
      const inside = form.contains(document.activeElement);
      if (event.shiftKey && (document.activeElement === items[0] || !inside)) {
        event.preventDefault();
        items[items.length - 1].focus();
      } else if (!event.shiftKey && (document.activeElement === items[items.length - 1] || !inside)) {
        event.preventDefault();
        items[0].focus();
      }
    }

    const checked = (radios) => radios.find((r) => r.checked)?.value;
    function sync() {
      scope = checked(scopeRadios) ?? 'all';
      range = checked(rangeRadios) ?? '30d';
      teamSelect.disabled = scope !== 'team';
      customRow.hidden = range !== 'custom';
      perRow.hidden = scope === 'employee' && keys.length === 1;
      const over = scope === 'employee' && keys.length > (options.max_employees || Infinity);
      capNote.hidden = !over;
      if (!busy) submit.disabled = over;
    }
    form.addEventListener('change', sync);
    sync();

    function buildSpec() {
      const spec = { scope, range, per_employee_pages: perEmployee.checked, destination: checked(destRadios) ?? 'default' };
      if (scope === 'team') spec.team = teamSelect.value;
      if (scope === 'employee') spec.employee_keys = keys;
      if (range === 'custom') Object.assign(spec, { start: startInput.value, end: endInput.value });
      return spec;
    }

    function setBusy(on) {
      busy = on;
      progressRow.hidden = !on;
      submit.disabled = on;
      cancel.disabled = on;
      formBody.querySelectorAll('input, select').forEach((el) => { el.disabled = on || (el.value === 'dialog' && el.type === 'radio' && !options.can_pick_path); });
      if (!on) sync();
      form.setAttribute('aria-busy', String(on));
    }

    async function poll() {
      try {
        const p = await api.report_progress();
        if (!busy || !p?.running) return;
        bar.value = Math.round((Number(p.fraction) || 0) * 100);
        if (p.message) progressMsg.textContent = p.message;
      } catch { /* progress is cosmetic */ }
    }

    function showResult(res) {
      formBody.hidden = true;
      errorBox.hidden = true;
      const run = async (reveal) => {
        try {
          await api.open_path(res.path, reveal);
          errorBox.hidden = true;
        } catch (err) {
          errorBox.textContent = errorText(err);
          errorBox.hidden = false;
        }
      };
      const openFile = h('button', { type: 'button', class: 'tv-btn tv-btn--primary', dataset: { report: 'open-file' }, on: { click: () => run(false) } }, t('openFile'));
      const openFolder = h('button', { type: 'button', class: 'tv-btn', dataset: { report: 'open-folder' }, on: { click: () => run(true) } }, t('openFolder'));
      const closeBtn = h('button', { type: 'button', class: 'tv-btn', dataset: { report: 'close' }, on: { click: close } }, t('close'));
      resultBox.replaceChildren(
        h('p', { class: 'tv-report-dlg__done' }, t('done')),
        h('p', { class: 'tv-report-dlg__file', title: res.path, dataset: { report: 'file' } }, res.file_name),
        h('p', { class: 'tv-report-dlg__meta' }, t('doneMeta', { pages: res.pages, size: fmtBytes(res.bytes), range: res.range_label })),
        h('p', { class: 'tv-report-dlg__path', dataset: { report: 'folder' } }, res.folder),
        h('div', { class: 'tv-installer-dlg__actions' }, closeBtn, options.can_reveal ? openFolder : null, options.can_open ? openFile : null),
      );
      resultBox.hidden = false;
      actions.hidden = true;
      (options.can_open ? openFile : closeBtn).focus();
    }

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (busy) return;
      errorBox.hidden = true;
      bar.value = 0;
      progressMsg.textContent = t('progress.start');
      setBusy(true);
      timer = setInterval(poll, POLL_MS);
      try {
        const res = await api.export_report(buildSpec());
        finished = res;
        clearInterval(timer);
        setBusy(false);
        showResult(res);
        if (aborted) close();
      } catch (err) {
        clearInterval(timer);
        setBusy(false);
        if (errorCode(err) !== 'cancelled') {
          errorBox.textContent = errorText(err);
          errorBox.hidden = false;
        }
        submit.focus();
        if (aborted) close();
      }
    });
    cancel.addEventListener('click', close);
    backdrop.addEventListener('mousedown', (event) => {
      if (event.target === backdrop && !busy) close();
    });
    document.addEventListener('keydown', onKey, true);
    if (signal) {
      if (signal.aborted) return close();
      signal.addEventListener('abort', onAbort, { once: true });
    }
    if (options._error) {
      errorBox.textContent = errorText(options._error);
      errorBox.hidden = false;
    }
    document.body.append(backdrop);
    submit.focus();
    return undefined;
  });
}

/** A ".tv-btn" that opens the dialog with `preset()` evaluated at click time. */
export async function createReportButton({ api, signal, preset, labelKey = 'button', labelParams, attrs = {} }) {
  await prepare();
  let opening = false;
  return h('button', { type: 'button', class: 'tv-btn tv-report-btn', ...attrs, on: {
    click: () => {
      if (opening) return;
      opening = true;
      openReportDialog({ api, signal, preset: preset() }).finally(() => { opening = false; });
    },
  } }, t(labelKey, labelParams));
}
