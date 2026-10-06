/**
 * Multi-select bulk actions for message listings (HLD U5.6/U7.6/UX3h).
 *
 * Selection entry points:
 * - Desktop (`md:` and up): per-row checkboxes (data-select-message) plus
 *   a master "select all" box — the keyboard/screen-reader accessible path.
 * - Touch below `md` (UX3h): press-and-hold (~500ms) on a row enters a
 *   selection mode with animated circle/check controls; taps toggle
 *   selection; "Done" or deselecting the last row exits.
 *
 * The selected-id Set is the single source of truth; checkboxes and circle
 * controls are synced from it by refresh(). Actions post to /mail/bulk
 * (page selection) or /mail/search/apply (whole-result-set mode) and
 * update the UI in place.
 */
(function () {
  'use strict';

  var CONFIRM_THRESHOLD = 200;
  var LONG_PRESS_MS = 500;
  var TAP_SLOP_PX = 10;
  var SELECTABLE_SELECTOR = '.message-row[data-message-id]';

  function init(opts) {
    var container = typeof opts.container === 'string'
      ? document.querySelector(opts.container)
      : opts.container;
    var toolbar = document.getElementById('bulk-toolbar');
    if (!container || !toolbar) return null;
    var accountId = String(opts.accountId || toolbar.dataset.accountId || '');
    var bulkUrl = toolbar.dataset.bulkUrl || '/app/mail/bulk';
    var searchApplyUrl = opts.searchApplyUrl || null;
    var totalMatches = parseInt(opts.totalMatches || '0', 10) || 0;
    var query = opts.query || '';
    var countEl = toolbar.querySelector('[data-bulk-count]');
    var destinationEl = toolbar.querySelector('[data-bulk-destination]');
    var selectAll = document.getElementById('select-all-messages');
    var matchBar = document.getElementById('bulk-select-all-match');
    var matchBtn = matchBar ? matchBar.querySelector('[data-bulk-select-all-match]') : null;
    var doneBtn = toolbar.querySelector('[data-bulk-done]');
    var selectPageBtn = toolbar.querySelector('[data-bulk-select-page]');
    var longPressQuery = window.matchMedia('(max-width: 767.98px)');
    var mode = 'page'; // 'page' | 'match'
    var selection = new Set();
    var selectionMode = false;
    var suppressToggleTap = false;
    var pressTimer = null;
    var pressStart = null;

    function checkboxes() {
      return Array.prototype.slice.call(
        container.querySelectorAll('input[data-select-message]')
      );
    }

    function rowIds() {
      return Array.prototype.slice.call(
        container.querySelectorAll(SELECTABLE_SELECTOR)
      ).map(function (row) {
        return String(row.dataset.messageId);
      }).filter(Boolean);
    }

    function selectedIds() {
      return Array.from(selection);
    }

    function rowFor(id) {
      return container.querySelector(
        '.message-row[data-message-id="' + CSS.escape(String(id)) + '"]'
      );
    }

    function t(key, params) {
      return window.LR ? window.LR.t(key, params) : key;
    }

    function refresh() {
      var ids = selectedIds();
      var total = mode === 'match' ? totalMatches : ids.length;
      if (countEl) {
        countEl.textContent = t('{n} selected', { n: total });
      }
      var hasSelection = mode === 'match' || ids.length > 0;
      toolbar.classList.toggle('hidden', !hasSelection && !selectionMode);
      toolbar.classList.toggle('flex', hasSelection || selectionMode);
      if (matchBar) matchBar.classList.toggle('hidden', mode === 'match');
      checkboxes().forEach(function (cb) {
        cb.checked = selection.has(String(cb.dataset.selectMessage));
      });
      container.querySelectorAll('[data-select-circle]').forEach(function (circle) {
        var row = circle.closest('.message-row');
        var on = row ? selection.has(String(row.dataset.messageId)) : false;
        circle.classList.toggle('is-selected', on);
        circle.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
      if (doneBtn) {
        doneBtn.classList.toggle('hidden', !selectionMode);
      }
      if (selectPageBtn) {
        selectPageBtn.classList.toggle('hidden', !selectionMode || selection.size >= rowIds().length);
      }
      if (selectAll) {
        var boxes = rowIds();
        var allChecked = boxes.length > 0 && ids.length === boxes.length;
        selectAll.checked = allChecked;
        selectAll.indeterminate = ids.length > 0 && !allChecked;
      }
    }

    function enterSelectionMode(row) {
      selectionMode = true;
      container.setAttribute('data-selection-mode', '1');
      mode = 'page';
      var id = row && row.dataset.messageId ? String(row.dataset.messageId) : null;
      if (id) selection.add(id);
      refresh();
    }

    function exitSelectionMode() {
      selectionMode = false;
      container.removeAttribute('data-selection-mode');
      selection.clear();
      refresh();
    }

    function clearSelection() {
      mode = 'page';
      if (selectionMode) {
        exitSelectionMode();
        return;
      }
      selection.clear();
      refresh();
    }

    function toggleRow(row) {
      var id = row && row.dataset.messageId ? String(row.dataset.messageId) : null;
      if (!id) return;
      if (selection.has(id)) {
        selection.delete(id);
        if (selectionMode && selection.size === 0) {
          exitSelectionMode();
          return;
        }
      } else {
        selection.add(id);
      }
      refresh();
    }

    // UX3h: rows tapped while in selection mode toggle selection via the
    // lr:toggle-select event dispatched by LRMessageList.
    container.addEventListener('lr:toggle-select', function (e) {
      if (suppressToggleTap) {
        suppressToggleTap = false;
        return;
      }
      var row = e.target && e.target.closest ? e.target.closest('.message-row') : null;
      if (row) toggleRow(row);
    });

    // UX3h: press-and-hold enters selection mode (touch, below md). The
    // timer is cancelled by movement beyond the tap slop (scroll/swipe)
    // or by lifting the finger early.
    container.addEventListener('touchstart', function (e) {
      if (!longPressQuery.matches || !e.touches || e.touches.length !== 1) return;
      var row = e.target.closest('.message-row');
      if (!row || !row.dataset.messageId) return;
      pressStart = {
        x: e.touches[0].clientX,
        y: e.touches[0].clientY,
        row: row
      };
      pressTimer = window.setTimeout(function () {
        if (!pressStart) return;
        var held = pressStart.row;
        pressStart = null;
        if (selectionMode) return;
        enterSelectionMode(held);
        if (navigator.vibrate) {
          try { navigator.vibrate(10); } catch (err) { /* unsupported */ }
        }
        // The long-press's trailing click must not immediately toggle
        // the just-selected row off.
        suppressToggleTap = true;
      }, LONG_PRESS_MS);
    }, { passive: true });

    container.addEventListener('touchmove', function (e) {
      if (!pressStart || !e.touches) return;
      var dx = e.touches[0].clientX - pressStart.x;
      var dy = e.touches[0].clientY - pressStart.y;
      if (Math.abs(dx) > TAP_SLOP_PX || Math.abs(dy) > TAP_SLOP_PX) {
        window.clearTimeout(pressTimer);
        pressStart = null;
      }
    }, { passive: true });

    ['touchend', 'touchcancel'].forEach(function (type) {
      container.addEventListener(type, function () {
        window.clearTimeout(pressTimer);
        pressStart = null;
      });
    });

    // iOS long-press would open the callout/context menu instead.
    container.addEventListener('contextmenu', function (e) {
      if (longPressQuery.matches && e.target.closest('.message-row')) {
        e.preventDefault();
      }
    });

    // Circles are also directly tappable (they sit on the row's left edge
    // and are not row-nav targets).
    container.addEventListener('click', function (e) {
      var circle = e.target.closest('[data-select-circle]');
      if (!circle) return;
      e.preventDefault();
      e.stopPropagation();
      toggleRow(circle.closest('.message-row'));
    });

    container.addEventListener('change', function (e) {
      var cb = e.target.closest('input[data-select-message]');
      if (!cb) return;
      mode = 'page';
      var id = String(cb.dataset.selectMessage);
      if (cb.checked) {
        selection.add(id);
      } else {
        selection.delete(id);
      }
      refresh();
    });

    if (selectAll) {
      selectAll.addEventListener('change', function () {
        mode = 'page';
        selection.clear();
        if (selectAll.checked) {
          rowIds().forEach(function (id) { selection.add(id); });
        }
        refresh();
      });
    }

    if (selectPageBtn) {
      selectPageBtn.addEventListener('click', function () {
        mode = 'page';
        rowIds().forEach(function (id) { selection.add(id); });
        refresh();
      });
    }

    if (doneBtn) {
      doneBtn.addEventListener('click', exitSelectionMode);
    }

    if (matchBtn) {
      matchBtn.addEventListener('click', function () {
        mode = 'match';
        rowIds().forEach(function (id) { selection.add(id); });
        refresh();
      });
    }

    var clearBtn = toolbar.querySelector('[data-bulk-clear]');
    if (clearBtn) {
      clearBtn.addEventListener('click', clearSelection);
    }

    function updateRowUnreadUI(row, isUnread) {
      if (!row) return;
      row.dataset.isUnread = isUnread ? '1' : '0';
      var fg = row.querySelector('[data-row-foreground]');
      if (fg) {
        fg.classList.toggle('bg-[#fef7d8]', isUnread);
        fg.classList.toggle('hover:bg-amber-100', isUnread);
        fg.classList.toggle('bg-white', !isUnread);
        fg.classList.toggle('hover:bg-slate-50/80', !isUnread);
      }
      var subject = row.querySelector('[data-subject="true"]');
      if (subject) {
        subject.classList.toggle('font-bold', isUnread);
        subject.classList.toggle('text-slate-950', isUnread);
        subject.classList.toggle('text-slate-700', !isUnread);
      }
      var sender = row.querySelector('[data-sender="true"]');
      if (sender) {
        sender.classList.toggle('font-semibold', isUnread);
        sender.classList.toggle('text-slate-900', isUnread);
        sender.classList.toggle('text-slate-600', !isUnread);
      }
    }

    function updateRowFlagUI(row) {
      if (!row) return;
      row.dataset.isFlagged = '1';
      row.querySelectorAll('[data-star-icon]').forEach(function (star) {
        star.setAttribute('fill', 'currentColor');
        star.setAttribute('stroke-width', '1');
        star.classList.add('text-amber-400');
        star.classList.remove('text-slate-300');
      });
      row.querySelectorAll('form[data-action="flag"]').forEach(function (form) {
        var input = form.querySelector('input[name="action"]');
        if (input) input.value = 'remove';
      });
    }

    function removeRow(row) {
      if (!row) return;
      var threadCard = row.closest('[data-thread-card="true"]');
      row.style.overflow = 'hidden';
      row.style.maxHeight = row.offsetHeight + 'px';
      row.style.transition = 'max-height 0.2s ease-out, opacity 0.2s ease-out';
      row.style.opacity = '0';
      requestAnimationFrame(function () {
        row.style.maxHeight = '0px';
        row.style.paddingTop = '0px';
        row.style.paddingBottom = '0px';
      });
      setTimeout(function () {
        row.remove();
        if (threadCard && !threadCard.querySelector('.message-row')) {
          threadCard.remove();
        }
        if (mode === 'page') refresh();
      }, 220);
    }

    function applyToPage(action, ids) {
      var formData = new FormData();
      formData.append('action', action);
      formData.append('account_id', accountId);
      ids.forEach(function (id) { formData.append('message_ids', id); });
      if (action === 'move') {
        if (!destinationEl || !destinationEl.value) {
          if (window.LR) window.LR.notifyError(t('Choose a destination folder first.'));
          return Promise.reject(new Error('no destination'));
        }
        formData.append('destination', destinationEl.value);
      }
      return fetch(bulkUrl, {
        method: 'POST',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        body: formData
      }).then(function (resp) {
        if (!resp.ok) throw new Error('bulk failed');
        return resp.json();
      }).then(function (data) {
        ids.forEach(function (id) {
          var row = rowFor(id);
          if (!row) return;
          if (action === 'mark_read') updateRowUnreadUI(row, false);
          else if (action === 'mark_unread') updateRowUnreadUI(row, true);
          else if (action === 'flag') updateRowFlagUI(row);
          else if (action === 'delete' || action === 'move') removeRow(row);
        });
        if (data && data.skipped > 0) {
          if (window.LR) window.LR.notifyError(t('{n} conversations were skipped (protected).', { n: data.skipped }));
        }
        clearSelection();
        return data;
      });
    }

    function applyToMatch(action) {
      if (totalMatches > CONFIRM_THRESHOLD) {
        var msg = t('Apply this action to all {n} matching conversations?', { n: totalMatches });
        if (!window.confirm(msg)) return Promise.reject(new Error('cancelled'));
      }
      var formData = new FormData();
      formData.append('q', query);
      formData.append('action', action);
      formData.append('account_id', accountId);
      if (action === 'move') {
        if (!destinationEl || !destinationEl.value) {
          if (window.LR) window.LR.notifyError(t('Choose a destination folder first.'));
          return Promise.reject(new Error('no destination'));
        }
        formData.append('destination', destinationEl.value);
      }
      return fetch(searchApplyUrl, {
        method: 'POST',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        body: formData
      }).then(function (resp) {
        if (!resp.ok) throw new Error('apply failed');
        return resp.json();
      }).then(function (data) {
        if (!data || data.status !== 'ok') throw new Error('apply failed');
        if (data.skipped > 0) {
          if (window.LR) window.LR.notifyError(t('{n} conversations were skipped (protected).', { n: data.skipped }));
        }
        window.location.reload();
        return data;
      });
    }

    toolbar.addEventListener('click', function (e) {
      var btn = e.target.closest('[data-bulk-action]');
      if (!btn) return;
      var action = btn.dataset.bulkAction;
      var ids = selectedIds();
      if (mode === 'page' && ids.length === 0) return;
      var apply = mode === 'match'
        ? applyToMatch(action)
        : applyToPage(action, ids);
      if (window.LR) window.LR.setButtonLoading(btn);
      apply.catch(function () {
        if (window.LR) window.LR.notifyError();
      }).finally(function () {
        if (window.LR) window.LR.clearButtonLoading(btn);
      });
    });

    refresh();
    return {
      refresh: refresh,
      clearSelection: clearSelection,
      enterSelectionMode: enterSelectionMode,
      exitSelectionMode: exitSelectionMode
    };
  }

  window.LRBulkSelect = { init: init };
})();
