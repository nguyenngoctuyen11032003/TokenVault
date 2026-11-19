// <tv-gauge value="4820000" max="8000000" label="${t('home.tokens')}" unit="TOKEN" display="4.82M">
// NitroSense radial gauge: a 270° ring of tick marks, the ticks up to value/max lit red, and a
// glowing inner face showing the number. `display` overrides the formatted value (e.g. "$61.4"
// or "UNKNOWN"); a non-numeric value shows an empty arc. Exposes --tv-gauge-p (0..1) on
// .tv-gauge__dial for CSS, and role="meter" + aria-value* for assistive tech.

import { compactNumber } from '../dom.js';
import { t } from '../i18n.js';

export class TvGauge extends HTMLElement {
  static get observedAttributes() {
    return ['value', 'max', 'min', 'label', 'unit', 'display'];
  }

  connectedCallback() {
    if (!this._dial) this._build();
    this._update();
  }

  attributeChangedCallback() {
    if (this._dial) this._update();
  }

  _build() {
    this.innerHTML = `
      <div class="tv-gauge__dial">
        <div class="tv-gauge__ticks" aria-hidden="true"></div>
        <div class="tv-gauge__ticks tv-gauge__ticks--lit" aria-hidden="true"></div>
        <div class="tv-gauge__arc" aria-hidden="true"></div>
        <div class="tv-gauge__face">
          <b class="tv-gauge__value"></b>
          <span class="tv-gauge__unit"></span>
        </div>
      </div>
      <div class="tv-gauge__label"></div>`;
    this._dial = this.querySelector('.tv-gauge__dial');
    this._value = this.querySelector('.tv-gauge__value');
    this._unit = this.querySelector('.tv-gauge__unit');
    this._label = this.querySelector('.tv-gauge__label');
    this.setAttribute('role', 'meter');
  }

  /** Fraction of the arc to light, clamped to [0, 1]; 0 for missing/invalid numbers. */
  get fraction() {
    const value = Number.parseFloat(this.getAttribute('value'));
    const min = Number.parseFloat(this.getAttribute('min') ?? '0') || 0;
    const max = Number.parseFloat(this.getAttribute('max') ?? '100');
    if (!Number.isFinite(value) || !Number.isFinite(max) || max <= min) return 0;
    return Math.min(1, Math.max(0, (value - min) / (max - min)));
  }

  _update() {
    const raw = this.getAttribute('value');
    const numeric = Number.parseFloat(raw);
    const valid = raw !== null && raw.trim() !== '' && Number.isFinite(Number(raw));
    const p = this.fraction;
    this._dial.style.setProperty('--tv-gauge-p', String(Number(p.toFixed(4))));
    this.dataset.level = p >= 0.9 ? 'high' : p > 0 ? 'mid' : 'empty';

    const display = this.getAttribute('display');
    this._value.textContent =
      display ?? (valid ? compactNumber(numeric) : t('gauge.unknown'));
    this._unit.textContent = this.getAttribute('unit') ?? '';
    this._unit.hidden = !this._unit.textContent;
    const label = this.getAttribute('label') ?? '';
    this._label.textContent = label;
    this._label.hidden = !label;

    if (label) this.setAttribute('aria-label', label);
    else this.removeAttribute('aria-label');
    this.setAttribute('aria-valuemin', this.getAttribute('min') ?? '0');
    this.setAttribute('aria-valuemax', this.getAttribute('max') ?? '100');
    if (valid) {
      this.setAttribute('aria-valuenow', String(numeric));
      this.setAttribute('aria-valuetext', this._value.textContent);
    } else {
      this.removeAttribute('aria-valuenow');
      this.setAttribute('aria-valuetext', this._value.textContent);
    }
  }
}

if (!customElements.get('tv-gauge')) customElements.define('tv-gauge', TvGauge);
