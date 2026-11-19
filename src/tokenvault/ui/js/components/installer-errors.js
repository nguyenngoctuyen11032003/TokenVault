// Error classification for the installer / settings / diagnostics pages (Task 17).
//
// The Manager marks errors the UI must react to with a machine prefix at the start of the
// exception message (contract, "Error classification"): [template_missing], [cert_error],
// [revoked], [invalid_team]. The UI tests for the prefix, never for words, and never shows the
// prefix to the owner: known codes get a translated message, anything else shows the detail
// with any [prefix] stripped.

export const KNOWN_CODES = Object.freeze(['template_missing', 'cert_error', 'revoked', 'invalid_team']);

const PREFIX_RE = /^\s*\[([A-Za-z0-9_-]+)\]\s*/;

/** Raw message of an error as Python raised it (ApiError keeps it on `.cause`). */
function rawMessage(err) {
  if (err === null || err === undefined) return '';
  const cause = err.cause;
  if (cause !== undefined && cause !== null) {
    return String(cause?.message ?? cause);
  }
  let text = String(err?.message ?? err);
  // ApiError without cause: "<method>: <message>".
  if (err?.method && text.startsWith(`${err.method}: `)) text = text.slice(err.method.length + 2);
  return text;
}

/**
 * {code, known, detail}: `code` is the prefix name (lower case) or null, `known` whether it is
 * one of KNOWN_CODES, `detail` the message without the prefix.
 */
export function classifyError(err) {
  const raw = rawMessage(err);
  const m = PREFIX_RE.exec(raw);
  if (!m) return { code: null, known: false, detail: raw.trim() };
  const code = m[1].toLowerCase();
  return { code, known: KNOWN_CODES.includes(code), detail: raw.slice(m[0].length).trim() };
}

/** Owner-facing detail: the message without any machine prefix. */
export function errorDetail(err) {
  return classifyError(err).detail;
}

/**
 * Text to show for `err`: `messages[code]` (already translated) for a known code the caller
 * handles, else `fallback(detail)` with the prefix stripped.
 * Returns {code, text}.
 */
export function describeError(err, messages, fallback) {
  const info = classifyError(err);
  const special = info.known && messages ? messages[info.code] : undefined;
  return { code: info.known ? info.code : null, text: special ?? fallback(info.detail) };
}
