/**
 * Shared edge-swipe drawer gesture engine (HLD U24.2a).
 *
 * Modules register their existing off-canvas drawer (U24.2) with:
 *   LRDrawer.register({ drawer, backdrop, isOpen, setOpen })
 * and gain finger-tracking gestures below the lg breakpoint:
 *   - open:  a horizontal drag starting within 24px of the left edge
 *   - close: a right-to-left drag starting on the open drawer/backdrop
 * Tracking locks only after horizontal dominance (|dx| > 8 and
 * |dx| > 1.5 * |dy|), so vertical scrolling is never intercepted. On
 * release the drawer settles open past 50% of its width or on a fast
 * flick (> 0.5 px/ms); the final state is applied through the module's
 * own setOpen() so aria-expanded, backdrop, and body scroll lock stay
 * in one place. Competing horizontal-swipe handlers (mail rows UX3g,
 * calendar period nav U12.56d) yield edge-origin touches by checking
 * LRDrawer.claims() on touchstart.
 */
(function () {
  'use strict';

  var EDGE_PX = 24;
  var ENGAGE_PX = 8;
  var DOMINANCE = 1.5;
  var OPEN_RATIO = 0.5;
  var FLICK_PX_PER_MS = 0.5;
  var MOBILE_QUERY = window.matchMedia('(max-width: 1023.98px)');
  var REDUCED_MOTION_QUERY = window.matchMedia('(prefers-reduced-motion: reduce)');
  // Targets that keep their own touch/drag behavior even inside the edge
  // zone (calendar event chips drag via pointer events, U12.56).
  var SKIP_TARGETS = '.cal-event, .cal-resize-handle';

  var drawers = [];
  var gesture = null;

  function openDrawer() {
    for (var i = 0; i < drawers.length; i++) {
      if (drawers[i].isOpen()) return drawers[i];
    }
    return null;
  }

  /* True when the edge gesture should own a touch starting at clientX.
   * Row swipes (UX3g) and calendar period swipes (U12.56d) check this on
   * touchstart and yield when it returns true. */
  function claims(clientX) {
    return (
      MOBILE_QUERY.matches &&
      drawers.length > 0 &&
      !openDrawer() &&
      typeof clientX === 'number' &&
      clientX <= EDGE_PX
    );
  }

  function register(config) {
    if (!config || !config.drawer || !config.isOpen || !config.setOpen) return;
    drawers = drawers.filter(function (d) {
      return d.drawer !== config.drawer;
    });
    drawers.push(config);
  }

  function begin(mode, d, touch) {
    gesture = {
      mode: mode,
      d: d,
      x0: touch.clientX,
      y0: touch.clientY,
      lastX: touch.clientX,
      lastT: Date.now(),
      velocity: 0,
      tx: null,
      w: 0,
      engaged: false
    };
  }

  function onStart(e) {
    if (gesture && gesture.engaged) return; // extra fingers never interrupt
    gesture = null;
    if (!MOBILE_QUERY.matches || !e.touches || e.touches.length !== 1) return;
    if (e.target && e.target.closest && e.target.closest(SKIP_TARGETS)) return;
    var touch = e.touches[0];
    var current = openDrawer();
    if (current) {
      var onChrome =
        current.drawer.contains(e.target) ||
        (current.backdrop && current.backdrop.contains(e.target));
      if (onChrome) begin('close', current, touch);
      return;
    }
    if (touch.clientX <= EDGE_PX && drawers.length > 0) {
      begin('open', drawers[0], touch);
    }
  }

  function trackVelocity(x, now) {
    var dt = now - gesture.lastT;
    if (dt > 0) gesture.velocity = (x - gesture.lastX) / dt;
    gesture.lastX = x;
    gesture.lastT = now;
  }

  /* Closed position is -w, open is 0; the drag is clamped to that range. */
  function translateFor(dx) {
    var base = gesture.mode === 'open' ? -gesture.w : 0;
    return Math.max(-gesture.w, Math.min(0, base + dx));
  }

  function applyTransform(dx) {
    var tx = translateFor(dx);
    gesture.tx = tx;
    gesture.d.drawer.style.transform = 'translateX(' + tx + 'px)';
    if (gesture.d.backdrop) {
      gesture.d.backdrop.style.opacity = String((tx + gesture.w) / gesture.w);
    }
  }

  function onMove(e) {
    if (!gesture || !e.touches || e.touches.length !== 1) return;
    var touch = e.touches[0];
    var now = Date.now();
    var dx = touch.clientX - gesture.x0;
    var dy = touch.clientY - gesture.y0;
    trackVelocity(touch.clientX, now);
    if (!gesture.engaged) {
      var opening = gesture.mode === 'open' ? dx : -dx;
      if (opening > ENGAGE_PX && opening > Math.abs(dy) * DOMINANCE) {
        gesture.engaged = true;
        gesture.w = gesture.d.drawer.getBoundingClientRect().width || 288;
        gesture.d.drawer.style.transition = 'none';
        if (gesture.mode === 'open' && gesture.d.backdrop) {
          gesture.d.backdrop.classList.remove('hidden');
        }
      } else if (Math.abs(dy) > ENGAGE_PX && Math.abs(dy) > Math.abs(dx) * DOMINANCE) {
        gesture = null; // vertical scroll intent — release the touch
        return;
      } else {
        return;
      }
    }
    if (e.cancelable) e.preventDefault();
    applyTransform(dx);
  }

  function settle(d, open) {
    d.drawer.style.transition = REDUCED_MOTION_QUERY.matches ? 'none' : '';
    d.drawer.style.transform = '';
    if (d.backdrop) d.backdrop.style.opacity = '';
    d.setOpen(open);
    if (REDUCED_MOTION_QUERY.matches) {
      window.setTimeout(function () {
        d.drawer.style.transition = '';
      }, 30);
    }
  }

  function onEnd(e) {
    if (!gesture) return;
    var g = gesture;
    gesture = null;
    if (!g.engaged) return; // plain tap: leave clicks to the page
    if (e && e.cancelable) e.preventDefault(); // suppress the ghost click
    var tx = g.tx !== null ? g.tx : g.mode === 'open' ? -g.w : 0;
    var progress = g.w ? (tx + g.w) / g.w : 0;
    var v = g.velocity || 0;
    var open = g.mode === 'open'
      ? progress > OPEN_RATIO || v > FLICK_PX_PER_MS
      : progress > OPEN_RATIO && v > -FLICK_PX_PER_MS;
    settle(g.d, open);
  }

  function onCancel() {
    if (!gesture) return;
    var g = gesture;
    gesture = null;
    if (!g.engaged) return;
    var progress = g.w && g.tx !== null ? (g.tx + g.w) / g.w : 1;
    settle(g.d, progress > OPEN_RATIO);
  }

  document.addEventListener('touchstart', onStart, { passive: true });
  document.addEventListener('touchmove', onMove, { passive: false });
  document.addEventListener('touchend', onEnd, { passive: false });
  document.addEventListener('touchcancel', onCancel, { passive: true });

  window.LRDrawer = { register: register, claims: claims, EDGE_PX: EDGE_PX };
})();
