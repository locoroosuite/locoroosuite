/**
 * Shared message-list behavior (HLD UX2/UX3b/UX3d) for the folder view,
 * search results, and full-search results: the row action overlay with its
 * "..." touch toggle (data-overlay-locked), the overflow menu and
 * move-to-folder picker, inline form.message-action submits, thread
 * collapse, and row navigation.
 *
 * UX3b: the overlay is pointer-events-none by default; on hover-capable
 * devices group-hover reveals it (gated by @media (hover:hover) and
 * (pointer:fine) via Tailwind hoverOnlyWhenSupported). On touch devices the
 * "..." toggle locks it open via [data-overlay-locked].
 */
(function () {
  'use strict';

  var MOVE_URL_TEMPLATE = '/app/mail/message/{accountId}/{messageId}/move';

  function initMessageList(opts) {
    var container = opts.container;
    if (!container) {
      return null;
    }
    var folders = opts.folders || [];
    var currentFolder = opts.currentFolder || '';
    var previewPane = opts.previewPane || null;

    var openOverflowMenu = null;
    var expandedThreads = new Set();

    var closeMessageActions = function (exceptRow) {
      container.querySelectorAll('.message-row.is-actions-open').forEach(function (row) {
        if (exceptRow && row === exceptRow) {
          return;
        }
        row.classList.remove('is-actions-open');
        row.removeAttribute('data-overlay-locked');
        var toggle = row.querySelector('[data-message-actions-toggle]');
        if (toggle) {
          toggle.setAttribute('aria-expanded', 'false');
        }
        var overflowMenu = row.querySelector('[data-overflow-menu]');
        if (overflowMenu) {
          overflowMenu.classList.add('hidden');
        }
      });
    };

    var positionOverflowMenu = function (menu, row) {
      menu.classList.remove('bottom-full', 'mb-1', 'top-full', 'mt-1');
      var rowRect = row.getBoundingClientRect();
      var spaceAbove = rowRect.top;
      var spaceBelow = window.innerHeight - rowRect.bottom;
      var menuHeight = menu.scrollHeight || 160;
      if (spaceAbove < menuHeight && spaceBelow > spaceAbove) {
        menu.classList.add('top-full', 'mt-1');
      } else {
        menu.classList.add('bottom-full', 'mb-1');
      }
    };

    var renderOverflowMenu = function (menu) {
      if (!menu) return;
      menu.innerHTML = menu.dataset.originalContent || '';
    };

    var closeOverflowMenu = function () {
      if (!openOverflowMenu) return;
      var menu = openOverflowMenu;
      var row = menu.closest('.message-row');
      renderOverflowMenu(menu);
      menu.classList.add('hidden');
      menu.classList.remove('bottom-full', 'mb-1', 'top-full', 'mt-1');
      openOverflowMenu = null;
      if (row && !row.classList.contains('is-actions-open')) {
        row.removeAttribute('data-overlay-locked');
      }
      if (typeof opts.onMenuClosed === 'function') {
        opts.onMenuClosed();
      }
    };

    var renderFolderPicker = function (menu) {
      if (!menu) return;
      if (!menu.dataset.originalContent) {
        menu.dataset.originalContent = menu.innerHTML;
      }
      var targets = folders.filter(function (f) {
        return f.toLowerCase() !== currentFolder.toLowerCase();
      });
      if (targets.length === 0) {
        if (window.LR) window.LR.notifyError('No other folders available.');
        renderOverflowMenu(menu);
        return;
      }
      var html = '<button type="button" class="w-full text-left text-[12px] md:text-[11px] px-3 py-2 text-slate-500 hover:bg-slate-50 hover:text-slate-600 whitespace-nowrap" data-move-back>&larr; Back</button>';
      html += '<div class="border-t border-slate-100"></div>';
      html += '<div class="px-2 py-1"><input type="text" placeholder="Filter folders..." class="w-full text-base md:text-[11px] px-2 py-1 rounded border border-slate-200 bg-white focus:outline-none focus:border-slate-400" data-folder-filter /></div>';
      html += '<div class="max-h-40 overflow-y-auto" data-folder-list>';
      targets.forEach(function (f) {
        html += '<button type="button" class="w-full text-left text-[12px] md:text-[11px] px-3 py-2 text-slate-600 hover:bg-slate-50 hover:text-slate-900 whitespace-nowrap" data-move-target="' + CSS.escape(f) + '">' + f + '</button>';
      });
      html += '</div>';
      menu.innerHTML = html;
      var row = menu.closest('.message-row');
      if (row) positionOverflowMenu(menu, row);
    };

    container.addEventListener('click', function (e) {
      // UX3b touch toggle: lock/unlock the row's action overlay.
      var toggle = e.target.closest('[data-message-actions-toggle]');
      if (toggle) {
        e.preventDefault();
        e.stopPropagation();
        var row = toggle.closest('.message-row');
        if (!row) return;
        var isOpen = row.classList.contains('is-actions-open');
        if (!isOpen) {
          closeMessageActions(row);
        }
        row.classList.toggle('is-actions-open', !isOpen);
        if (!isOpen) {
          row.setAttribute('data-overlay-locked', '');
        } else {
          if (openOverflowMenu && row.contains(openOverflowMenu)) {
            closeOverflowMenu();
          }
          row.removeAttribute('data-overlay-locked');
        }
        toggle.setAttribute('aria-expanded', isOpen ? 'false' : 'true');
        return;
      }

      var overflowToggle = e.target.closest('[data-overflow-toggle]');
      if (overflowToggle) {
        e.preventDefault();
        e.stopPropagation();
        var row = overflowToggle.closest('.message-row');
        if (!row) return;
        var menu = row.querySelector('[data-overflow-menu]');
        if (!menu) return;
        var wasHidden = menu.classList.contains('hidden');
        if (openOverflowMenu && openOverflowMenu !== menu) {
          closeOverflowMenu();
        }
        if (wasHidden) {
          menu.classList.remove('hidden');
          positionOverflowMenu(menu, row);
          openOverflowMenu = menu;
          row.setAttribute('data-overlay-locked', '');
        } else {
          closeOverflowMenu();
        }
        return;
      }

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
        renderOverflowMenu(menu);
        var row = menu ? menu.closest('.message-row') : null;
        if (menu && row) positionOverflowMenu(menu, row);
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
        var messageId = row.dataset.messageId;
        var accountId = row.dataset.accountId;
        if (!messageId || !accountId) return;
        target.disabled = true;
        var spinner = document.createElement('span');
        spinner.className = 'inline-block w-3 h-3 border-2 border-slate-400 border-r-transparent rounded-full animate-spin ml-1';
        target.appendChild(spinner);
        var url = MOVE_URL_TEMPLATE
          .replace('{accountId}', encodeURIComponent(accountId))
          .replace('{messageId}', encodeURIComponent(messageId));
        fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-Requested-With': 'XMLHttpRequest' },
          body: 'destination=' + encodeURIComponent(destination)
        })
          .then(function (resp) {
            if (!resp.ok) throw new Error('Move failed');
            return resp.json();
          })
          .then(function () {
            openOverflowMenu = null;
            removeRowWithAnimation(row);
          })
          .catch(function () {
            if (window.LR) window.LR.notifyError('Failed to move message. Please retry. If it keeps happening, check your connection or refresh.');
            renderOverflowMenu(menu);
          });
        return;
      }

      // Thread collapse bar (folder view thread cards).
      var toggleBar = e.target.closest('[data-thread-collapse-toggle]');
      if (toggleBar) {
        e.stopPropagation();
        var wrapper = toggleBar.parentElement;
        var content = wrapper.querySelector('[data-thread-collapse-content]');
        if (!content) return;
        var chevron = toggleBar.querySelector('[data-collapse-chevron]');
        var label = toggleBar.querySelector('[data-collapse-label]');
        var isExpanded = toggleBar.getAttribute('aria-expanded') === 'true';
        var card = toggleBar.closest('[data-thread-card]');
        if (isExpanded) {
          content.style.maxHeight = content.scrollHeight + 'px';
          requestAnimationFrame(function () {
            content.style.maxHeight = '0';
            content.style.opacity = '0';
          });
          toggleBar.setAttribute('aria-expanded', 'false');
          if (chevron) chevron.style.transform = 'rotate(0deg)';
          if (label) {
            var count = parseInt(toggleBar.dataset.olderCount || content.querySelectorAll('.message-row').length, 10);
            label.textContent = count + ' older message' + (count !== 1 ? 's' : '');
          }
          if (card && card.dataset.threadKey) {
            expandedThreads.delete(card.dataset.threadKey);
          }
        } else {
          toggleBar.setAttribute('aria-expanded', 'true');
          content.style.maxHeight = content.scrollHeight + 'px';
          content.style.opacity = '1';
          if (chevron) chevron.style.transform = 'rotate(180deg)';
          if (label) label.textContent = 'Collapse older messages';
          var onEnd = function () {
            content.style.maxHeight = 'none';
            content.removeEventListener('transitionend', onEnd);
          };
          content.addEventListener('transitionend', onEnd);
          if (card && card.dataset.threadKey) {
            expandedThreads.add(card.dataset.threadKey);
          }
        }
        return;
      }

      // Row navigation.
      var row = e.target.closest('.message-row');
      if (!row) return;
      if (e.target.closest('[data-no-row-nav]')) return;
      var messageUrl = row.dataset.messageUrl;
      if (!messageUrl) return;
      if (e.metaKey || e.ctrlKey) {
        window.open(messageUrl, '_blank');
        return;
      }
      if (previewPane && !previewPane.classList.contains('hidden') && typeof opts.onLoadPreview === 'function') {
        document.querySelectorAll('.message-row').forEach(function (el) {
          el.classList.remove('bg-slate-50');
        });
        row.classList.add('bg-slate-50');
        opts.onLoadPreview(row);
        return;
      }
      window.location.href = messageUrl;
    });

    container.addEventListener('input', function (e) {
      var filter = e.target.closest('[data-folder-filter]');
      if (!filter) return;
      e.stopPropagation();
      var list = filter.closest('[data-overflow-menu]').querySelector('[data-folder-list]');
      if (!list) return;
      var query = filter.value.toLowerCase();
      list.querySelectorAll('[data-move-target]').forEach(function (btn) {
        var match = !query || btn.textContent.toLowerCase().includes(query);
        btn.classList.toggle('hidden', !match);
      });
    });

    container.addEventListener('auxclick', function (e) {
      if (e.button !== 1) return;
      var row = e.target.closest('.message-row');
      if (!row) return;
      if (e.target.closest('[data-no-row-nav]')) return;
      var messageUrl = row.dataset.messageUrl;
      if (!messageUrl) return;
      e.preventDefault();
      window.open(messageUrl, '_blank');
    });

    document.addEventListener('click', function (e) {
      if (e.target.closest('[data-message-actions]') || e.target.closest('[data-message-actions-toggle]')) {
        return;
      }
      closeOverflowMenu();
      closeMessageActions();
    });

    var updateRowUnreadUI = function (row, isUnread) {
      row.dataset.isUnread = isUnread ? '1' : '0';
      row.classList.toggle('bg-amber-100/70', isUnread);
      row.classList.toggle('hover:bg-amber-100', isUnread);
      row.classList.toggle('hover:bg-slate-50/80', !isUnread);
      var subject = row.querySelector('[data-subject="true"]') || row.querySelector('[data-subject]');
      if (subject) {
        subject.classList.toggle('font-bold', isUnread);
        subject.classList.toggle('text-slate-950', isUnread);
        subject.classList.toggle('text-slate-700', !isUnread);
      }
      var markForm = row.querySelector('form[data-action="mark"]');
      if (markForm) {
        var input = markForm.querySelector('input[name="action"]');
        var button = markForm.querySelector('button');
        if (input) {
          input.value = isUnread ? 'read' : 'unread';
        }
        if (button) {
          button.textContent = isUnread ? 'Mark as read' : 'Mark as unread';
        }
      }
    };

    var updateRowFlagUI = function (row, isFlagged) {
      row.dataset.isFlagged = isFlagged ? '1' : '0';
      var starIcon = row.querySelector('[data-star-icon]');
      if (starIcon) {
        starIcon.setAttribute('fill', isFlagged ? 'currentColor' : 'none');
        starIcon.setAttribute('stroke-width', isFlagged ? '1' : '1.5');
        starIcon.classList.toggle('text-amber-400', isFlagged);
        starIcon.classList.toggle('text-slate-300', !isFlagged);
        starIcon.classList.toggle('hover:text-amber-400', !isFlagged);
      }
      var starToggle = row.querySelector('[data-star-toggle]');
      if (starToggle) {
        starToggle.setAttribute('aria-label', isFlagged ? 'Unstar' : 'Star');
      }
      var form = row.querySelector('form[data-action="flag"]');
      if (form) {
        var input = form.querySelector('input[name="action"]');
        if (input) {
          input.value = isFlagged ? 'remove' : 'add';
        }
      }
    };

    var updateThreadCountsAfterRemoval = function (threadCard) {
      if (!threadCard) return;
      var rows = threadCard.querySelectorAll('.message-row');
      var badge = threadCard.querySelector('.thread-count');
      if (rows.length === 0) {
        threadCard.remove();
        if (typeof opts.onThreadCardRemoved === 'function') {
          opts.onThreadCardRemoved(threadCard);
        }
        return;
      }
      if (badge) {
        badge.textContent = rows.length;
      }
    };

    var moveThreadCountBadge = function (row) {
      var badge = row.querySelector('.thread-count');
      if (!badge) return;
      var threadCard = row.closest('[data-thread-card="true"]');
      if (!threadCard) return;
      var rows = Array.from(threadCard.querySelectorAll('.message-row')).filter(function (item) {
        return item !== row;
      });
      var target = rows[0];
      if (!target) return;
      var subject = target.querySelector('[data-subject="true"]');
      if (subject && subject.parentElement) {
        subject.parentElement.appendChild(badge);
      } else {
        target.appendChild(badge);
      }
    };

    var removeRowWithAnimation = function (row) {
      var threadCard = row.closest('[data-thread-card="true"]');
      row.classList.add('opacity-0', 'translate-x-2');
      row.style.maxHeight = row.offsetHeight + 'px';
      row.style.overflow = 'hidden';
      requestAnimationFrame(function () {
        row.style.maxHeight = '0px';
        row.style.paddingTop = '0px';
        row.style.paddingBottom = '0px';
      });
      setTimeout(function () {
        moveThreadCountBadge(row);
        row.remove();
        updateThreadCountsAfterRemoval(threadCard);
        if (typeof opts.onListChanged === 'function') {
          opts.onListChanged();
        }
      }, 220);
    };

    container.addEventListener('submit', function (e) {
      var form = e.target.closest('form.message-action');
      if (!form) return;
      e.preventDefault();
      e.stopPropagation();
      var row = form.closest('.message-row');
      var action = form.dataset.action || '';
      var formData = new FormData(form);
      var button = form.querySelector('button');
      var isFlagAction = action === 'flag';
      if (isFlagAction && button) {
        var starIcon = button.querySelector('[data-star-icon]');
        if (starIcon) starIcon.classList.add('hidden');
        var spinner = document.createElement('span');
        spinner.className = 'lr-spinner inline-block w-[18px] h-[18px] border-2 border-slate-400 border-r-transparent rounded-full animate-spin';
        spinner.dataset.starSpinner = '1';
        button.appendChild(spinner);
        button.disabled = true;
      } else if (button && window.LR) {
        window.LR.setButtonLoading(button);
      }
      fetch(form.getAttribute('action') || '', {
        method: 'POST',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        body: formData
      })
        .then(function (resp) {
          return resp.json().catch(function () { return null; });
        })
        .then(function (data) {
          if (!data || !row) {
            if (window.LR) window.LR.notifyError();
            return;
          }
          if (data.error || data.status === 'error') {
            if (window.LR) {
              window.LR.notifyError('Action failed: ' + (data.error || 'Unknown error'), { suffix: !data || data.code !== 'PROTECTED' });
            }
            return;
          }
          if (action === 'mark') {
            var wasUnread = row.dataset.isUnread === '1';
            updateRowUnreadUI(row, data.is_unread);
            var delta = (data.is_unread ? 1 : 0) - (wasUnread ? 1 : 0);
            if (delta !== 0 && typeof opts.onUnreadDelta === 'function') {
              opts.onUnreadDelta(delta);
            }
            var wasFlagged = row.dataset.isFlagged === '1';
            if (wasFlagged && typeof opts.onStarredDelta === 'function') {
              opts.onStarredDelta(delta);
            }
            var overflowMenu = row.querySelector('[data-overflow-menu]');
            if (overflowMenu) overflowMenu.classList.add('hidden');
            if (typeof opts.onListChanged === 'function') opts.onListChanged();
            return;
          }
          if (action === 'flag') {
            var wasFlagged = row.dataset.isFlagged === '1';
            updateRowFlagUI(row, data.is_flagged);
            if (row.dataset.isUnread === '1' && typeof opts.onStarredDelta === 'function') {
              var starredDelta = (data.is_flagged ? 1 : 0) - (wasFlagged ? 1 : 0);
              if (starredDelta !== 0) {
                opts.onStarredDelta(starredDelta);
              }
            }
            var starBtn = form.querySelector('[data-star-toggle]');
            if (starBtn) starBtn.blur();
            return;
          }
          if (action === 'lock') {
            var overflowMenu = row.querySelector('[data-overflow-menu]');
            if (overflowMenu) overflowMenu.classList.add('hidden');
            if (window.LR) {
              window.LR.notifySuccess(data.is_locked ? 'Message locked' : 'Message unlocked');
            }
            return;
          }
          if (action === 'archive' || action === 'delete' || action === 'junk' || action === 'not-junk') {
            if (data.was_unread && typeof opts.onUnreadDelta === 'function') {
              opts.onUnreadDelta(-1);
            }
            if (row.dataset.isFlagged === '1' && row.dataset.isUnread === '1' && typeof opts.onStarredDelta === 'function') {
              opts.onStarredDelta(-1);
            }
            removeRowWithAnimation(row);
            if (data.undo_action && typeof opts.onRenderUndo === 'function') {
              opts.onRenderUndo(data.undo_action);
            }
            return;
          }
        })
        .catch(function () {
          if (window.LR) window.LR.notifyError();
        })
        .finally(function () {
          if (isFlagAction && button) {
            var sp = button.querySelector('[data-star-spinner]');
            if (sp) sp.remove();
            var si = button.querySelector('[data-star-icon]');
            if (si) si.classList.remove('hidden');
            button.disabled = false;
          } else if (button && window.LR) {
            window.LR.clearButtonLoading(button);
          }
        });
    });

    var saveExpandState = function () {
      expandedThreads.clear();
      container.querySelectorAll('[data-thread-collapse-toggle][aria-expanded="true"]').forEach(function (toggle) {
        var card = toggle.closest('[data-thread-card]');
        if (card && card.dataset.threadKey) {
          expandedThreads.add(card.dataset.threadKey);
        }
      });
    };

    var restoreExpandState = function () {
      if (expandedThreads.size === 0) return;
      container.querySelectorAll('[data-thread-card]').forEach(function (card) {
        if (!card.dataset.threadKey || !expandedThreads.has(card.dataset.threadKey)) return;
        var toggle = card.querySelector('[data-thread-collapse-toggle]');
        var content = card.querySelector('[data-thread-collapse-content]');
        if (!toggle || !content) return;
        toggle.setAttribute('aria-expanded', 'true');
        content.style.maxHeight = 'none';
        content.style.opacity = '1';
        var chevron = toggle.querySelector('[data-collapse-chevron]');
        if (chevron) chevron.style.transform = 'rotate(180deg)';
        var label = toggle.querySelector('[data-collapse-label]');
        if (label) label.textContent = 'Collapse older messages';
      });
    };

    return {
      closeMessageActions: closeMessageActions,
      closeOverflowMenu: closeOverflowMenu,
      isOverflowOpen: function () {
        return openOverflowMenu !== null;
      },
      removeRowWithAnimation: removeRowWithAnimation,
      saveExpandState: saveExpandState,
      restoreExpandState: restoreExpandState
    };
  }

  window.LRMessageList = { init: initMessageList };
})();
