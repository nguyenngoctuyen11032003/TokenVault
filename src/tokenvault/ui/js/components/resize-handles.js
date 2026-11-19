// JS resize handles for the frameless window (Task 20).
//
// Eight invisible hit areas (6 px edges, 12 px corners) along the viewport border, drawn only
// while the window is normal (not maximised / full screen) and Python reports no native
// resizing (`WindowState.native_resize !== true`). With `native_region: true` (the OS window
// region itself is chamfered, so the cut corners are not part of the window) the corner areas
// sit on the chamfer diagonals and the edges start where the chamfers end. A drag:
//   pointerdown  -> setPointerCapture, api.window_get_bounds() once
//   pointermove  -> new bounds from the SCREEN delta (event.screenX/Y: the window itself moves
//                   under the pointer, so client coordinates are useless), clamped to
//                   min_width/min_height; sent with api.window_set_bounds at most once per
//                   animation frame and never with more than one call in flight (intermediate
//                   bounds are dropped, the latest one wins)
//   pointerup    -> the final bounds are always sent (after the in-flight call, if any)
//   Escape       -> cancel: the original bounds are restored
//   lostpointercapture / pointercancel without pointerup -> ends the drag with the last bounds
// Geometry is in the logical (DPI-scaled) pixels of the contract, like screenX/innerWidth.

import * as winstate from '../winstate.js';

const DIRS = ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'];

/** Pure: bounds after dragging edge(s) `dir` by (dx, dy), clamped to the minimum size. */
export function resizeBounds(start, dir, dx, dy, minW, minH) {
  let { x, y, width, height } = start;
  if (dir.includes('e')) width = Math.max(minW, start.width + dx);
  if (dir.includes('s')) height = Math.max(minH, start.height + dy);
  if (dir.includes('w')) {
    width = Math.max(minW, start.width - dx);
    x = start.x + start.width - width;
  }
  if (dir.includes('n')) {
    height = Math.max(minH, start.height - dy);
    y = start.y + start.height - height;
  }
  return { x: Math.round(x), y: Math.round(y), width: Math.round(width), height: Math.round(height) };
}

/** Serialises window_set_bounds: one call in flight, latest pending wins, one per frame,
 * identical consecutive bounds are sent once. */
function createSender(api) {
  let pending = null;
  let inflight = false;
  let lastSent = '';
  let frame = 0;
  const flush = () => {
    frame = 0;
    if (inflight || !pending) return;
    const b = pending;
    pending = null;
    const key = `${b.x},${b.y},${b.width},${b.height}`;
    if (key === lastSent) return; // same bounds already sent (e.g. final == last move)
    lastSent = key;
    inflight = true;
    Promise.resolve()
      .then(() => api.window_set_bounds(b.x, b.y, b.width, b.height))
      .then((state) => {
        if (state != null) winstate.applyState(state);
      })
      .catch((err) => console.info('[resize] window_set_bounds failed', err?.message ?? err))
      .finally(() => {
        inflight = false;
        if (pending && !frame) frame = requestAnimationFrame(flush);
      });
  };
  return {
    /** New drag: forget what the previous drag sent. */
    reset() {
      lastSent = '';
    },
    /** Queue bounds for the next frame. */
    send(b) {
      pending = b;
      if (!inflight && !frame) frame = requestAnimationFrame(flush);
    },
    /** Queue the final bounds and send them as soon as nothing is in flight. */
    finish(b) {
      pending = b;
      if (frame) cancelAnimationFrame(frame);
      frame = 0;
      flush();
    },
  };
}

export function mountResizeHandles(api, host = document.body) {
  const root = document.createElement('div');
  root.className = 'tv-resize';
  root.setAttribute('aria-hidden', 'true');
  for (const dir of DIRS) {
    const h = document.createElement('div');
    h.className = `tv-resize__handle tv-resize__handle--${dir}`;
    h.dataset.dir = dir;
    root.append(h);
  }
  host.append(root);

  const sender = createSender(api);
  let drag = null; // {id, el, dir, sx, sy, start: bounds|null, last: bounds|null, x, y}

  const sync = (state) => {
    root.hidden = state.maximized || state.fullscreen || state.native_resize === true;
    // The OS window region is cut to the chamfer: keep every hit area inside it.
    root.classList.toggle('tv-resize--region', state.native_region === true);
    if (root.hidden && drag) end(false);
  };

  const compute = (d = drag) => {
    if (!d?.start) return null;
    const s = winstate.getState();
    return resizeBounds(d.start, d.dir, d.x - d.sx, d.y - d.sy, s.min_width, s.min_height);
  };

  function end(cancel) {
    if (!drag) return;
    const d = drag;
    drag = null;
    window.removeEventListener('keydown', onKey, true);
    try {
      if (d.el.hasPointerCapture(d.id)) d.el.releasePointerCapture(d.id);
    } catch {
      /* capture already gone */
    }
    if (!d.start) {
      d.finishWhenReady = !cancel; // released before window_get_bounds answered
      return;
    }
    if (cancel) sender.finish({ ...d.start });
    else if (d.last) sender.finish(d.last);
  }

  function onKey(event) {
    if (event.key !== 'Escape' || !drag) return;
    event.preventDefault();
    event.stopPropagation();
    end(true);
  }

  root.addEventListener('pointerdown', (event) => {
    const el = event.target.closest('.tv-resize__handle');
    if (!el || event.button !== 0 || drag) return;
    event.preventDefault();
    el.setPointerCapture(event.pointerId);
    const d = { id: event.pointerId, el, dir: el.dataset.dir, sx: event.screenX, sy: event.screenY,
      x: event.screenX, y: event.screenY, start: null, last: null };
    drag = d;
    sender.reset();
    window.addEventListener('keydown', onKey, true);
    Promise.resolve()
      .then(() => api.window_get_bounds())
      .then((b) => {
        if (!b || ![b.x, b.y, b.width, b.height].every(Number.isFinite)) throw new Error('bad bounds');
        d.start = { x: b.x, y: b.y, width: b.width, height: b.height };
        d.last = compute(d);
        if (drag === d) sender.send(d.last);
        else if (d.finishWhenReady) sender.finish(d.last);
      })
      .catch((err) => {
        console.info('[resize] window_get_bounds unavailable', err?.message ?? err);
        if (drag === d) end(true);
      });
  });

  root.addEventListener('pointermove', (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    drag.x = event.screenX;
    drag.y = event.screenY;
    const b = compute();
    if (!b) return;
    drag.last = b;
    sender.send(b);
  });

  root.addEventListener('pointerup', (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    drag.x = event.screenX;
    drag.y = event.screenY;
    drag.last = compute() ?? drag.last;
    end(false);
  });
  root.addEventListener('pointercancel', () => end(false));
  root.addEventListener('lostpointercapture', () => end(false));

  const off = winstate.subscribe(sync);
  sync(winstate.getState());
  return () => {
    off();
    end(true);
    root.remove();
  };
}
