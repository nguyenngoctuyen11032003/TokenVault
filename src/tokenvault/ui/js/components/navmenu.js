// <tv-navmenu> — left navigation built from the js/nav.js manifest.
// Plain links (Tab / Enter work natively); ArrowUp/ArrowDown/Home/End move focus between items.
// The active item gets aria-current="page"; detail routes highlight their parent (nav `also`).

import { t } from '../i18n.js';
import { NAV, navEntryFor } from '../nav.js';
import { parseHash } from '../router.js';
import { icon } from './icons.js';

export class TvNavmenu extends HTMLElement {
  connectedCallback() {
    if (this._built) return;
    this._built = true;
    this.innerHTML = `
      <nav class="tv-nav" aria-label="${t('nav.label')}">
        <ul class="tv-nav__list">
          ${NAV.map(
            (item) => `
            <li>
              <a class="tv-nav__item" href="#/${item.route}" data-route="${item.route}">
                <span class="tv-nav__ring">${icon(item.icon)}</span>
                <span class="tv-nav__label">${t(item.i18nKey)}</span>
              </a>
            </li>`,
          ).join('')}
        </ul>
      </nav>`;
    this._onHash = () => this.sync();
    window.addEventListener('hashchange', this._onHash);
    this.addEventListener('keydown', (ev) => this._onKey(ev));
    this.sync();
  }

  disconnectedCallback() {
    window.removeEventListener('hashchange', this._onHash);
    this._built = false;
  }

  sync() {
    const entry = navEntryFor(parseHash(location.hash).name);
    for (const link of this.querySelectorAll('.tv-nav__item')) {
      if (entry && link.dataset.route === entry.route) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    }
  }

  _onKey(ev) {
    const items = [...this.querySelectorAll('.tv-nav__item')];
    const index = items.indexOf(document.activeElement);
    if (index < 0) return;
    const next = {
      ArrowDown: (index + 1) % items.length,
      ArrowUp: (index - 1 + items.length) % items.length,
      Home: 0,
      End: items.length - 1,
    }[ev.key];
    if (next === undefined) return;
    ev.preventDefault();
    items[next].focus();
  }
}

if (!customElements.get('tv-navmenu')) customElements.define('tv-navmenu', TvNavmenu);
