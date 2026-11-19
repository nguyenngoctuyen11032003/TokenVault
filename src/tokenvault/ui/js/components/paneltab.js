// <tv-panel heading="${t('home.today')}"> ... </tv-panel>
// Chamfered panel with a NitroSense angled tab. `title` is accepted as an alias of `heading`
// and converted (a bare `title` attribute would show a native tooltip over the whole panel).
// Children with slot="actions" are placed on the tab row, right-aligned; everything else goes
// into the body. Light DOM (no shadow root) so theme and page CSS apply directly.

let seq = 0;

export class TvPanel extends HTMLElement {
  static get observedAttributes() {
    return ['heading', 'title'];
  }

  connectedCallback() {
    if (!this._built) this._build();
    this._sync();
  }

  attributeChangedCallback(name, _old, value) {
    if (name === 'title' && value !== null) {
      this.removeAttribute('title');
      this.setAttribute('heading', value);
      return;
    }
    if (this._built) this._sync();
  }

  _build() {
    this._built = true;
    if (this.hasAttribute('title')) {
      this.setAttribute('heading', this.getAttribute('title'));
      this.removeAttribute('title');
    }
    const id = `tv-panel-h-${++seq}`;
    const head = document.createElement('div');
    head.className = 'tv-panel__head';
    const tab = document.createElement('h2');
    tab.className = 'tv-panel__tab';
    tab.id = id;
    const actions = document.createElement('div');
    actions.className = 'tv-panel__actions';
    head.append(tab, actions);

    const body = document.createElement('div');
    body.className = 'tv-panel__body';
    for (const child of [...this.childNodes]) {
      if (child.nodeType === 1 && child.getAttribute('slot') === 'actions') actions.append(child);
      else body.append(child);
    }
    this.append(head, body);
    this._tab = tab;
    this._body = body;
    if (!this.hasAttribute('role')) this.setAttribute('role', 'region');
  }

  _sync() {
    const heading = this.getAttribute('heading') || '';
    this._tab.textContent = heading;
    this._tab.hidden = !heading;
    this.classList.toggle('tv-panel--untitled', !heading);
    if (heading) this.setAttribute('aria-labelledby', this._tab.id);
    else this.removeAttribute('aria-labelledby');
  }

  /** The element page code should fill (children appended later should go here). */
  get body() {
    if (!this._built) this._build();
    return this._body;
  }
}

if (!customElements.get('tv-panel')) customElements.define('tv-panel', TvPanel);
