// <tv-titlebar> — custom frameless-window title bar.
// Left/center are pywebview drag regions (class `pywebview-drag-region`; pywebview matches the
// target or any ancestor, so buttons are kept OUTSIDE those regions). Right side: live device
// indicator (updated via tv.onLive), installer + settings shortcuts, minimise, maximise/restore
// and close (close hides the window to the tray — implemented in Python). Double-click on a drag
// region toggles maximise (not while full screen); the max button follows js/winstate.js.

import { t } from '../i18n.js';
import { deviceCounts, lastLive, onLive } from '../live.js';
import { closeWindow, minimize, toggleMaximize } from '../wincommands.js';
import * as winstate from '../winstate.js';
import { icon } from './icons.js';

export class TvTitlebar extends HTMLElement {
  connectedCallback() {
    if (this._built) return;
    this._built = true;
    this.innerHTML = `
      <div class="tv-titlebar__brand pywebview-drag-region">
        <span class="tv-titlebar__badge">${t('app.brand')}</span>
      </div>
      <div class="tv-titlebar__title pywebview-drag-region" role="heading" aria-level="1">
        <b>${t('app.titleBold')}</b><span>${t('app.titleLight')}</span>
      </div>
      <div class="tv-titlebar__tools">
        <div class="tv-titlebar__spacer pywebview-drag-region"></div>
        <div class="tv-live" data-state="waiting" role="status" aria-live="polite"
             aria-label="${t('live.label')}">
          <i class="tv-live__dot" aria-hidden="true"></i>
          <span class="tv-live__text">${t('live.waiting')}</span>
        </div>
        <div class="tv-titlebar__buttons" role="toolbar" aria-label="${t('titlebar.toolbar')}">
          <a class="tv-iconbtn" href="#/installer" aria-label="${t('titlebar.installer')}"
             title="${t('titlebar.installer')}">${icon('installer')}</a>
          <a class="tv-iconbtn" href="#/settings" aria-label="${t('titlebar.settings')}"
             title="${t('titlebar.settings')}">${icon('settings')}</a>
          <span class="tv-titlebar__sep" aria-hidden="true"></span>
          <button type="button" class="tv-iconbtn" data-action="min"
                  aria-label="${t('titlebar.minimize')}" title="${t('titlebar.minimize')}">${icon('minimize')}</button>
          <button type="button" class="tv-iconbtn" data-action="max"></button>
          <button type="button" class="tv-iconbtn tv-iconbtn--close" data-action="close"
                  aria-label="${t('titlebar.close')}" title="${t('titlebar.close')}">${icon('close')}</button>
        </div>
      </div>`;
    this._live = this.querySelector('.tv-live');
    this._liveText = this.querySelector('.tv-live__text');
    this.querySelector('[data-action="min"]').addEventListener('click', () => minimize());
    this.querySelector('[data-action="close"]').addEventListener('click', () => closeWindow());
    // Drag regions: marked once with tv-titlebar__drag; the pywebview class is only present while
    // the window is normal, so a drag can never displace a maximised / full-screen window.
    this._drags = [...this.querySelectorAll('.pywebview-drag-region')];
    this._drags.forEach((el) => el.classList.add('tv-titlebar__drag'));
    this._max = this.querySelector('[data-action="max"]');
    this._max.addEventListener('click', () => toggleMaximize());
    this.addEventListener('dblclick', (event) => {
      const target = event.target instanceof Element ? event.target : null;
      if (!target?.closest('.tv-titlebar__drag') || target.closest('button, a')) return;
      if (winstate.getState().fullscreen) return;
      toggleMaximize();
    });
    this._off = onLive((payload) => this._render(payload));
    if (lastLive()) this._render(lastLive());
    this._offWin = winstate.subscribe((state) => this._renderWindow(state));
    this._renderWindow(winstate.getState());
  }

  disconnectedCallback() {
    this._off?.();
    this._offWin?.();
    this._built = false;
  }

  _renderWindow(state) {
    const flat = state.maximized || state.fullscreen;
    this._drags.forEach((el) => el.classList.toggle('pywebview-drag-region', !flat));
    const key = flat ? 'restore' : 'maximize';
    if (this._max.dataset.icon === key) return;
    this._max.dataset.icon = key;
    this._max.setAttribute('aria-label', t(`titlebar.${key}`));
    this._max.title = t(`titlebar.${key}Tip`);
    this._max.innerHTML = icon(key);
  }

  _render(payload) {
    const counts = deviceCounts(payload);
    if (!counts) return;
    this._liveText.textContent = t('live.online', counts);
    this._live.dataset.state = counts.online > 0 ? 'online' : 'offline';
  }
}

if (!customElements.get('tv-titlebar')) customElements.define('tv-titlebar', TvTitlebar);
