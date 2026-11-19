// Test-only: async route that keeps working after the user may have navigated away.
export default async function render(container, ctx) {
  window.__slowContainer = container;
  container.innerHTML = '<p data-slow>SLOW-BEFORE</p>';
  await new Promise((resolve) => setTimeout(resolve, window.__slowDelay ?? 400));
  container.insertAdjacentHTML('beforeend', '<p data-slow>SLOW-AFTER</p>');
  window.__slowConnectedAfter = container.isConnected;
  ctx.onLive(() => { window.__slowLive = (window.__slowLive ?? 0) + 1; });
  ctx.filters.subscribe(() => { window.__slowFilter = (window.__slowFilter ?? 0) + 1; });
  window.__slowAborted = ctx.signal.aborted;
  return () => { window.__slowCleanup = (window.__slowCleanup ?? 0) + 1; };
}

export function destroy() {
  window.__slowDestroy = (window.__slowDestroy ?? 0) + 1;
}
