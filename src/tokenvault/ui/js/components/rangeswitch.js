// Range switch (today / 7d / 30d) on the shared .tv-seg primitive.
//   const sw = createRangeSwitch({ t: (k) => ctx.t(`employee.${k}`), value, onChange, label });
//   container.append(sw.el); sw.set('7d');
// `t` is scoped to the calling page's namespace: labels are `<page>.range.today|7d|30d`.

import { h, RANGES } from './homekit.js';

export function createRangeSwitch({ t, value = 'today', onChange, label }) {
  let current = RANGES.includes(value) ? value : 'today';
  const buttons = RANGES.map((range) =>
    h(
      'button',
      {
        type: 'button',
        dataset: { range },
        'aria-pressed': String(range === current),
        on: {
          click: () => {
            if (range === current) return;
            set(range);
            onChange?.(range);
          },
        },
      },
      t(`range.${range}`),
    ),
  );
  const el = h('div', { class: 'tv-seg tv-rangeswitch', role: 'group', 'aria-label': label }, buttons);

  function set(range) {
    current = RANGES.includes(range) ? range : 'today';
    for (const b of buttons) b.setAttribute('aria-pressed', String(b.dataset.range === current));
  }

  return { el, set, get: () => current };
}
