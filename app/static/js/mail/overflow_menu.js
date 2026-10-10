/**
 * Overflow menu plumbing for mail message rows (HLD UX3b/UX3g).
 *
 * Two surfaces share this module:
 *
 * 1. The desktop "..." dropdown's move-to-folder picker (installed per
 *    list container by message-list.js via initDropdownPicker). The
 *    dropdown itself stays in message-list.js; only the folder picker
 *    and the move POST live here so both surfaces share one fetch path.
 *
 * 2. The touch bottom action sheet (UX3g). On touch devices the in-row
 *    dropdown is unreachable — it renders inside the hover action
 *    overlay, which is display:none on (hover: none)/(pointer: coarse)
 *    devices — so the swipe panel's "More" button opens this bottom
 *    sheet instead. The sheet clones the row's overflow-menu entries
 *    (already i18n'd and per-row conditioned server-side), restyles
 *    them as large touch rows, and submits them by requestSubmit() on
 *    the row's real forms so undo banners and in-place row updates
 *    behave identically to the desktop path.
 */
(function () {
  'use strict';

  var MOVE_URL_TEMPLATE = '/app/mail/message/{accountId}/{messageId}/move';

  var ICONS = {
    mark: '<svg class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path d="M2.22 4.47c-.27.11-.44.38-.44.67A11.5 11.5 0 0013.17 16.5c.29 0 .56-.17.67-.44l.86-2.15a.75.75 0 00-.42-.97l-2.4-.96a.75.75 0 00-.8.17l-.72.72a9 9 0 01-3.06-3.06l.72-.72a.75.75 0 00.17-.8l-.96-2.4a.75.75 0 00-.97-.42l-2.15.86z" clip-rule="evenodd"/><path d="M4.5 7.5a8 8 0 0011 7.75l-.44 1.1A11.5 11.5 0 013.28 6.05l1.1-.44A8 8 0 004.5 7.5z"/></svg>',
    junk: '<svg class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-8-5a.75.75 0 01.75.75v4.5a.75.75 0 01-1.5 0v-4.5A.75.75 0 0110 5zm0 10a1 1 0 100-2 1 1 0 000 2z" clip-rule="evenodd"/></svg>',
    'not-junk': '<svg class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-8-5a.75.75 0 01.75.75v4.5a.75.75 0 01-1.5 0v-4.5A.75.75 0 0110 5zm0 10a1 1 0 100-2 1 1 0 000 2z" clip-rule="evenodd"/></svg>',
    lock: '<svg class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 1a4.5 4.5 0 00-4.5 4.5V8H5a2 2 0 00-2 2v6a2 2 0 002 2h10a2 2 0 002-2v-6a2 2 0 00-2-2h-.5V5.5A4.5 4.5 0 0010 1zm3 8V5.5a3 3 0 10-6 0V9h6z" clip-rule="evenodd"/></svg>',
    move: '<svg class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path d="M3.5 2A1.5 1.5 0 002 3.5V5c0 1.1.9 2 2 2h12a2 2 0 002-2V3.5A1.5 1.5 0 0016.5 2h-13zM4 8.5a.5.5 0 01.5-.5h11a.5.5 0 01.5.5v8a.5.5 0 01-.5.5h-11a.5.5 0 01-.5-.5v-8z"/></svg>'
  };

  var BACK_ICON = '<svg class="h-4 w-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" d="M17 10a.75.75 0 0 1-.75.75H5.612l4.158 3.96a.75.75 0 0 1-1.04 1.08l-5.5-5.25a.75.75 0 0 1 0-1.08l5.5-5.25a.75.75 0 1 1 1.04 1.08L5.612 9.25H16.25A.75.75 0 0 1 17 10z" clip-rule="evenodd"/></svg>';

  function t(key) {
    return window.LR ? window.LR.t(key) : key;
  }

  function folderLabelOf(opts, folder) {
    return (opts.folderLabels && opts.folderLabels[folder]) || folder;
  }

  // ---- shared: move a message via the inline move endpoint ----

  function moveMessage(row, destination, target) {
    var messageId = row.dataset.messageId;
    var accountId = row.dataset.accountId;
    if (!messageId || !accountId || !destination) {
      return Promise.reject(new Error('missing row data for move'));
    }
    if (target && !target.disabled) {
      target.disabled = true;
      var spinner = document.createElement('span');
      spinner.className = 'ml-1 inline-block h-3 w-3 animate-spin rounded-full border-2 border-slate-400 border-r-transparent';
      spinner.dataset.moveSpinner = '1';
      target.appendChild(spinner);
    }
    var url = MOVE_URL_TEMPLATE
      .replace('{accountId}', encodeURIComponent(accountId))
      .replace('{messageId}', encodeURIComponent(messageId));
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-Requested-With': 'XMLHttpRequest' },
      body: 'destination=' + encodeURIComponent(destination)
    }).then(function (resp) {
      if (!resp.ok) throw new Error('Move failed');
      return resp.json();
    });
  }

  // ---- desktop dropdown folder picker (ported from message-list.js) ----

  function initDropdownPicker(container, opts) {
    var folders = opts.folders || [];
    var currentFolder = opts.currentFolder || '';

    var renderFolderPicker = function (menu) {
      if (!menu) return;
      if (!menu.dataset.originalContent) {
        menu.dataset.originalContent = menu.innerHTML;
      }
      var targets = folders.filter(function (f) {
        return f.toLowerCase() !== currentFolder.toLowerCase();
      });
      if (targets.length === 0) {
        if (window.LR) window.LR.notifyError(window.LR.t('No other folders available.'));
        opts.onRestore(menu);
        return;
      }
      var html = '<button type="button" class="w-full text-left text-[12px] md:text-[11px] px-3 py-2 text-slate-500 hover:bg-slate-50 hover:text-slate-600 whitespace-nowrap" data-move-back>&larr; ' + window.LR.t('Back') + '</button>';
      html += '<div class="border-t border-slate-100"></div>';
      html += '<div class="px-2 py-1"><input type="text" placeholder="' + window.LR.t('Filter folders...') + '" class="w-full text-base md:text-[11px] px-2 py-1 rounded border border-slate-200 bg-white focus:outline-none focus:border-slate-400" data-folder-filter /></div>';
      html += '<div class="max-h-40 overflow-y-auto" data-folder-list>';
      targets.forEach(function (f) {
        html += '<button type="button" class="w-full text-left text-[12px] md:text-[11px] px-3 py-2 text-slate-600 hover:bg-slate-50 hover:text-slate-900 whitespace-nowrap" data-move-target="' + CSS.escape(f) + '">' + folderLabelOf(opts, f) + '</button>';
      });
      html += '</div>';
      menu.innerHTML = html;
      var row = menu.closest('.message-row');
      if (row && opts.positionMenu) opts.positionMenu(menu, row);
    };

    container.addEventListener('click', function (e) {
      var moveBtn = e.target.closest('[data-move-to-folder]');
      if (moveBtn) {
        e.preventDefault();
        e.stopPropagation();
        var row = moveBtn.closest('.message-row');
        if (!row) return;
        renderFolderPicker(row.querySelector('[data-overflow-menu]'));
        return;
      }

      var backBtn = e.target.closest('[data-move-back]');
      if (backBtn) {
        e.preventDefault();
        e.stopPropagation();
        var menu = backBtn.closest('[data-overflow-menu]');
        if (opts.onRestore) opts.onRestore(menu);
        var row = menu ? menu.closest('.message-row') : null;
        if (menu && row && opts.positionMenu) opts.positionMenu(menu, row);
        return;
      }

      var target = e.target.closest('[data-move-target]');
      if (target) {
        e.preventDefault();
        e.stopPropagation();
        var row = target.closest('.message-row');
        if (!row) return;
        var menu = row.querySelector('[data-overflow-menu]');
        var destination = target.dataset.moveTarget;
        if (!destination) return;
        moveMessage(row, destination, target)
          .then(function () {
            opts.onMoved(row);
          })
          .catch(function () {
            if (window.LR) window.LR.notifyError(window.LR.t('Failed to move message. Please retry. If it keeps happening, check your connection or refresh.'));
            if (opts.onRestore && menu) opts.onRestore(menu);
          });
        return;
      }
    });

    container.addEventListener('input', function (e) {
      var filter = e.target.closest('[data-folder-filter]');
      if (!filter) return;
      e.stopPropagation();
      var menu = filter.closest('[data-overflow-menu]');
      var list = menu ? menu.querySelector('[data-folder-list]') : null;
      if (!list) return;
      var query = filter.value.toLowerCase();
      list.querySelectorAll('[data-move-target]').forEach(function (btn) {
        var match = !query || btn.textContent.toLowerCase().includes(query);
        btn.classList.toggle('hidden', !match);
      });
    });
  }

  // ---- touch bottom action sheet (UX3g) ----

  var sheetRoot = null;
  var sheetState = null;
  var closeTimer = null;
  var bodyLock = null; // original body overflow while the sheet holds the scroll lock

  function lockBodyScroll() {
    if (!bodyLock) {
      bodyLock = { overflow: document.body.style.overflow };
    }
    document.body.style.overflow = 'hidden';
  }

  function unlockBodyScroll() {
    if (!bodyLock) return;
    document.body.style.overflow = bodyLock.overflow;
    bodyLock = null;
  }

  function ensureSheet() {
    if (sheetRoot) return sheetRoot;
    sheetRoot = document.createElement('div');
    sheetRoot.className = 'lr-sheet';
    sheetRoot.hidden = true;
    sheetRoot.innerHTML =
      '<div class="lr-sheet-backdrop fixed inset-0 z-[60] bg-slate-900/40" data-sheet-backdrop></div>' +
      '<div class="lr-sheet-panel fixed inset-x-0 bottom-0 z-[61] outline-none" role="dialog" aria-modal="true" tabindex="-1" data-sheet-panel>' +
        '<div class="mx-auto w-full max-w-lg rounded-t-2xl bg-white shadow-2xl">' +
          '<div class="flex justify-center pt-2 pb-1" aria-hidden="true"><span class="h-1 w-10 rounded-full bg-slate-300"></span></div>' +
          '<div class="truncate px-5 pb-1 text-center text-[13px] font-medium text-slate-500" data-sheet-title></div>' +
          '<div class="max-h-[60vh] overflow-y-auto overscroll-contain pb-1" data-sheet-content></div>' +
          '<div class="lr-sheet-actions border-t border-slate-100 px-3 pt-2">' +
            '<button type="button" class="w-full rounded-lg py-3 text-[15px] font-medium text-slate-600 active:bg-slate-100" data-sheet-cancel></button>' +
          '</div>' +
        '</div>' +
      '</div>';
    document.body.appendChild(sheetRoot);
    sheetRoot.addEventListener('click', onSheetClick);
    sheetRoot.addEventListener('input', onSheetInput);
    // Touch fast-path (same rationale as js/mail/swipe.js): the click a
    // tap produces can be retargeted off the sheet entry, leaving the
    // tap dead. Touch events resolve reliably — suppress the ghost
    // click and click the hit target directly.
    sheetRoot.addEventListener('touchend', function (e) {
      if (!e.target.closest) return;
      var hit = e.target.closest(
        '[data-sheet-entry], [data-sheet-cancel], [data-sheet-back], [data-sheet-move], [data-sheet-backdrop]'
      );
      if (!hit) return;
      if (e.cancelable) e.preventDefault();
      hit.click();
    });
    return sheetRoot;
  }

  function sheetEl(name) {
    return sheetRoot ? sheetRoot.querySelector('[' + name + ']') : null;
  }

  function setSheetTitle(text) {
    var title = sheetEl('data-sheet-title');
    if (!title) return;
    title.textContent = text;
    title.hidden = !text;
  }

  function buildEntryButton(kind, label, icon) {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.dataset.sheetEntry = kind;
    btn.className = 'flex w-full items-center gap-3 border-t border-slate-100 px-5 py-3.5 text-left text-[15px] text-slate-700 first:border-t-0 active:bg-slate-100';
    var iconWrap = document.createElement('span');
    iconWrap.className = 'flex h-5 w-5 flex-shrink-0 items-center justify-center text-slate-400';
    iconWrap.setAttribute('aria-hidden', 'true');
    iconWrap.innerHTML = icon;
    var labelWrap = document.createElement('span');
    labelWrap.className = 'flex-1 truncate';
    labelWrap.textContent = label;
    btn.appendChild(iconWrap);
    btn.appendChild(labelWrap);
    return btn;
  }

  function renderSheetEntries(row) {
    var content = sheetEl('data-sheet-content');
    if (!content) return;
    var menu = row ? row.querySelector('[data-overflow-menu]') : null;
    content.innerHTML = '';
    if (!menu) return;

    var holder = document.createElement('div');
    holder.innerHTML = menu.innerHTML;
    holder.querySelectorAll('form.message-action').forEach(function (form) {
      var button = form.querySelector('button');
      if (!button) return;
      var action = form.dataset.action || '';
      content.appendChild(buildEntryButton(action, button.textContent.trim(), ICONS[action] || ''));
    });
    var moveBtn = holder.querySelector('[data-move-to-folder]');
    if (moveBtn) {
      content.appendChild(buildEntryButton('move', moveBtn.textContent.trim(), ICONS.move));
    }
  }

  function buildBackRow() {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.dataset.sheetBack = '';
    btn.className = 'flex w-full items-center gap-2 px-4 py-2.5 text-left text-[13px] font-medium text-sky-600 active:bg-slate-100';
    btn.innerHTML = BACK_ICON;
    btn.appendChild(document.createTextNode(t('Back')));
    return btn;
  }

  function renderSheetPicker() {
    if (!sheetState) return;
    var opts = sheetState.opts;
    var content = sheetEl('data-sheet-content');
    if (!content) return;
    var folders = opts.folders || [];
    var currentFolder = opts.currentFolder || '';
    var targets = folders.filter(function (f) {
      return f.toLowerCase() !== currentFolder.toLowerCase();
    });

    setSheetTitle(t('Move to folder'));
    content.innerHTML = '';
    content.appendChild(buildBackRow());

    if (targets.length === 0) {
      var empty = document.createElement('div');
      empty.className = 'px-5 py-6 text-center text-[13px] text-slate-400';
      empty.textContent = t('No other folders available.');
      content.appendChild(empty);
      return;
    }

    var inputWrap = document.createElement('div');
    inputWrap.className = 'px-4 pb-2 pt-1';
    var input = document.createElement('input');
    input.type = 'text';
    input.dataset.sheetFilter = '';
    input.placeholder = t('Filter folders...');
    input.className = 'w-full rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-base focus:border-slate-400 focus:outline-none';
    inputWrap.appendChild(input);
    content.appendChild(inputWrap);

    var list = document.createElement('div');
    list.dataset.sheetFolders = '';
    list.className = 'max-h-[45vh] overflow-y-auto overscroll-contain';
    targets.forEach(function (f) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.dataset.sheetMove = f;
      btn.className = 'flex w-full items-center border-t border-slate-100 px-5 py-3 text-left text-[15px] text-slate-700 first:border-t-0 active:bg-slate-100';
      btn.textContent = folderLabelOf(opts, f);
      list.appendChild(btn);
    });
    content.appendChild(list);
  }

  function closeSheet() {
    if (!sheetState) return;
    var st = sheetState;
    sheetState = null;
    sheetRoot.classList.remove('is-open');
    // The slide-down transition is 250ms (none under prefers-reduced-motion);
    // a timeout covers both cases without accumulating transitionend listeners.
    if (closeTimer) clearTimeout(closeTimer);
    closeTimer = setTimeout(function () {
      closeTimer = null;
      if (sheetState) return; // re-opened while the close was still settling
      sheetRoot.hidden = true;
      unlockBodyScroll();
      if (st.prevFocus && typeof st.prevFocus.focus === 'function') {
        try {
          st.prevFocus.focus({ preventScroll: true });
        } catch (err) {
          // The previously focused node may have been removed by the action.
        }
      }
      if (typeof st.opts.onMenuClosed === 'function') {
        st.opts.onMenuClosed();
      }
    }, 260);
  }

  function onSheetClick(e) {
    // Backdrop tap: dismiss the sheet and let the event keep bubbling so
    // the page's outside-click handlers also close the revealed panel.
    if (e.target.closest('[data-sheet-backdrop]')) {
      closeSheet();
      return;
    }

    var cancel = e.target.closest('[data-sheet-cancel]');
    if (cancel) {
      e.stopPropagation();
      closeSheet();
      return;
    }

    var entry = e.target.closest('[data-sheet-entry]');
    if (entry) {
      e.preventDefault();
      e.stopPropagation();
      var kind = entry.dataset.sheetEntry;
      if (kind === 'move') {
        renderSheetPicker();
        return;
      }
      var st = sheetState;
      var row = st && st.opts.row;
      closeSheet();
      // Submit the row's real form so the shared inline-action path runs
      // (spinner states, undo banner, in-place row updates).
      var srcForm = row && row.querySelector(
        '[data-overflow-menu] form.message-action[data-action="' + CSS.escape(kind) + '"]'
      );
      if (srcForm) {
        if (typeof srcForm.requestSubmit === 'function') {
          srcForm.requestSubmit();
        } else {
          srcForm.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
        }
      }
      return;
    }

    var back = e.target.closest('[data-sheet-back]');
    if (back) {
      e.stopPropagation();
      if (sheetState) renderSheetEntries(sheetState.opts.row);
      return;
    }

    var moveTarget = e.target.closest('[data-sheet-move]');
    if (moveTarget) {
      e.preventDefault();
      e.stopPropagation();
      var st = sheetState;
      if (!st) return;
      var row = st.opts.row;
      var destination = moveTarget.dataset.sheetMove;
      moveMessage(row, destination, moveTarget)
        .then(function () {
          closeSheet();
          if (typeof st.opts.removeRowWithAnimation === 'function') {
            st.opts.removeRowWithAnimation(row);
          }
        })
        .catch(function () {
          if (window.LR) window.LR.notifyError(window.LR.t('Failed to move message. Please retry. If it keeps happening, check your connection or refresh.'));
          renderSheetPicker();
        });
      return;
    }

    // Any other tap inside the panel must not leak to page handlers.
    if (e.target.closest('[data-sheet-panel]')) {
      e.stopPropagation();
    }
  }

  function onSheetInput(e) {
    var input = e.target.closest('[data-sheet-filter]');
    if (!input) return;
    var list = sheetEl('data-sheet-folders');
    if (!list) return;
    var query = input.value.toLowerCase();
    list.querySelectorAll('[data-sheet-move]').forEach(function (btn) {
      var match = !query || btn.textContent.toLowerCase().includes(query);
      btn.classList.toggle('hidden', !match);
    });
  }

  document.addEventListener('keydown', function (e) {
    if (!sheetState || e.key !== 'Escape') return;
    closeSheet();
  });

  function openSheet(opts) {
    var row = opts.row;
    if (!row || !row.querySelector('[data-overflow-menu]')) return;
    if (sheetState) closeSheet();
    // Cancel a close that is still settling so its timeout cannot hide
    // the freshly re-opened sheet (the body scroll lock stays held).
    if (closeTimer) {
      clearTimeout(closeTimer);
      closeTimer = null;
    }
    ensureSheet();

    var prevFocus = document.activeElement;
    if (prevFocus && sheetRoot.contains(prevFocus)) {
      prevFocus = null;
    }
    sheetState = {
      opts: opts,
      prevFocus: prevFocus
    };

    var panel = sheetEl('data-sheet-panel');
    var cancel = sheetEl('data-sheet-cancel');
    var subject = row.querySelector('[data-subject="true"]') || row.querySelector('[data-subject]');
    setSheetTitle(subject ? subject.textContent.trim() : '');
    if (panel) panel.setAttribute('aria-label', t('More actions'));
    if (cancel) cancel.textContent = t('Cancel');
    renderSheetEntries(row);

    lockBodyScroll();
    sheetRoot.hidden = false;
    // Force a reflow so the slide-up transition plays from the closed state.
    sheetRoot.getBoundingClientRect();
    sheetRoot.classList.add('is-open');
    if (panel) panel.focus({ preventScroll: true });
  }

  window.LROverflowMenu = {
    initDropdownPicker: initDropdownPicker,
    openSheet: openSheet
  };
})();
