// UI strings. Every visible string lives in i18n/<lang>.json; code only references keys.
//   t('nav.home')                       -> the Vietnamese label from i18n/vi.json
//   t('live.online', {online: 3, total: 4})
// Markup can use data-i18n / data-i18n-aria-label / data-i18n-title / data-i18n-placeholder
// attributes and call applyI18n(root) after inserting it.

let dict = {};
let lang = 'vi';

export async function loadI18n(language = 'vi') {
  const url = new URL(`../i18n/${language}.json`, import.meta.url);
  const response = await fetch(url);
  if (!response.ok) throw new Error(`i18n ${language}: HTTP ${response.status}`);
  dict = await response.json();
  lang = language;
  document.documentElement.lang = language;
  return dict;
}

const UNSAFE_KEYS = new Set(['__proto__', 'constructor', 'prototype']);

function isPlainObject(value) {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

/** Merge `source` into `target`, never touching prototype-related keys. */
export function deepMerge(target, source) {
  for (const key of Object.keys(source)) {
    if (UNSAFE_KEYS.has(key)) continue;
    const value = source[key];
    if (isPlainObject(value)) {
      const base = Object.prototype.hasOwnProperty.call(target, key) && isPlainObject(target[key])
        ? target[key]
        : {};
      target[key] = deepMerge(base, value);
    } else {
      target[key] = value;
    }
  }
  return target;
}

const loaded = new Set(); // successful extensions only; failures are retried next time

/**
 * Merge the optional per-page file i18n/<lang>/<name>.json. Only the `<name>` namespace is
 * accepted (other top-level keys are ignored with a warning) so a page cannot override shell
 * strings. A missing file or failed fetch is not cached: the next navigation tries again.
 * Resolves to true when strings for `name` are available.
 */
export async function loadI18nExtension(name) {
  const cacheKey = `${lang}/${name}`;
  if (loaded.has(cacheKey)) return true;
  try {
    const url = new URL(`../i18n/${lang}/${name}.json`, import.meta.url);
    const response = await fetch(url, { cache: 'no-store' });
    if (!response.ok) return false;
    const extra = await response.json();
    if (!isPlainObject(extra)) return false;
    const foreign = Object.keys(extra).filter((key) => key !== name);
    if (foreign.length) {
      console.warn(`[i18n] ${name}.json: ignoring keys outside "${name}.*": ${foreign.join(', ')}`);
    }
    if (isPlainObject(extra[name])) deepMerge(dict, { [name]: extra[name] });
    loaded.add(cacheKey);
    return true;
  } catch {
    return false;
  }
}

export function currentLang() {
  return lang;
}

function lookup(key) {
  let node = dict;
  for (const part of key.split('.')) {
    if (node == null || typeof node !== 'object') return undefined;
    node = node[part];
  }
  return typeof node === 'string' ? node : undefined;
}

export function has(key) {
  return lookup(key) !== undefined;
}

/** Translate `key`, substituting `{name}` placeholders from `params`. Missing key -> key. */
export function t(key, params) {
  const template = lookup(key);
  if (template === undefined) {
    console.warn(`[i18n] missing key: ${key}`);
    return key;
  }
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, name) =>
    Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match,
  );
}

const ATTRS = [
  ['i18nAriaLabel', 'aria-label'],
  ['i18nTitle', 'title'],
  ['i18nPlaceholder', 'placeholder'],
];

/** Fill `[data-i18n*]` elements under `root` (including root itself). */
export function applyI18n(root = document) {
  const nodes = [...root.querySelectorAll('[data-i18n],[data-i18n-aria-label],[data-i18n-title],[data-i18n-placeholder]')];
  if (root instanceof Element) nodes.unshift(root);
  for (const el of nodes) {
    if (el.dataset.i18n) el.textContent = t(el.dataset.i18n);
    for (const [dataKey, attr] of ATTRS) {
      if (el.dataset[dataKey]) el.setAttribute(attr, t(el.dataset[dataKey]));
    }
  }
}
