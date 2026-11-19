// Test-only: subscribes to live + filters during render.
export default function render(container, ctx) {
  container.innerHTML = '<p data-subs>SUBS</p>';
  ctx.onLive(() => { window.__subsLive = (window.__subsLive ?? 0) + 1; });
  ctx.filters.subscribe(() => { window.__subsFilter = (window.__subsFilter ?? 0) + 1; });
}
