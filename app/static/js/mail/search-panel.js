/**
 * "Show search options" panel (HLD U7.5).
 *
 * The toggle lives inside the global search box (desktop + mobile); the
 * panel slides open under the header on every customer page. Opening it
 * parses the query currently in the box via GET /mail/search/parse (the
 * Python grammar is the single source of truth — never duplicated here)
 * and pre-fills the matching fields. Folder options are fetched lazily
 * from /mail/search/folders the first time the panel opens.
 */
(function () {
  'use strict';

  var PARSE_URL = '/app/mail/search/parse';
  var FOLDERS_URL = '/app/mail/search/folders';

  var panel = document.getElementById('search-options-panel');
  if (!panel) return;
  var toggles = Array.prototype.slice.call(document.querySelectorAll('[data-search-options-toggle]'));
  var boxInputs = Array.prototype.slice.call(
    document.querySelectorAll('[data-mail-search-form] input[name="q"]')
  );
  var foldersLoaded = false;

  function field(name) {
    return panel.querySelector('[name="' + name + '"]');
  }

  function accountId() {
    var el = field('account_id');
    return el ? el.value : '';
  }

  function currentQuery() {
    for (var i = 0; i < boxInputs.length; i++) {
      if (boxInputs[i].value && boxInputs[i].value.trim()) {
        return boxInputs[i].value.trim();
      }
    }
    return '';
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  function setText(name, value) {
    var el = field(name);
    if (el) el.value = value || '';
  }

  function setChecked(name, value) {
    var el = field(name);
    if (el) el.checked = !!value;
  }

  function applyFolderValue() {
    var sel = field('f_folder');
    if (!sel) return;
    sel.value = sel.dataset.pendingValue || '';
  }

  function fillFields(fields) {
    setText('f_from', fields.f_from);
    setText('f_to', fields.f_to);
    setText('f_subject', fields.f_subject);
    setText('f_filename', fields.f_filename);
    setText('f_after', fields.f_after);
    setText('f_before', fields.f_before);
    setText('q', fields.q);
    setChecked('f_attachment', fields.f_attachment);
    setChecked('f_unread', fields.f_unread);
    setChecked('f_starred', fields.f_starred);
    var sel = field('f_folder');
    if (sel) sel.dataset.pendingValue = fields.f_folder || '';
    applyFolderValue();
  }

  function prefill() {
    var q = currentQuery();
    var url = PARSE_URL + '?q=' + encodeURIComponent(q) + '&account_id=' + encodeURIComponent(accountId());
    fetch(url, { headers: { Accept: 'application/json' }, credentials: 'same-origin' })
      .then(function (resp) {
        if (!resp.ok) throw new Error('parse failed');
        return resp.json();
      })
      .then(function (data) {
        if (data && data.fields) fillFields(data.fields);
      })
      .catch(function () {
        // Parsing unavailable (e.g. offline): never lose the user's text.
        setText('q', q);
      });
  }

  function loadFolders() {
    if (foldersLoaded) {
      applyFolderValue();
      return;
    }
    var url = FOLDERS_URL + '?account_id=' + encodeURIComponent(accountId());
    fetch(url, { headers: { Accept: 'application/json' }, credentials: 'same-origin' })
      .then(function (resp) {
        if (!resp.ok) throw new Error('folders failed');
        return resp.json();
      })
      .then(function (data) {
        var sel = field('f_folder');
        if (sel && data && Array.isArray(data.folders)) {
          var placeholder = sel.querySelector('option[value=""]');
          var html = placeholder ? placeholder.outerHTML : '<option value=""></option>';
          data.folders.forEach(function (name) {
            html += '<option value="' + escapeHtml(name) + '">' + escapeHtml(name) + '</option>';
          });
          sel.innerHTML = html;
        }
        foldersLoaded = true;
        applyFolderValue();
      })
      .catch(function () {
        // Folder list unavailable: the select keeps its placeholder; the
        // user can still scope via the main box's folder: operator.
        if (window.console && window.console.warn) {
          window.console.warn('search panel: folder options unavailable');
        }
      });
  }

  function setOpen(willOpen) {
    panel.classList.toggle('hidden', !willOpen);
    toggles.forEach(function (btn) {
      btn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
    });
    if (willOpen) {
      prefill();
      loadFolders();
      var first = field('f_from');
      if (first) first.focus();
    }
  }

  toggles.forEach(function (btn) {
    btn.addEventListener('click', function () {
      setOpen(panel.classList.contains('hidden'));
    });
  });

  // Keep the desktop and mobile search-box inputs in sync.
  boxInputs.forEach(function (input) {
    input.addEventListener('input', function () {
      boxInputs.forEach(function (other) {
        if (other !== input) other.value = input.value;
      });
    });
  });

  var clearBtn = panel.querySelector('[data-search-panel-clear]');
  if (clearBtn) {
    clearBtn.addEventListener('click', function () {
      panel.reset();
      var sel = field('f_folder');
      if (sel) delete sel.dataset.pendingValue;
    });
  }

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !panel.classList.contains('hidden')) {
      setOpen(false);
    }
  });
})();
