/**
 * Multi-select bulk actions for message listings (HLD U5.6/U7.6).
 *
 * Per-row checkboxes (data-select-message) plus a master "select all" box
 * drive a shared action bar (_bulk_toolbar.html). On search result views an
 * additional "select all N that match" mode re-runs the query server-side
 * via /mail/search/apply (with a count confirmation above 200 matches).
 *
 * Actions post to /mail/bulk (page selection) or /mail/search/apply
 * (whole-result-set mode) and update the UI in place: read/unread styling,
 * star state, and row removal with a short animation.
 */
(function () {
  'use strict';

  var CONFIRM_THRESHOLD = 200;

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
    var mode = 'page'; // 'page' | 'match'

    function checkboxes() {
      return Array.prototype.slice.call(
        container.querySelectorAll('input[data-select-message]')
      );
    }

    function selectedIds() {
      return checkboxes()
        .filter(function (cb) { return cb.checked; })
        .map(function (cb) { return cb.dataset.selectMessage; });
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
      toolbar.classList.toggle('hidden', !hasSelection);
      toolbar.classList.toggle('flex', hasSelection);
      if (matchBar) matchBar.classList.toggle('hidden', mode === 'match');
      if (selectAll) {
        var boxes = checkboxes();
        var allChecked = boxes.length > 0 && ids.length === boxes.length;
        selectAll.checked = allChecked;
        selectAll.indeterminate = ids.length > 0 && !allChecked;
      }
    }

    function clearSelection() {
      mode = 'page';
      checkboxes().forEach(function (cb) { cb.checked = false; });
      refresh();
    }

    function updateRowUnreadUI(row, isUnread) {
      if (!row) return;
      row.dataset.isUnread = isUnread ? '1' : '0';
      row.classList.toggle('bg-amber-100/70', isUnread);
      row.classList.toggle('hover:bg-amber-100', isUnread);
      row.classList.toggle('hover:bg-slate-50/80', !isUnread);
      var subject = row.querySelector('[data-subject="true"]');
      if (subject) {
        subject.classList.toggle('font-bold', isUnread);
        subject.classList.toggle('text-slate-950', isUnread);
        subject.classList.toggle('text-slate-700', !isUnread);
      }
    }

    function updateRowFlagUI(row) {
      if (!row) return;
      row.dataset.isFlagged = '1';
      var star = row.querySelector('[data-star-icon]');
      if (star) {
        star.setAttribute('fill', 'currentColor');
        star.setAttribute('stroke-width', '1');
        star.classList.add('text-amber-400');
        star.classList.remove('text-slate-300');
      }
      var form = row.querySelector('form[data-action="flag"]');
      if (form) {
        var input = form.querySelector('input[name="action"]');
        if (input) input.value = 'remove';
      }
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

    container.addEventListener('change', function (e) {
      var cb = e.target.closest('input[data-select-message]');
      if (!cb) return;
      mode = 'page';
      refresh();
    });

    if (selectAll) {
      selectAll.addEventListener('change', function () {
        mode = 'page';
        var checked = selectAll.checked;
        checkboxes().forEach(function (cb) { cb.checked = checked; });
        refresh();
      });
    }

    if (matchBtn) {
      matchBtn.addEventListener('click', function () {
        mode = 'match';
        checkboxes().forEach(function (cb) { cb.checked = true; });
        refresh();
      });
    }

    var clearBtn = toolbar.querySelector('[data-bulk-clear]');
    if (clearBtn) {
      clearBtn.addEventListener('click', clearSelection);
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
    return { refresh: refresh, clearSelection: clearSelection };
  }

  window.LRBulkSelect = { init: init };
})();
