/**
 * Docs list page behaviors (HLD U13.33a / U24.32).
 *
 * - Sidebar drawer (U13.60m), "New" dropdown, trash toggle.
 * - AJAX upload with progress (U13.32a).
 * - Relative dates + SSE live refresh.
 * - Per-item kebab action menu with Export-as sub-view (U13.31/U13.33a).
 * - Rename from the list context menu (U13.25).
 * - Touch swipe: right = move to trash, left = quick actions (U24.32).
 */
(function () {
  'use strict';

  // ---------------------------------------------------------------------
  // Sidebar drawer (U24.2/U13.60m): off-canvas below lg.
  // ---------------------------------------------------------------------
  (function initDrawer() {
    var sidebar = document.getElementById('docs-sidebar');
    var backdrop = document.getElementById('docs-sidebar-backdrop');
    var closeBtn = document.getElementById('docs-sidebar-close');
    var toggles = document.querySelectorAll('.docs-sidebar-toggle');
    var isOpen = function () {
      return sidebar && !sidebar.classList.contains('-translate-x-full');
    };
    var setOpen = function (open) {
      if (!sidebar || !backdrop) return;
      sidebar.classList.toggle('-translate-x-full', !open);
      backdrop.classList.toggle('hidden', !open);
      document.body.style.overflow = open ? 'hidden' : '';
      toggles.forEach(function (t) {
        t.setAttribute('aria-expanded', open ? 'true' : 'false');
      });
    };
    toggles.forEach(function (t) {
      t.addEventListener('click', function () {
        setOpen(!isOpen());
      });
    });
    if (closeBtn) closeBtn.addEventListener('click', function () { setOpen(false); });
    if (backdrop) backdrop.addEventListener('click', function () { setOpen(false); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && isOpen()) setOpen(false);
    });
    window.addEventListener('resize', function () {
      if (window.matchMedia('(min-width: 1024px)').matches && isOpen()) setOpen(false);
    });
    // U24.2a: edge-swipe open/close gestures (shared engine in js/drawer.js).
    if (window.LRDrawer) {
      window.LRDrawer.register({
        drawer: sidebar,
        backdrop: backdrop,
        isOpen: isOpen,
        setOpen: setOpen
      });
    }
  })();

  // ---------------------------------------------------------------------
  // "New document" dropdown + trash section toggle.
  // ---------------------------------------------------------------------
  (function initDropdowns() {
    var btn = document.getElementById('new-doc-button');
    var panel = document.getElementById('new-doc-panel');
    if (btn && panel) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        panel.classList.toggle('hidden');
      });
      document.addEventListener('click', function () {
        panel.classList.add('hidden');
      });
      document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') panel.classList.add('hidden');
      });
    }

    var trashToggle = document.getElementById('trash-toggle');
    var trashSection = document.getElementById('trash-section');
    var trashChevron = document.getElementById('trash-chevron');
    if (trashToggle && trashSection) {
      trashToggle.addEventListener('click', function () {
        var open = !trashSection.classList.contains('hidden');
        trashSection.classList.toggle('hidden');
        if (trashChevron) trashChevron.style.transform = open ? '' : 'rotate(180deg)';
      });
    }
  })();

  // ---------------------------------------------------------------------
  // AJAX upload (U13.32a/32b) with progress, popup fallback, retry.
  // ---------------------------------------------------------------------
  (function initUpload() {
    var uploadForm = document.getElementById('upload-form');
    var uploadInput = document.getElementById('upload-input');
    var uploadLabel = document.getElementById('upload-label');
    var uploadLabelText = document.getElementById('upload-label-text');
    var uploadProgress = document.getElementById('upload-progress');
    var uploadProgressBar = document.getElementById('upload-progress-bar');
    var uploadProgressText = document.getElementById('upload-progress-text');
    var uploadResult = document.getElementById('upload-result');

    function setUploadState(state) {
      uploadProgress.classList.toggle('hidden', state !== 'uploading' && state !== 'converting');
      uploadResult.classList.toggle('hidden', state !== 'success' && state !== 'error');

      if (state === 'idle') {
        uploadLabel.classList.remove('pointer-events-none', 'opacity-50');
        uploadLabelText.textContent = window.LR.t('Upload');
        uploadInput.value = '';
      } else if (state === 'uploading') {
        uploadLabel.classList.add('pointer-events-none', 'opacity-50');
        uploadLabelText.textContent = window.LR.t('Uploading...');
      } else if (state === 'converting') {
        uploadLabelText.textContent = window.LR.t('Converting...');
        uploadProgressBar.style.width = '100%';
        uploadProgressBar.classList.remove('bg-slate-900');
        uploadProgressBar.classList.add('bg-blue-500');
        uploadProgressText.textContent = window.LR.t('Converting...');
      } else {
        uploadProgressBar.classList.remove('bg-blue-500');
        uploadProgressBar.classList.add('bg-slate-900');
      }
    }

    function showError(msg) {
      uploadResult.innerHTML = '';
      var errSpan = document.createElement('span');
      errSpan.className = 'text-sm text-rose-600';
      errSpan.textContent = msg;
      uploadResult.appendChild(errSpan);
      var retryBtn = document.createElement('button');
      retryBtn.type = 'button';
      retryBtn.className = 'text-sm font-medium text-slate-700 hover:text-slate-900 underline underline-offset-2';
      retryBtn.textContent = window.LR.t('Retry');
      retryBtn.addEventListener('click', function () { setUploadState('idle'); });
      uploadResult.appendChild(retryBtn);
      setTimeout(function () { setUploadState('idle'); }, 8000);
    }

    if (!uploadInput) return;

    uploadInput.addEventListener('change', function () {
      var file = uploadInput.files[0];
      if (!file) return;

      var ext = file.name.split('.').pop().toLowerCase();
      var allowed = ['odt', 'ods', 'odp', 'docx', 'xlsx', 'pptx', 'pdf'];
      if (!allowed.includes(ext)) {
        window.LR.notifyError(window.LR.t('Unsupported file type. Accepted: .odt, .ods, .odp, .docx, .xlsx, .pptx'), { suffix: false });
        uploadInput.value = '';
        return;
      }

      if (file.size > 50 * 1024 * 1024) {
        window.LR.notifyError(window.LR.t('File exceeds 50 MB limit.'), { suffix: false });
        uploadInput.value = '';
        return;
      }

      setUploadState('uploading');

      var fd = new FormData();
      fd.append('file', file);

      var xhr = new XMLHttpRequest();
      xhr.open('POST', uploadForm.action);
      xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');

      xhr.upload.addEventListener('progress', function (e) {
        if (e.lengthComputable) {
          var pct = Math.round((e.loaded / e.total) * 100);
          uploadProgressBar.style.width = pct + '%';
          uploadProgressText.textContent = pct + '%';
        }
      });

      xhr.addEventListener('load', function () {
        var data;
        try { data = JSON.parse(xhr.responseText); } catch (err) { data = null; }
        if (xhr.status >= 200 && xhr.status < 300 && data && data.editor_url) {
          setUploadState('success');
          var newTab = window.open(data.editor_url, '_blank');
          uploadResult.innerHTML = '';
          var link = document.createElement('a');
          link.href = data.editor_url;
          link.target = '_blank';
          link.className = 'text-sm font-medium text-slate-900 hover:text-slate-700 underline underline-offset-2';
          link.textContent = window.LR.t('Open document');
          uploadResult.appendChild(link);
          if (!newTab || newTab.closed) {
            var hint = document.createElement('span');
            hint.className = 'text-xs text-slate-400';
            hint.textContent = window.LR.t('(popup blocked — use the link)');
            uploadResult.appendChild(hint);
          }
          setTimeout(function () { setUploadState('idle'); window.location.reload(); }, 4000);
        } else {
          setUploadState('error');
          showError((data && data.error) ? data.error : window.LR.t('Upload failed. Please try again.'));
        }
      });

      xhr.addEventListener('error', function () {
        setUploadState('error');
        showError(window.LR.t('Network error. Check your connection and try again.'));
      });

      xhr.send(fd);
    });
  })();

  // ---------------------------------------------------------------------
  // Relative timestamps.
  // ---------------------------------------------------------------------
  (function initDates() {
    function relativeTime(isoStr) {
      if (!isoStr) return '';
      var d = new Date(isoStr + (isoStr.includes('Z') || isoStr.includes('+') ? '' : 'Z'));
      if (isNaN(d.getTime())) return isoStr;
      var now = new Date();
      var diffSec = Math.floor((now - d) / 1000);
      if (diffSec < 5) return window.LR.t('just now');
      if (diffSec < 60) return window.LR.t('{n} seconds ago', { n: diffSec });
      var diffMin = Math.floor(diffSec / 60);
      if (diffMin === 1) return window.LR.t('1 minute ago');
      if (diffMin < 60) return window.LR.t('{n} minutes ago', { n: diffMin });
      var diffHr = Math.floor(diffMin / 60);
      if (diffHr === 1) return window.LR.t('1 hour ago');
      if (diffHr < 24) return window.LR.t('{n} hours ago', { n: diffHr });
      var diffDay = Math.floor(diffHr / 24);
      if (diffDay === 1) return window.LR.t('yesterday');
      if (diffDay < 30) return window.LR.t('{n} days ago', { n: diffDay });
      var diffMonth = Math.floor(diffDay / 30);
      if (diffMonth === 1) return window.LR.t('1 month ago');
      if (diffMonth < 12) return window.LR.t('{n} months ago', { n: diffMonth });
      var diffYear = Math.floor(diffDay / 365);
      if (diffYear === 1) return window.LR.t('1 year ago');
      return window.LR.t('{n} years ago', { n: diffYear });
    }

    function localDatetime(isoStr) {
      if (!isoStr) return '';
      var d = new Date(isoStr + (isoStr.includes('Z') || isoStr.includes('+') ? '' : 'Z'));
      if (isNaN(d.getTime())) return isoStr;
      return d.toLocaleString(undefined, {
        year: 'numeric', month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit'
      });
    }

    document.querySelectorAll('.relative-date').forEach(function (el) {
      var iso = el.dataset.datetime;
      el.textContent = relativeTime(iso);
      el.title = localDatetime(iso);
      el.classList.add('cursor-default');
    });

    // Exposed for the share modal's "last accessed" column.
    window.LRDocsRelativeTime = relativeTime;
  })();

  // ---------------------------------------------------------------------
  // SSE live refresh.
  // ---------------------------------------------------------------------
  (function initSse() {
    var sse = new EventSource('/events/stream');
    sse.addEventListener('ui_change', function (evt) {
      var d = JSON.parse(evt.data || '{}');
      if (d.module === 'docs') window.location.reload();
    });
  })();

  // ---------------------------------------------------------------------
  // Per-item kebab menus (U13.33a): open/close, close-others, Escape,
  // outside-click, Export-as sub-view, close-after-action.
  // ---------------------------------------------------------------------
  (function initMenus() {
    function panelOf(menu) {
      return menu.querySelector('[data-doc-menu-panel]');
    }

    function resetViews(menu) {
      menu.querySelectorAll('[data-menu-view]').forEach(function (view) {
        view.classList.toggle('hidden', view.getAttribute('data-menu-view') !== 'main');
      });
    }

    function closeMenu(menu) {
      var panel = panelOf(menu);
      if (!panel) return;
      panel.classList.add('hidden');
      resetViews(menu);
      var toggle = menu.querySelector('[data-doc-menu-toggle]');
      if (toggle) toggle.setAttribute('aria-expanded', 'false');
    }

    function closeAll(except) {
      document.querySelectorAll('[data-doc-menu]').forEach(function (menu) {
        if (menu !== except) closeMenu(menu);
      });
    }

    // Menu-item handlers in other docs JS files stopPropagation, so they call
    // this explicitly to close the containing menu.
    window.LRDocsCloseMenu = function (el) {
      var menu = el && el.closest ? el.closest('[data-doc-menu]') : null;
      if (menu) closeMenu(menu);
      else closeAll(null);
    };

    document.addEventListener('click', function (e) {
      var toggle = e.target.closest('[data-doc-menu-toggle]');
      if (toggle) {
        e.preventDefault();
        e.stopPropagation();
        var menu = toggle.closest('[data-doc-menu]');
        var panel = panelOf(menu);
        var willOpen = panel.classList.contains('hidden');
        closeAll(menu);
        panel.classList.toggle('hidden', !willOpen);
        if (willOpen) resetViews(menu);
        toggle.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
        return;
      }

      var exportBtn = e.target.closest('[data-menu-export-btn]');
      if (exportBtn) {
        e.preventDefault();
        e.stopPropagation();
        var m = exportBtn.closest('[data-doc-menu]');
        m.querySelectorAll('[data-menu-view]').forEach(function (view) {
          view.classList.toggle('hidden', view.getAttribute('data-menu-view') !== 'export');
        });
        return;
      }

      var backBtn = e.target.closest('[data-menu-export-back]');
      if (backBtn) {
        e.preventDefault();
        e.stopPropagation();
        resetViews(backBtn.closest('[data-doc-menu]'));
        return;
      }

      // Any click outside an open panel closes it; clicks on menu actions
      // (links/forms) let the event run but still close the menu.
      var openMenu = e.target.closest('[data-doc-menu]');
      if (!openMenu || !e.target.closest('[data-doc-menu-panel]')) {
        closeAll(null);
      } else {
        closeMenu(openMenu);
      }
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') closeAll(null);
    });
  })();

  // ---------------------------------------------------------------------
  // Rename from the list context menu (U13.25): prompt + POST rename.
  // ---------------------------------------------------------------------
  (function initRename() {
    document.querySelectorAll('.rename-doc-btn').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        if (window.LRDocsCloseMenu) window.LRDocsCloseMenu(btn);
        var docId = btn.getAttribute('data-doc-id');
        var name = btn.getAttribute('data-doc-name');
        var newName = window.prompt(window.LR.t('Rename document'), name);
        if (newName === null) return;
        newName = newName.trim();
        if (!newName || newName === name) return;
        window.LR.setButtonLoading(btn);
        fetch('/app/docs/' + encodeURIComponent(docId) + '/rename', {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: 'name=' + encodeURIComponent(newName)
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (data.ok) window.location.reload();
            else window.LR.notifyError(data.error || window.LR.t('Rename failed.'));
          })
          .catch(function () {
            window.LR.notifyError(window.LR.t('Network error. Please try again.'), { suffix: false });
          })
          .finally(function () { window.LR.clearButtonLoading(btn); });
      });
    });
  })();

  // ---------------------------------------------------------------------
  // Touch swipe gestures on mobile cards (U24.32, mail UX3g pattern):
  // swipe right = move to trash, swipe left = quick-action panel.
  // ---------------------------------------------------------------------
  (function initSwipe() {
    var SWIPE_ENGAGE_PX = 12;
    var SWIPE_TRIGGER_PX = 60;
    var SWIPE_QUERY = window.matchMedia('(max-width: 1023.98px)');
    var REDUCED_MOTION_QUERY = window.matchMedia('(prefers-reduced-motion: reduce)');
    var container = document.getElementById('docs-mobile-list');
    if (!container) return;

    var swipe = null;

    function foregroundOf(row) {
      return row.querySelector('[data-row-foreground]');
    }

    function resetRow(row) {
      var fg = foregroundOf(row);
      if (fg) {
        fg.style.transition = '';
        fg.style.transform = '';
      }
      row.classList.remove('is-swipe-open');
      row.querySelectorAll('[data-swipe-panel]').forEach(function (panel) {
        panel.classList.add('hidden');
      });
    }

    function closeAll(exceptRow) {
      container.querySelectorAll('.doc-swipe-row.is-swipe-open').forEach(function (row) {
        if (exceptRow && row === exceptRow) return;
        resetRow(row);
      });
    }

    container.addEventListener('touchstart', function (e) {
      if (!SWIPE_QUERY.matches || !e.touches || e.touches.length !== 1) return;
      // U24.2a: edge-origin touches belong to the drawer gesture.
      if (window.LRDrawer && window.LRDrawer.claims(e.touches[0].clientX)) return;
      var row = e.target.closest('.doc-swipe-row');
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
        // Horizontal must dominate before the gesture engages (UX3g).
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

    function finishSwipe() {
      if (!swipe) return;
      var st = swipe;
      swipe = null;
      if (!st.engaged) return;
      st.fg.style.transition = REDUCED_MOTION_QUERY.matches ? 'none' : '';
      if (st.dx >= SWIPE_TRIGGER_PX) {
        // Full right swipe: move to trash (soft delete, restore from Trash).
        st.fg.style.transform = 'translateX(' + (st.row.offsetWidth + 40) + 'px)';
        window.setTimeout(function () {
          var form = st.row.querySelector('[data-swipe-trash-form]');
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
    }

    container.addEventListener('touchend', finishSwipe);
    container.addEventListener('touchcancel', function () {
      if (swipe && swipe.engaged) resetRow(swipe.row);
      swipe = null;
    });

    // Panel-button activation fast-path (same rationale as mail's
    // js/mail/swipe.js): Chrome can retarget the click a tap produces to
    // the card even when every touch event resolved to the revealed
    // panel button, leaving the tap dead. Touch events keep the
    // touchstart target for the whole sequence, so ending on a panel
    // button is reliable: suppress the ghost click and click it directly.
    container.addEventListener('touchend', function (e) {
      if (!e.target.closest) return;
      var panelButton = e.target.closest('[data-swipe-panel] a, [data-swipe-panel] button');
      if (!panelButton) return;
      if (e.cancelable) e.preventDefault();
      panelButton.click();
    });

    // Tapping anywhere in an open card's foreground closes the reveal.
    container.addEventListener('click', function (e) {
      var open = e.target.closest('.doc-swipe-row.is-swipe-open');
      if (open && !e.target.closest('[data-doc-menu-panel]')) resetRow(open);
    });
  })();
})();
