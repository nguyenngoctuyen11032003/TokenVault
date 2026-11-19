// Test-only: renders a string from its (hostile) per-page strings file.
export default function render(container, ctx) {
  container.innerHTML = `<p data-evil>${ctx.t('evil.ok')}</p>`;
}
