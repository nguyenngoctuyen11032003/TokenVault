// #/installer — create the employee installer (the owner's key flow), 3 steps in one panel:
//   1. team name (+ optional label), validated (it becomes part of a Windows file name);
//   2. api.create_installer(team, label|null) with a busy state; failures show a banner + retry
//      (errors are classified by their [code] prefix, see installer-errors.js);
//   3. result: file name, full path, "Open folder", "Copy path", copyable employee instructions
//      (SmartScreen "More info -> Run anyway", what is / is not sent, uninstall).
// Below: previously created packages with a confirmed "Revoke". Untrusted values -> textContent.

import { confirmDialog } from '../components/installer-dialog.js';
import { describeError } from '../components/installer-errors.js';
import {
  clipText,
  copyButton,
  debounce,
  errorBanner,
  errText,
  fmtWhen,
  h,
  LIVE_DEBOUNCE_MS,
  revCounter,
  shortFingerprint,
  TEAM_MAX,
  validateTeam,
} from '../components/installer-kit.js';

export default async function render(container, ctx) {
  const t = (key, params) => ctx.t(`installer.${key}`, params);
  await Promise.all([
    ctx.useStylesheet('css/pages/people.css'),
    ctx.useStylesheet('css/pages/installer-kit.css'),
    ctx.useStylesheet('css/pages/installer.css'),
    ctx.useStylesheet('css/pages/installer-result.css'),
  ]);
  if (ctx.signal.aborted) return;

  const root = h('div', { class: 'tv-installer', dataset: { tvRev: '0' } });
  container.append(root);
  const bump = revCounter(root);
  let dialogOpen = false;
  let last = { team: '', label: null };

  // ---------- step indicator ----------
  const steps = [1, 2, 3].map((n) =>
    h('li', { class: 'tv-installer-step', dataset: { step: String(n) } },
      h('span', { class: 'tv-installer-step__num', 'aria-hidden': 'true' }, String(n)),
      h('span', { class: 'tv-installer-step__text' }, t(`step${n}`))),
  );
  const stepper = h('ol', { class: 'tv-installer-steps', 'aria-label': t('stepsLabel') }, steps);
  function setStep(n) {
    steps.forEach((li, i) => {
      li.toggleAttribute('aria-current', false);
      li.classList.toggle('is-done', i + 1 < n);
      if (i + 1 === n) li.setAttribute('aria-current', 'step');
    });
  }

  // ---------- step 1: form ----------
  const teamInput = h('input', {
    type: 'text', id: 'tv-installer-team', name: 'team', class: 'tv-installer-field__input',
    autocomplete: 'off', spellcheck: 'false', placeholder: t('teamPlaceholder'),
    'aria-describedby': 'tv-installer-team-hint tv-installer-team-error', required: true,
  });
  const teamError = h('p', { class: 'tv-installer-field__error', id: 'tv-installer-team-error', 'data-field-error': 'team', hidden: true });
  const labelInput = h('input', {
    type: 'text', id: 'tv-installer-label', name: 'label', class: 'tv-installer-field__input',
    autocomplete: 'off', maxlength: '60', placeholder: t('labelPlaceholder'), 'aria-describedby': 'tv-installer-label-hint',
  });
  const suggest = h('div', { class: 'tv-installer-suggest', role: 'group', 'aria-label': t('suggestLabel') });
  const generate = h('button', { type: 'submit', class: 'tv-btn tv-btn--primary tv-installer-generate', dataset: { action: 'generate' } }, t('generate'));
  const busyRow = h('div', { class: 'tv-installer-busy', role: 'status', hidden: true },
    h('span', { class: 'tv-installer-busy__bar', 'aria-hidden': 'true' }, h('i')),
    h('span', null, t('busyNote')));
  const errorSlot = h('div', { class: 'tv-installer-errslot' });

  const info = {
    hosts: h('dd', { class: 'tv-installer-info__hosts' }),
    port: h('dd', { class: 'tv-num--orange' }),
    fp: h('dd', { class: 'tv-installer-mono' }),
  };
  const infoBox = h('aside', { class: 'tv-installer-info', 'aria-label': t('contains') },
    h('h3', { class: 'tv-installer-info__title' }, t('contains')),
    h('dl', null,
      h('dt', null, t('containsHosts')), info.hosts,
      h('dt', null, t('containsPort')), info.port,
      h('dt', null, t('containsFingerprint')), info.fp,
      h('dt', null, t('containsSecret')), h('dd', null, t('containsSecretValue'))),
    h('p', { class: 'tv-installer-info__note' }, t('containsNote')));

  const form = h('form', { class: 'tv-installer-form', novalidate: true },
    h('div', { class: 'tv-installer-field' },
      h('label', { class: 'tv-installer-field__label', for: 'tv-installer-team' }, t('teamLabel')),
      teamInput,
      h('p', { class: 'tv-installer-field__hint', id: 'tv-installer-team-hint' }, t('teamHint', { max: TEAM_MAX })),
      teamError,
      suggest),
    h('div', { class: 'tv-installer-field' },
      h('label', { class: 'tv-installer-field__label', for: 'tv-installer-label' }, t('labelLabel')),
      labelInput,
      h('p', { class: 'tv-installer-field__hint', id: 'tv-installer-label-hint' }, t('labelHint'))),
    h('div', { class: 'tv-installer-form__actions' }, generate, busyRow));
  const formView = h('div', { class: 'tv-installer-formview' }, h('div', null, errorSlot, form), infoBox);

  // ---------- step 3: result ----------
  const res = {
    file: h('p', { class: 'tv-installer-result__file', 'data-result-file': '' }),
    path: h('code', { class: 'tv-installer-mono tv-installer-result__path', 'data-result-path': '' }),
    size: h('dd'),
    lead: h('p', { class: 'tv-installer-result__lead' }),
    instructions: h('pre', { class: 'tv-installer-instructions', 'data-instructions': '', tabindex: '0' }),
    revealError: h('p', { class: 'tv-installer-field__error', role: 'alert', hidden: true }),
  };
  let resultPath = '';
  const copyLabels = { copiedLabel: t('copied'), failedLabel: t('copyFailed'), hint: t('copyHint') };
  const doneView = h('div', { class: 'tv-installer-doneview', hidden: true },
    h('div', { class: 'tv-installer-result' },
      h('div', { class: 'tv-installer-result__mark', 'aria-hidden': 'true' }),
      h('div', { class: 'tv-installer-result__main' },
        h('h3', { class: 'tv-installer-result__title', tabindex: '-1' }, t('doneTitle')),
        res.lead,
        res.file,
        h('dl', { class: 'tv-installer-result__meta' },
          h('dt', null, t('pathLabel')), h('dd', null, res.path),
          h('dt', null, t('sizeLabel')), res.size),
        h('div', { class: 'tv-installer-result__actions' },
          h('button', { type: 'button', class: 'tv-btn tv-btn--primary', dataset: { action: 'reveal' }, on: { click: reveal } }, t('openFolder')),
          copyButton({ label: t('copyPath'), ...copyLabels, action: 'copy-path', getText: () => resultPath, target: () => res.path }),
          h('button', { type: 'button', class: 'tv-btn tv-installer-again', dataset: { action: 'again' }, on: { click: reset } }, t('again'))),
        res.revealError)),
    h('div', { class: 'tv-installer-instrbox' },
      h('div', { class: 'tv-meta tv-installer-instrbox__head' },
        h('h3', { class: 'tv-installer-instrbox__title' }, t('instructionsTitle')),
        copyButton({ label: t('copyInstructions'), ...copyLabels, action: 'copy-instructions', getText: () => res.instructions.textContent, target: () => res.instructions })),
      h('p', { class: 'tv-installer-smartscreen' }, t('smartscreenNote')),
      res.instructions));

  const wizard = h('div', { class: 'tv-installer-wizard', dataset: { state: 'form' } }, formView, doneView);
  const wizardPanel = h('tv-panel', { heading: t('title'), class: 'tv-installer-panel' },
    h('p', { class: 'tv-installer-lead' }, t('lead')), stepper, wizard);

  // ---------- packages ----------
  const pkgBody = h('tbody');
  const pkgTable = h('table', { class: 'tv-table tv-installer-packages', 'aria-label': t('packagesLabel') },
    h('colgroup', null, ['team', 'label', 'created', 'devices', 'status', 'action'].map((c) => h('col', { class: `tv-installer-packages__col--${c}` }))),
    h('thead', null, h('tr', null,
      h('th', { scope: 'col' }, t('col.team')), h('th', { scope: 'col' }, t('col.label')),
      h('th', { scope: 'col' }, t('col.created')), h('th', { scope: 'col', class: 'tv-num' }, t('col.devices')),
      h('th', { scope: 'col' }, t('col.status')), h('td'))),
    pkgBody);
  const pkgEmpty = h('p', { class: 'tv-muted tv-installer-empty', 'data-packages-empty': '', hidden: true }, t('packagesEmpty'));
  const pkgError = h('div');
  const pkgPanel = h('tv-panel', { heading: t('packagesTitle'), class: 'tv-installer-pkgpanel' },
    pkgError, h('div', { class: 'tv-installer-tablewrap' }, pkgTable), pkgEmpty);

  root.append(wizardPanel, pkgPanel);
  setStep(1);

  // ---------- behaviour ----------
  function setState(state) {
    wizard.dataset.state = state;
    const busy = state === 'busy';
    teamInput.disabled = busy;
    labelInput.disabled = busy;
    generate.disabled = busy;
    generate.textContent = busy ? t('generating') : t('generate');
    suggest.querySelectorAll('button').forEach((b) => { b.disabled = busy; });
    busyRow.hidden = !busy;
    formView.hidden = state === 'done';
    doneView.hidden = state !== 'done';
    setStep(state === 'form' ? 1 : state === 'busy' ? 2 : 3);
  }

  function showTeamError(reason) {
    teamError.dataset.reason = reason;
    teamError.textContent = t(`err.${reason}`, { max: TEAM_MAX });
    teamError.hidden = false;
    teamInput.setAttribute('aria-invalid', 'true');
  }
  function clearTeamError() {
    teamError.hidden = true;
    teamInput.removeAttribute('aria-invalid');
  }
  teamInput.addEventListener('input', clearTeamError);

  function instructionsText(team, file) {
    return [1, 2, 3, 4, 5, 6, 7].map((n) => t(`instr.l${n}`, { team, file })).join('\n');
  }

  async function create(team, label) {
    last = { team, label };
    errorSlot.replaceChildren();
    setState('busy');
    try {
      const result = await ctx.api.create_installer(team, label);
      if (ctx.signal.aborted) return;
      const file = String(result?.file_name ?? '');
      resultPath = String(result?.path ?? '');
      res.file.textContent = file;
      res.path.textContent = resultPath;
      res.path.title = resultPath;
      const mb = Number(result?.bytes) / (1024 * 1024);
      res.size.textContent = Number.isFinite(mb) && mb > 0 ? t('sizeMb', { mb: mb.toFixed(1) }) : '';
      res.lead.textContent = t('doneLead', { team: String(result?.team ?? team) });
      res.instructions.textContent = instructionsText(String(result?.team ?? team), file);
      res.revealError.hidden = true;
      setState('done');
      doneView.querySelector('.tv-installer-result__title').focus({ preventScroll: true });
      loadPackages();
    } catch (err) {
      if (ctx.signal.aborted) return;
      console.warn('[installer] create_installer failed', err);
      const { code, text } = describeError(
        err,
        { template_missing: t('templateMissing'), invalid_team: t('err.server') },
        (detail) => t('createError', { detail }),
      );
      setState('form');
      if (code === 'invalid_team') {
        showTeamError('server');
        teamInput.focus();
      } else {
        errorSlot.replaceChildren(errorBanner({
          text,
          retryLabel: t('retry'),
          onRetry: () => create(last.team, last.label),
          attr: 'data-create-error',
          kind: code === 'template_missing' ? 'template' : 'generic',
        }));
      }
    }
    bump();
  }

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    if (wizard.dataset.state === 'busy') return;
    const check = validateTeam(teamInput.value);
    if (!check.ok) {
      showTeamError(check.reason);
      teamInput.focus();
      return;
    }
    clearTeamError();
    teamInput.value = check.value;
    const label = labelInput.value.trim();
    create(check.value, label || null);
  });

  async function reveal() {
    try {
      res.revealError.hidden = true;
      await ctx.api.reveal_in_explorer(resultPath);
    } catch (err) {
      console.warn('[installer] reveal_in_explorer failed', err);
      res.revealError.textContent = t('revealError', { detail: errText(err) });
      res.revealError.hidden = false;
    }
  }

  function reset() {
    errorSlot.replaceChildren();
    teamInput.value = '';
    labelInput.value = '';
    clearTeamError();
    setState('form');
    teamInput.focus();
  }

  function renderSuggestions(teams) {
    const valid = (Array.isArray(teams) ? teams : [])
      .filter((x) => validateTeam(x?.name).ok && validateTeam(x?.name).value === x.name);
    suggest.hidden = !valid.length;
    suggest.replaceChildren(
      h('span', { class: 'tv-installer-suggest__label' }, t('suggestLabel')),
      ...valid.map((x) => h('button', {
        type: 'button', class: 'tv-installer-chip', dataset: { team: x.name }, title: x.name,
        on: { click: () => { teamInput.value = x.name; clearTeamError(); teamInput.focus(); } },
      }, clipText('span', 'tv-installer-chip__name', x.name),
      h('small', null, t('suggestCount', { count: Number(x.devices) || 0 })))),
    );
  }

  function renderInfo(settings) {
    const hosts = Array.isArray(settings?.hosts) ? settings.hosts : [];
    info.hosts.replaceChildren(...hosts.map((x) => clipText('span', 'tv-installer-info__host', x)));
    info.port.textContent = settings?.port ? String(settings.port) : '';
    info.fp.textContent = shortFingerprint(settings?.fingerprint);
    info.fp.title = String(settings?.fingerprint ?? '');
    infoBox.hidden = !settings;
  }

  function packageRow(p) {
    const revoked = !!p.revoked;
    const team = String(p.team ?? '');
    const label = p.label ? String(p.label) : '';
    return h('tr', { dataset: { package: String(p.package_id), revoked: String(revoked) } },
      h('td', null, clipText('span', 'tv-installer-pkgname', team)),
      h('td', null, label ? clipText('span', 'tv-installer-pkglabel', label) : h('span', { class: 'tv-muted' }, t('noLabel'))),
      h('td', { class: 'tv-installer-when' }, fmtWhen(p.created_at)),
      h('td', { class: 'tv-num' }, String(Number(p.devices) || 0)),
      h('td', null, h('span', { class: `tv-installer-badge ${revoked ? 'is-revoked' : 'is-active'}` }, revoked ? t('statusRevoked') : t('statusActive'))),
      h('td', { class: 'tv-installer-rowaction' }, revoked ? null : h('button', {
        type: 'button', class: 'tv-installer-linkbtn', dataset: { action: 'revoke' },
        'aria-label': t('revokeAria', { team }), on: { click: () => revokePackage(p) },
      }, t('revoke'))));
  }

  async function revokePackage(p) {
    dialogOpen = true;
    const ok = await confirmDialog({
      title: t('revokeTitle'),
      body: [
        t('revokeBody', { team: String(p.team ?? ''), label: p.label ? String(p.label) : t('noLabel'), created: fmtWhen(p.created_at) }),
        t('revokeDevices', { count: Number(p.devices) || 0 }),
      ],
      confirmLabel: t('revokeConfirm'),
      cancelLabel: t('cancel'),
      danger: true,
      signal: ctx.signal,
    });
    dialogOpen = false;
    if (!ok || ctx.signal.aborted) return;
    try {
      await ctx.api.revoke_package(p.package_id);
    } catch (err) {
      console.warn('[installer] revoke_package failed', err);
      const { code, text } = describeError(err, { revoked: t('alreadyRevoked') }, (detail) => t('revokeError', { detail }));
      if (code !== 'revoked') {
        pkgError.replaceChildren(errorBanner({ text, retryLabel: t('retry'), onRetry: () => revokePackage(p) }));
        bump();
        return;
      }
      await loadPackages(); // already revoked elsewhere: show the current state + a notice
      pkgError.replaceChildren(errorBanner({ text, attr: 'data-revoke-notice', kind: 'revoked' }));
      bump();
      return;
    }
    await loadPackages();
  }

  async function loadPackages() {
    try {
      const list = await ctx.api.packages();
      if (ctx.signal.aborted) return;
      const rows = Array.isArray(list) ? list : [];
      pkgError.replaceChildren();
      pkgBody.replaceChildren(...rows.map(packageRow));
      pkgTable.hidden = !rows.length;
      pkgEmpty.hidden = rows.length > 0;
    } catch (err) {
      if (ctx.signal.aborted) return;
      console.warn('[installer] packages failed', err);
      pkgError.replaceChildren(errorBanner({ text: t('loadError', { detail: errText(err) }), retryLabel: t('retry'), onRetry: () => load() }));
    }
    bump();
  }

  async function load() {
    const [teams, settings] = await Promise.all([
      ctx.api.teams().catch((err) => { console.warn('[installer] teams failed', err); return []; }),
      ctx.api.settings().catch((err) => { console.warn('[installer] settings failed', err); return null; }),
    ]);
    if (ctx.signal.aborted) return;
    renderSuggestions(teams);
    renderInfo(settings);
    await loadPackages();
  }

  ctx.onLive(debounce(() => { if (!dialogOpen) loadPackages(); }, LIVE_DEBOUNCE_MS, ctx.signal));
  await load();
}
