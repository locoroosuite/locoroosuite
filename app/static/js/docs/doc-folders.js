/**
 * Docs folder tree + drag-and-drop document move (HLD U13.90+, U13.38 area).
 *
 * Tree expand/collapse, create, rename, delete + undo, and dragging a
 * document row onto a folder to move it.
 */
(function () {
  'use strict';

  function currentFolderFromUrl() {
    var p = new URLSearchParams(window.location.search);
    return (p.get('folder') || '').trim();
  }

  document.querySelectorAll('.folder-toggle').forEach(function (btn) {
    var item = btn.closest('.folder-item');
    var children = item ? item.querySelector(':scope > .folder-children') : null;
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      if (!children) return;
      var open = !children.classList.contains('hidden');
      children.classList.toggle('hidden');
      var chev = btn.querySelector('.folder-chevron');
      if (chev) chev.style.transform = open ? '' : 'rotate(-90deg)';
    });
  });

  // Auto-expand ancestors of the current folder so it is visible.
  (function autoExpand() {
    var cur = currentFolderFromUrl();
    if (!cur) return;
    document.querySelectorAll('.folder-item').forEach(function (item) {
      var path = item.getAttribute('data-folder-path');
      if (path && (cur === path || cur.indexOf(path + '/') === 0)) {
        var children = item.querySelector(':scope > .folder-children');
        if (children && children.classList.contains('hidden')) {
          children.classList.remove('hidden');
          var chev = item.querySelector('.folder-chevron');
          if (chev) chev.style.transform = 'rotate(-90deg)';
        }
      }
    });
  })();

  var addFolderBtn = document.getElementById('add-folder-btn');
  var newFolderInline = document.getElementById('new-folder-inline');
  var newFolderForm = document.getElementById('new-folder-form');
  var newFolderInput = document.getElementById('new-folder-input');
  var newFolderAdd = document.getElementById('new-folder-add');
  if (addFolderBtn) {
    addFolderBtn.addEventListener('click', function () {
      newFolderInline.classList.toggle('hidden');
      if (!newFolderInline.classList.contains('hidden')) newFolderInput.focus();
    });
  }
  if (newFolderInput) {
    newFolderInput.addEventListener('input', function () {
      newFolderAdd.disabled = !newFolderInput.value.trim();
    });
    newFolderInput.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        newFolderInline.classList.add('hidden');
        newFolderInput.value = '';
      }
    });
  }
  if (newFolderForm) {
    newFolderForm.addEventListener('submit', function (e) {
      e.preventDefault();
      var name = newFolderInput.value.trim();
      if (!name) return;
      newFolderAdd.disabled = true;
      fetch('/app/docs/folders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name, parent: currentFolderFromUrl() })
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.ok) window.location.reload();
          else {
            newFolderAdd.disabled = false;
            window.LR.notifyError((data.error && data.error.message) || window.LR.t('Could not create folder.'));
          }
        })
        .catch(function () {
          newFolderAdd.disabled = false;
          window.LR.notifyError(window.LR.t('Network error. Please try again.'), { suffix: false });
        });
    });
  }

  document.querySelectorAll('.folder-rename-btn').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      var path = btn.getAttribute('data-folder-path');
      var name = btn.getAttribute('data-folder-name');
      var newName = window.prompt(window.LR.t('Rename folder'), name);
      if (newName === null) return;
      newName = newName.trim();
      if (!newName || newName === name) return;
      window.LR.setButtonLoading(btn);
      fetch('/app/docs/folders/rename', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: path, name: newName })
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.ok) window.location.reload();
          else window.LR.notifyError((data.error && data.error.message) || window.LR.t('Rename failed.'));
        })
        .catch(function () {
          window.LR.notifyError(window.LR.t('Network error. Please try again.'), { suffix: false });
        })
        .finally(function () { window.LR.clearButtonLoading(btn); });
    });
  });

  var undoBanner = document.getElementById('folder-undo-banner');
  var undoMsg = document.getElementById('folder-undo-msg');
  var undoBtn = document.getElementById('folder-undo-btn');
  function showUndoBanner(msg) {
    if (!undoBanner) return;
    undoMsg.textContent = msg;
    undoBanner.classList.remove('hidden');
  }
  document.querySelectorAll('.folder-delete-btn').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      var path = btn.getAttribute('data-folder-path');
      var name = btn.getAttribute('data-folder-name');
      window.LR.setButtonLoading(btn);
      fetch('/app/docs/folders/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: path })
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.ok) {
            window.location.reload();
            // Reload fires before this line in practice; banner is shown by server if needed.
            showUndoBanner(window.LR.t('Folder "{name}" deleted. Contents moved up.', { name: name }));
          } else {
            window.LR.notifyError((data.error && data.error.message) || window.LR.t('Delete failed.'));
          }
        })
        .catch(function () {
          window.LR.notifyError(window.LR.t('Network error. Please try again.'), { suffix: false });
        })
        .finally(function () { window.LR.clearButtonLoading(btn); });
    });
  });
  if (undoBtn) {
    undoBtn.addEventListener('click', function () {
      undoBtn.disabled = true;
      fetch('/app/docs/folders/delete/undo', { method: 'POST' })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.ok) window.location.reload();
          else {
            undoBtn.disabled = false;
            window.LR.notifyError((data.error && data.error.message) || window.LR.t('Undo failed.'));
          }
        })
        .catch(function () {
          undoBtn.disabled = false;
          window.LR.notifyError(window.LR.t('Network error. Please try again.'), { suffix: false });
        });
    });
  }

  // ---------------------------------------------------------------------
  // Drag-and-drop document move.
  // ---------------------------------------------------------------------
  var dragDocId = null;
  document.querySelectorAll('.doc-row').forEach(function (row) {
    row.addEventListener('dragstart', function () {
      dragDocId = row.getAttribute('data-doc-id');
      row.classList.add('opacity-50');
    });
    row.addEventListener('dragend', function () {
      dragDocId = null;
      row.classList.remove('opacity-50');
      document.querySelectorAll('.folder-drop-target').forEach(function (el) {
        el.classList.remove('folder-drop-target');
      });
    });
  });

  function dropTargetFolder(el) {
    var node = el.closest('[data-folder-path]');
    return node ? node.getAttribute('data-folder-path') : null;
  }

  document.querySelectorAll('.folder-link, .folder-row, #docs-home-link').forEach(function (el) {
    el.addEventListener('dragover', function (e) {
      if (!dragDocId) return;
      e.preventDefault();
      var target = dropTargetFolder(el);
      if (target === null) target = el.getAttribute('data-folder-path');
      el.classList.add('folder-drop-target');
    });
    el.addEventListener('dragleave', function () {
      el.classList.remove('folder-drop-target');
    });
    el.addEventListener('drop', function (e) {
      if (!dragDocId) return;
      e.preventDefault();
      el.classList.remove('folder-drop-target');
      var target = dropTargetFolder(el);
      if (target === null) target = el.getAttribute('data-folder-path');
      moveDoc(dragDocId, target);
    });
  });
  // Home link is a drop target for "move to root".
  var homeLink = document.getElementById('docs-home-link');
  if (homeLink) {
    homeLink.addEventListener('dragover', function (e) {
      if (dragDocId) {
        e.preventDefault();
        homeLink.classList.add('folder-drop-target');
      }
    });
    homeLink.addEventListener('dragleave', function () {
      homeLink.classList.remove('folder-drop-target');
    });
    homeLink.addEventListener('drop', function (e) {
      if (dragDocId) {
        e.preventDefault();
        homeLink.classList.remove('folder-drop-target');
        moveDoc(dragDocId, '');
      }
    });
  }

  function moveDoc(docId, folder) {
    fetch('/app/docs/' + encodeURIComponent(docId) + '/move', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder: folder || '' })
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.ok) window.location.reload();
        else window.LR.notifyError((data.error && data.error.message) || window.LR.t('Move failed.'));
      })
      .catch(function () {
        window.LR.notifyError(window.LR.t('Could not move the document. Please try again.'), { suffix: false });
      });
  }

  // ---------------------------------------------------------------------
  // Convert to Docs (U13.38) from the kebab menu.
  // ---------------------------------------------------------------------
  document.querySelectorAll('.convert-doc-btn').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      if (window.LRDocsCloseMenu) window.LRDocsCloseMenu(btn);
      var docId = btn.dataset.docId;
      var label = btn.querySelector('.convert-label');
      var origText = label ? label.textContent : '';
      btn.disabled = true;
      if (label) label.textContent = window.LR.t('Converting...');
      fetch('/app/docs/' + docId + '/convert', {
        method: 'POST',
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.editor_url) {
            window.open(data.editor_url, '_blank');
            window.location.reload();
          } else {
            btn.disabled = false;
            if (label) label.textContent = origText;
            window.LR.notifyError(data.error || window.LR.t('Conversion failed. Please try again.'), data.error ? undefined : { suffix: false });
          }
        })
        .catch(function () {
          btn.disabled = false;
          if (label) label.textContent = origText;
          window.LR.notifyError(window.LR.t('Conversion failed. Please check your connection or try again.'), { suffix: false });
        });
    });
  });
})();
