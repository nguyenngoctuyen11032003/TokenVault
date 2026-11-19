// Test-only: route whose render throws.
export default function render(container, ctx) {
  ctx.onLive(() => { window.__boomLive = (window.__boomLive ?? 0) + 1; });
  container.innerHTML = '<p>partial</p>';
  throw new Error('kaboom');
}

export function destroy() {
  window.__boomDestroy = (window.__boomDestroy ?? 0) + 1;
}
