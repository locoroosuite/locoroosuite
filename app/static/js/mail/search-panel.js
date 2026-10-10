/**
 * Module-contextual search shell (HLD U7.5, U7.7–U7.11).
 *
 * Generic behavior present on every customer page:
 *  - keeps the desktop and mobile search-box inputs in sync;
 *  - "live" modules (chat, U7.11): dispatches an `lr:search-live`
 *    CustomEvent on the document as the user types and prevents form
 *    submission (filtering happens client-side, no navigation).
 *
 * Advanced-panel behavior (only when a panel is rendered, U7.5):
 *  - toggle open/close, Escape to close, "Clear fields" button;
 *  - mail specifics (grammar prefill, lazy folder options) are driven by
 *    the panel's data-parse-url / data-folders-url attributes so this file
 *    stays module-agnostic. Panels without those attributes just copy the
 *    current query into their "Has the words"/free-text field.
 */
(function () {
  'use strict';

  var boxForms = Array.prototype.slice.call(document.querySelectorAll('[data-mail-search-form]'));
  var boxInputs = boxForms.reduce(function (acc, form) {
    var input = form.querySelector('input[name="q"]');
    if (input) acc.push(input);
    return acc;
  }, []);
  var liveMode = boxForms.some(function (form) {
    return form.hasAttribute('data-search-live');
  });

  function currentQuery() {
    for (var i = 0; i < boxInputs.length; i++) {
      if (boxInputs[i].value && boxInputs[i].value.trim()) {
        return boxInputs[i].value.trim();
      }
    }
    return '';
  }

  // Keep the desktop and mobile search-box inputs in sync; in live mode
  // also broadcast the value so the active module can filter in place.
  boxInputs.forEach(function (input) {
    input.addEventListener('input', function () {
      boxInputs.forEach(function (other) {
        if (other !== input) other.value = input.value;
      });
      if (liveMode) {
        document.dispatchEvent(new CustomEvent('lr:search-live', { detail: input.value }));
      }
    });
  });

  // Live modules never navigate on submit.
  if (liveMode) {
    boxForms.forEach(function (form) {
      form.addEventListener('submit', function (e) {
        e.preventDefault();
      });
    });
  }

  var panel = document.getElementById('search-options-panel');
  if (!panel) return;

  var parseUrl = panel.getAttribute('data-parse-url');
  var foldersUrl = panel.getAttribute('data-folders-url');
  var toggles = Array.prototype.slice.call(document.querySelectorAll('[data-search-options-toggle]'));
  var foldersLoaded = false;

  function field(name) {
    return panel.querySelector('[name="' + name + '"]');
  }

  function accountId() {
    var el = field('account_id');
    return el ? el.value : '';
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
    if (!parseUrl) {
      // Panels without a grammar parser (contacts/calendar/docs) keep the
      // current query in their free-text field; structured fields are
      // echoed server-side from the query string on render.
      setText('q', q);
      return;
    }
    var url = parseUrl + '?q=' + encodeURIComponent(q) + '&account_id=' + encodeURIComponent(accountId());
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
    if (!foldersUrl) return;
    if (foldersLoaded) {
      applyFolderValue();
      return;
    }
    var url = foldersUrl + '?account_id=' + encodeURIComponent(accountId());
    fetch(url, { headers: { Accept: 'application/json' }, credentials: 'same-origin' })
      .then(function (resp) {
        if (!resp.ok) throw new Error('folders failed');
        return resp.json();
      })
      .then(function (data) {
        var sel = field('f_folder');
        if (sel && data && Array.isArray(data.folders)) {
          var labels = data.folder_labels || {};
          var placeholder = sel.querySelector('option[value=""]');
          var html = placeholder ? placeholder.outerHTML : '<option value=""></option>';
          data.folders.forEach(function (name) {
            var label = labels[name] || name;
            html += '<option value="' + escapeHtml(name) + '">' + escapeHtml(label) + '</option>';
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

  function firstPanelField() {
    var names = ['f_from', 'q', 'location', 'email'];
    for (var i = 0; i < names.length; i++) {
      var el = field(names[i]);
      if (el) return el;
    }
    return null;
  }

  function setOpen(willOpen) {
    panel.classList.toggle('hidden', !willOpen);
    toggles.forEach(function (btn) {
      btn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
    });
    if (willOpen) {
      prefill();
      loadFolders();
      var first = firstPanelField();
      if (first) first.focus();
    }
  }

  toggles.forEach(function (btn) {
    btn.addEventListener('click', function () {
      setOpen(panel.classList.contains('hidden'));
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
