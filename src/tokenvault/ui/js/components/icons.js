// Inline stroke icons (24x24, currentColor). Decorative: always rendered with aria-hidden.

const P = {
  home: '<path d="M4 11.5 12 5l8 6.5"/><path d="M6.5 10v8.5h11V10"/><path d="M10.5 18.5v-4.5h3v4.5"/>',
  users:
    '<circle cx="9" cy="9" r="3.2"/><path d="M3.5 18.5c.7-3 2.9-4.6 5.5-4.6s4.8 1.6 5.5 4.6"/><path d="M15.5 6.2a3 3 0 0 1 0 5.6"/><path d="M17 14.2c1.8.5 3 1.9 3.5 4.3"/>',
  folder: '<path d="M3.5 7.5v10.5h17V9H12l-2-2.5H4.5z"/><path d="M3.5 11h17"/>',
  sessions:
    '<rect x="4" y="5" width="16" height="14" rx="1"/><path d="M4 9h16"/><path d="M8 13h5M8 16h8"/>',
  skills: '<path d="M13 3.5 6 13.5h5l-1 7 7-10h-5z"/>',
  orchestration:
    '<circle cx="12" cy="5.5" r="2"/><circle cx="5.5" cy="18" r="2"/><circle cx="18.5" cy="18" r="2"/><path d="M12 7.5v4M12 11.5 6.5 16M12 11.5l5.5 4.5"/>',
  installer:
    '<path d="M12 3.5v10"/><path d="m8 9.5 4 4 4-4"/><path d="M4.5 14.5v5h15v-5"/>',
  settings:
    '<circle cx="12" cy="12" r="3"/><path d="M12 3v2.2M12 18.8V21M3 12h2.2M18.8 12H21M5.6 5.6l1.6 1.6M16.8 16.8l1.6 1.6M5.6 18.4l1.6-1.6M16.8 7.2l1.6-1.6"/>',
  diagnostics: '<path d="M3 12h4l2.2-5.5 4.2 11 2.4-5.5H21"/>',
  minimize: '<path d="M6 12.5h12"/>',
  maximize: '<rect x="6.5" y="6.5" width="11" height="11"/>',
  restore: '<rect x="5.5" y="9" width="9.5" height="9.5"/><path d="M9 9V5.5h9.5V15H15"/>',
  close: '<path d="m7 7 10 10M17 7 7 17"/>',
  page: '<rect x="5" y="3.5" width="14" height="17" rx="1"/><path d="M8.5 8h7M8.5 12h7M8.5 16h4"/>',
};

export function icon(name, className = 'tv-icon') {
  const body = P[name] ?? P.page;
  return `<svg class="${className}" viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${body}</svg>`;
}

export const ICON_NAMES = Object.keys(P);
