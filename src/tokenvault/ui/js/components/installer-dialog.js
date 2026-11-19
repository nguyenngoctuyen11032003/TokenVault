// Modal confirm / prompt dialog for the installer, settings and diagnostics pages (Task 17).
//
//   const ok = await confirmDialog({ title, body, confirmLabel, cancelLabel, danger: true,
//                                    check: 'I understand ...', signal: ctx.signal });
//   const team = await confirmDialog({ title, confirmLabel, cancelLabel,
//                                      input: { label, value, validate: (v) => errorText|'' } });
//
// Resolves to false/null on cancel (Escape, Cancel button, backdrop click, route left via
// `signal`), to true (or the input value) on confirm. role="dialog" + aria-modal, labelled and
// described, focus trapped inside, focus restored to the opener. All strings come from the
// caller (each page passes its own i18n strings); body text is set with textContent.

import { h } from './homekit.js';

let seq = 0;

const FOCUSABLE = 'button:not([disabled]), input:not([disabled]), select, textarea, [href], [tabindex]:not([tabindex="-1"])';

export function confirmDialog(opts) {
  const {
    title,
    body = [],
    confirmLabel,
    cancelLabel,
    danger = false,
    check = null,
    input = null,
    signal = null,
  } = opts;
  seq += 1;
  const id = `tv-installer-dlg-${seq}`;
  const opener = document.activeElement;
  const paragraphs = (Array.isArray(body) ? body : [body]).filter((x) => x !== null && x !== undefined && x !== '');

  return new Promise((resolve) => {
    const confirm = h(
      'button',
      { type: 'submit', class: `tv-btn ${danger ? 'tv-btn--primary tv-installer-dlg__danger' : 'tv-btn--primary'}`, dataset: { dlg: 'confirm' } },
      confirmLabel,
    );
    const cancel = h('button', { type: 'button', class: 'tv-btn', dataset: { dlg: 'cancel' } }, cancelLabel);

    let checkbox = null;
    let checkRow = null;
    if (check) {
      checkbox = h('input', { type: 'checkbox', id: `${id}-check`, dataset: { dlg: 'check' } });
      checkRow = h('label', { class: 'tv-installer-dlg__check', for: `${id}-check` }, checkbox, h('span', null, check));
      confirm.disabled = true;
      checkbox.addEventListener('change', () => {
        confirm.disabled = !checkbox.checked;
      });
    }

    let field = null;
    let fieldRow = null;
    let fieldError = null;
    if (input) {
      field = h('input', {
        type: 'text',
        id: `${id}-input`,
        class: 'tv-installer-field__input',
        autocomplete: 'off',
        spellcheck: 'false',
        maxlength: input.maxLength ?? null,
        'aria-describedby': `${id}-input-error`,
        dataset: { dlg: 'input' },
      });
      field.value = input.value ?? '';
      fieldError = h('p', { class: 'tv-installer-field__error', id: `${id}-input-error`, dataset: { dlg: 'error' }, hidden: true });
      fieldRow = h(
        'div',
        { class: 'tv-installer-field' },
        h('label', { class: 'tv-installer-field__label', for: `${id}-input` }, input.label),
        field,
        fieldError,
      );
      field.addEventListener('input', () => {
        fieldError.hidden = true;
        field.removeAttribute('aria-invalid');
      });
    }

    const form = h(
      'form',
      {
        class: `tv-installer-dlg${danger ? ' tv-installer-dlg--danger' : ''}`,
        role: 'dialog',
        'aria-modal': 'true',
        'aria-labelledby': `${id}-title`,
        'aria-describedby': paragraphs.length ? `${id}-body` : null,
        novalidate: true,
      },
      h('h2', { class: 'tv-installer-dlg__title', id: `${id}-title` }, title),
      paragraphs.length
        ? h('div', { class: 'tv-installer-dlg__body', id: `${id}-body` }, paragraphs.map((p) => h('p', null, p)))
        : null,
      fieldRow,
      checkRow,
      h('div', { class: 'tv-installer-dlg__actions' }, cancel, confirm),
    );
    const backdrop = h('div', { class: 'tv-installer-dlg-backdrop' }, form);

    let done = false;
    function close(result) {
      if (done) return;
      done = true;
      document.removeEventListener('keydown', onKey, true);
      signal?.removeEventListener('abort', onAbort);
      backdrop.remove();
      if (opener && opener.isConnected && typeof opener.focus === 'function') opener.focus();
      resolve(result);
    }
    const cancelled = input ? null : false;
    const onAbort = () => close(cancelled);

    function onKey(event) {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        close(cancelled);
        return;
      }
      if (event.key !== 'Tab') return;
      const items = [...form.querySelectorAll(FOCUSABLE)].filter((el) => el.getClientRects().length);
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      const inside = form.contains(document.activeElement);
      if (event.shiftKey && (document.activeElement === first || !inside)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !inside)) {
        event.preventDefault();
        first.focus();
      }
    }

    form.addEventListener('submit', (event) => {
      event.preventDefault();
      if (confirm.disabled) return;
      if (!input) {
        close(true);
        return;
      }
      const problem = input.validate ? input.validate(field.value) : '';
      if (problem) {
        fieldError.textContent = problem;
        fieldError.hidden = false;
        field.setAttribute('aria-invalid', 'true');
        field.focus();
        return;
      }
      close(input.normalize ? input.normalize(field.value) : field.value.trim());
    });
    cancel.addEventListener('click', () => close(cancelled));
    backdrop.addEventListener('mousedown', (event) => {
      if (event.target === backdrop) close(cancelled);
    });
    document.addEventListener('keydown', onKey, true);
    if (signal) {
      if (signal.aborted) {
        close(cancelled);
        return;
      }
      signal.addEventListener('abort', onAbort, { once: true });
    }
    document.body.append(backdrop);
    (field || checkbox || cancel).focus();
  });
}
