/**
 * Docs share + tag modals (HLD U13.60n/U13.60o, U13.91).
 *
 * Opened from the per-item kebab menu. Pure client-side fetch — no
 * page navigation.
 */
(function () {
  'use strict';

  var shareModal = document.getElementById('share-modal');
  if (!shareModal) return;

  var shareOverlay = document.getElementById('share-overlay');
  var shareClose = document.getElementById('share-modal-close');
  var shareEmailsInput = document.getElementById('share-emails-input');
  var sharePermSelect = document.getElementById('share-permission-select');
  var shareAddBtn = document.getElementById('share-add-btn');
  var shareError = document.getElementById('share-error');
  var shareSuccess = document.getElementById('share-success');
  var shareListLoading = document.getElementById('share-list-loading');
  var shareListEmpty = document.getElementById('share-list-empty');
  var shareList = document.getElementById('share-list');
  var shareModalTitle = document.getElementById('share-modal-title');
  var shareAutocomplete = document.getElementById('share-autocomplete');

  var currentShareDocId = null;

  function esc(t) {
    var d = document.createElement('div');
    d.textContent = t;
    return d.innerHTML;
  }

  function openShareModal(docId, docName) {
    currentShareDocId = docId;
    shareModalTitle.textContent = window.LR.t('Share: {name}', { name: docName });
    shareEmailsInput.value = '';
    shareError.classList.add('hidden');
    shareSuccess.classList.add('hidden');
    shareAddBtn.disabled = true;
    shareModal.classList.remove('hidden');
    shareEmailsInput.focus();
    loadShareList(docId);
  }

  function closeShareModal() {
    shareModal.classList.add('hidden');
    currentShareDocId = null;
  }

  shareOverlay.addEventListener('click', closeShareModal);
  shareClose.addEventListener('click', closeShareModal);
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !shareModal.classList.contains('hidden')) closeShareModal();
  });

  var acTimer = null;

  shareEmailsInput.addEventListener('input', function () {
    shareAddBtn.disabled = !shareEmailsInput.value.trim();

    clearTimeout(acTimer);
    var parts = shareEmailsInput.value.split(',');
    var lastPart = parts[parts.length - 1].trim();
    if (lastPart.length >= 2) {
      acTimer = setTimeout(function () {
        fetch('/app/contacts/api/search?q=' + encodeURIComponent(lastPart))
          .then(function (r) { return r.json(); })
          .then(function (results) {
            shareAutocomplete.innerHTML = '';
            if (!results.length) { shareAutocomplete.classList.add('hidden'); return; }
            results.forEach(function (r) {
              var emails = (r.emails || []).map(function (e) { return e.email; }).filter(Boolean);
              if (!emails.length) return;
              emails.forEach(function (email) {
                var item = document.createElement('div');
                item.className = 'px-3 py-2 cursor-pointer text-sm text-slate-700 hover:bg-slate-50 flex items-center gap-2 border-b border-slate-50 last:border-0';
                item.innerHTML = '<span class="font-medium">' + esc(r.fn || email) + '</span><span class="text-xs text-slate-400">&lt;' + esc(email) + '&gt;</span>';
                item.addEventListener('click', function () {
                  parts[parts.length - 1] = ' ' + email;
                  shareEmailsInput.value = parts.join(',') + ', ';
                  shareAutocomplete.classList.add('hidden');
                  shareEmailsInput.focus();
                  shareEmailsInput.dispatchEvent(new Event('input'));
                });
                shareAutocomplete.appendChild(item);
              });
            });
            shareAutocomplete.classList.remove('hidden');
          })
          .catch(function () { shareAutocomplete.classList.add('hidden'); });
      }, 300);
    } else {
      shareAutocomplete.classList.add('hidden');
    }
  });

  document.addEventListener('click', function (e) {
    if (!shareAutocomplete.contains(e.target) && e.target !== shareEmailsInput) {
      shareAutocomplete.classList.add('hidden');
    }
  });

  shareAddBtn.addEventListener('click', function () {
    if (!currentShareDocId || !shareEmailsInput.value.trim()) return;
    shareAddBtn.disabled = true;
    shareError.classList.add('hidden');
    shareSuccess.classList.add('hidden');

    fetch('/app/docs/' + currentShareDocId + '/shares', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        emails: shareEmailsInput.value.trim(),
        permission: sharePermSelect.value,
        send_invite: true
      })
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        shareAddBtn.disabled = false;
        if (data.shares && data.shares.length > 0) {
          shareEmailsInput.value = '';
          shareSuccess.textContent = window.LR.t('Shared with {emails}', {
            emails: data.shares.map(function (s) { return s.recipient_email; }).join(', ')
          });
          shareSuccess.classList.remove('hidden');
          setTimeout(function () { shareSuccess.classList.add('hidden'); }, 4000);
          loadShareList(currentShareDocId);
        } else if (data.error) {
          shareError.textContent = data.error;
          shareError.classList.remove('hidden');
        }
      })
      .catch(function () {
        shareAddBtn.disabled = false;
        shareError.textContent = window.LR.t('Failed to share. Please try again.');
        shareError.classList.remove('hidden');
      });
  });

  function loadShareList(docId) {
    shareListLoading.classList.remove('hidden');
    shareListEmpty.classList.add('hidden');
    shareList.innerHTML = '';

    fetch('/app/docs/' + docId + '/shares')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        shareListLoading.classList.add('hidden');
        var shares = data.shares || [];
        if (shares.length === 0) {
          shareListEmpty.classList.remove('hidden');
          return;
        }
        shares.forEach(function (s) {
          var row = document.createElement('div');
          row.className = 'flex items-center justify-between py-2 px-1';
          row.innerHTML = `
            <div class="flex items-center gap-2 min-w-0">
              <div class="h-7 w-7 rounded-full bg-slate-100 flex items-center justify-center flex-shrink-0 text-xs font-medium text-slate-500">${esc(s.recipient_email.charAt(0).toUpperCase())}</div>
              <div class="min-w-0">
                <p class="text-sm text-slate-900 truncate">${esc(s.recipient_email)}</p>
                <p class="text-xs text-slate-400">${s.permission === 'write' ? window.LR.t('Can edit') : window.LR.t('Can view')} &middot; ${(s.view_count || 0) === 1 ? window.LR.t('{n} view', { n: s.view_count || 0 }) : window.LR.t('{n} views', { n: s.view_count || 0 })}${s.last_accessed_at ? ' &middot; ' + window.LR.t('Last:') + ' ' + (window.LRDocsRelativeTime ? window.LRDocsRelativeTime(s.last_accessed_at) : '') : ''}</p>
              </div>
            </div>
            <button type="button" class="revoke-share-btn text-xs text-slate-400 hover:text-rose-600 transition-colors px-2 py-1 rounded hover:bg-rose-50" data-share-id="${s.id}">${window.LR.t('Revoke')}</button>
          `;
          shareList.appendChild(row);
        });

        shareList.querySelectorAll('.revoke-share-btn').forEach(function (btn) {
          btn.addEventListener('click', function () {
            var shareId = btn.dataset.shareId;
            btn.disabled = true;
            btn.textContent = window.LR.t('Revoking...');
            fetch('/app/docs/' + currentShareDocId + '/shares/' + shareId, { method: 'DELETE' })
              .then(function (r) { return r.json(); })
              .then(function () { loadShareList(currentShareDocId); })
              .catch(function () {
                btn.disabled = false;
                btn.textContent = window.LR.t('Revoke');
              });
          });
        });
      })
      .catch(function () {
        shareListLoading.classList.add('hidden');
        shareError.textContent = window.LR.t('Failed to load shares.');
        shareError.classList.remove('hidden');
      });
  }

  document.querySelectorAll('.share-doc-btn').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      if (window.LRDocsCloseMenu) window.LRDocsCloseMenu(btn);
      openShareModal(btn.dataset.docId, btn.dataset.docName);
    });
  });

  // ---------------------------------------------------------------------
  // Tags modal (U13.91).
  // ---------------------------------------------------------------------
  var tagModal = document.getElementById('tag-modal');
  var tagOverlay = document.getElementById('tag-overlay');
  var tagClose = document.getElementById('tag-modal-close');
  var tagInput = document.getElementById('tag-input');
  var tagAddBtn = document.getElementById('tag-add-btn');
  var tagCurrent = document.getElementById('tag-current');
  var tagDocName = document.getElementById('tag-modal-doc-name');
  var currentTagDocId = null;

  function esc2(t) {
    var d = document.createElement('div');
    d.textContent = t;
    return d.innerHTML;
  }

  function renderTagCurrent(tags) {
    tagCurrent.innerHTML = '';
    if (!tags.length) {
      var empty = document.createElement('span');
      empty.className = 'text-xs text-slate-400';
      empty.textContent = window.LR.t('No tags yet.');
      tagCurrent.appendChild(empty);
      return;
    }
    tags.forEach(function (t) {
      var chip = document.createElement('span');
      chip.className = 'inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-slate-100 text-slate-700';
      chip.innerHTML = '#' + esc2(t) + ' <button type="button" class="text-slate-400 hover:text-rose-600" title="' + window.LR.t('Remove') + '">&times;</button>';
      chip.querySelector('button').addEventListener('click', function () {
        fetch('/app/docs/' + encodeURIComponent(currentTagDocId) + '/tags', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ remove: [t] })
        })
          .then(function (r) { return r.json(); })
          .then(function (d) { if (d.ok) renderTagCurrent(d.tags); });
      });
      tagCurrent.appendChild(chip);
    });
  }

  function loadTags(docId) {
    fetch('/app/docs/' + encodeURIComponent(docId) + '/tags', { method: 'GET' })
      .then(function (r) { return r.json(); })
      .then(function (d) { renderTagCurrent(d.tags || []); })
      .catch(function () { renderTagCurrent([]); });
  }

  function addTag(value) {
    var t = (value || '').trim();
    if (!t) return;
    tagAddBtn.disabled = true;
    fetch('/app/docs/' + encodeURIComponent(currentTagDocId) + '/tags', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ add: [t] })
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        tagAddBtn.disabled = false;
        tagInput.value = '';
        if (d.ok) renderTagCurrent(d.tags);
        else window.LR.notifyError((d.error && d.error.message) || window.LR.t('Could not add tag.'));
      })
      .catch(function () {
        tagAddBtn.disabled = false;
        window.LR.notifyError(window.LR.t('Network error. Please try again.'), { suffix: false });
      });
  }

  function openTagModal(docId, docName) {
    currentTagDocId = docId;
    tagDocName.textContent = docName || '';
    tagInput.value = '';
    tagModal.classList.remove('hidden');
    loadTags(docId);
    setTimeout(function () { tagInput.focus(); }, 50);
  }

  function closeTagModal() {
    tagModal.classList.add('hidden');
    currentTagDocId = null;
  }

  if (tagOverlay) tagOverlay.addEventListener('click', closeTagModal);
  if (tagClose) tagClose.addEventListener('click', closeTagModal);
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && tagModal && !tagModal.classList.contains('hidden')) closeTagModal();
  });
  if (tagAddBtn) tagAddBtn.addEventListener('click', function () { addTag(tagInput.value); });
  if (tagInput) {
    tagInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        addTag(tagInput.value);
      }
    });
  }

  document.querySelectorAll('.tag-doc-btn').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      if (window.LRDocsCloseMenu) window.LRDocsCloseMenu(btn);
      openTagModal(btn.getAttribute('data-doc-id'), btn.getAttribute('data-doc-name'));
    });
  });

  document.querySelectorAll('.tag-suggest-btn').forEach(function (btn) {
    btn.addEventListener('click', function () { addTag(btn.getAttribute('data-tag')); });
  });
})();
