/**
 * Touch swipe gestures for message rows (HLD UX3g).
 *
 * On touch below lg: swiping a row right past the trigger archives it
 * (submitting the archive form so the shared message-action handler runs
 * the removal + undo banner), and swiping left past the trigger snaps the
 * action panel open behind the row's opaque foreground. Gesture
 * disambiguation follows the U12.56d pattern (horizontal movement must
 * dominate vertical), a tap never triggers a swipe, and desktop (fine
 * pointer or lg+ viewport) never gets swipe behavior.
 */
(function () {
  'use strict';

  var SWIPE_ENGAGE_PX = 12;
  var SWIPE_TRIGGER_PX = 60;
  var SWIPE_QUERY = window.matchMedia('(max-width: 1023.98px)');
  var REDUCED_MOTION_QUERY = window.matchMedia('(prefers-reduced-motion: reduce)');

  function init(container) {
    var swipe = null;

    var foregroundOf = function (row) {
      return row.querySelector('[data-row-foreground]');
    };

    var resetRow = function (row) {
      var fg = foregroundOf(row);
      if (fg) {
        fg.style.transition = '';
        fg.style.transform = '';
      }
      row.classList.remove('is-swipe-open');
      row.querySelectorAll('[data-swipe-panel]').forEach(function (panel) {
        panel.classList.add('hidden');
      });
    };

    var closeAll = function (exceptRow) {
      container.querySelectorAll('.message-row.is-swipe-open').forEach(function (row) {
        if (exceptRow && row === exceptRow) {
          return;
        }
        resetRow(row);
      });
    };

    container.addEventListener('touchstart', function (e) {
      if (!SWIPE_QUERY.matches || !e.touches || e.touches.length !== 1) return;
      if (container.hasAttribute('data-selection-mode')) return;
      var row = e.target.closest('.message-row');
      if (!row || !row.querySelector('[data-swipe-panel]')) return;
      swipe = {
        row: row,
        fg: foregroundOf(row),
        startX: e.touches[0].clientX,
        startY: e.touches[0].clientY,
        engaged: false,
        dx: 0,
        openWidth: 0
      };
    }, { passive: true });

    container.addEventListener('touchmove', function (e) {
      if (!swipe || !swipe.fg) return;
      var dx = e.touches[0].clientX - swipe.startX;
      var dy = e.touches[0].clientY - swipe.startY;
      if (!swipe.engaged) {
        // Disambiguate against vertical scrolling (UX3g, U12.56d pattern):
        // horizontal must dominate before the gesture engages.
        if (Math.abs(dx) > SWIPE_ENGAGE_PX && Math.abs(dx) >= Math.abs(dy) * 1.5) {
          swipe.engaged = true;
          closeAll(swipe.row);
          var panel = swipe.row.querySelector('[data-swipe-panel="' + (dx > 0 ? 'left' : 'right') + '"]');
          if (panel) {
            panel.classList.remove('hidden');
            swipe.openWidth = panel.offsetWidth || 160;
          }
          swipe.fg.style.transition = 'none';
        } else if (Math.abs(dy) > SWIPE_ENGAGE_PX) {
          swipe = null;
          return;
        } else {
          return;
        }
      }
      if (e.cancelable) e.preventDefault();
      swipe.dx = dx;
      var clamped = Math.max(-(swipe.openWidth + 40), Math.min(swipe.row.offsetWidth, dx));
      swipe.fg.style.transform = 'translateX(' + clamped + 'px)';
    }, { passive: false });

    var finishSwipe = function () {
      if (!swipe) return;
      var st = swipe;
      swipe = null;
      if (!st.engaged) return;
      st.fg.style.transition = REDUCED_MOTION_QUERY.matches ? 'none' : '';
      if (st.dx >= SWIPE_TRIGGER_PX) {
        // Full right swipe: archive (UX3g). Fling out, then submit the
        // archive form so the shared message-action handler runs
        // (row removal + undo banner).
        st.fg.style.transform = 'translateX(' + (st.row.offsetWidth + 40) + 'px)';
        window.setTimeout(function () {
          var form = st.row.querySelector('[data-swipe-archive-form]');
          if (form) {
            form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
          }
          resetRow(st.row);
        }, REDUCED_MOTION_QUERY.matches ? 0 : 220);
      } else if (st.dx <= -SWIPE_TRIGGER_PX) {
        st.fg.style.transform = 'translateX(-' + st.openWidth + 'px)';
        st.row.classList.add('is-swipe-open');
      } else {
        resetRow(st.row);
      }
    };
    container.addEventListener('touchend', finishSwipe);
    container.addEventListener('touchcancel', function () {
      if (swipe && swipe.engaged) {
        resetRow(swipe.row);
      }
      swipe = null;
    });

    return {
      resetRow: resetRow,
      closeAll: closeAll
    };
  }

  window.LRSwipe = { init: init };
})();
