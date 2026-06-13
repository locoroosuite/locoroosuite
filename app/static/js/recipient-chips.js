(function (root) {
  function parseRfc5322(text) {
    if (!text || !text.trim()) return [];
    var results = [];
    var parts = text.split(/[,;]/);
    for (var p = 0; p < parts.length; p++) {
      var part = parts[p].trim();
      if (!part) continue;
      if (isValidEmail(part)) {
        results.push({ name: '', email: part });
        continue;
      }
      var re = /(?:\"?([^\"<@\n]+)\"?\s*)?<?\s*([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\s*>?/g;
      var m;
      while ((m = re.exec(part)) !== null) {
        var name = (m[1] || '').trim();
        results.push({ name: name, email: m[2] });
      }
    }
    return results;
  }

  function isValidEmail(v) {
    if (!/^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$/.test(v)) return false;
    var domain = v.split('@')[1];
    return domain.indexOf('..') === -1;
  }

  function escHtml(t) {
    var d = document.createElement('div');
    d.textContent = t;
    return d.innerHTML;
  }

  function copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    return Promise.resolve();
  }

  function serializeRfc5322(chipsData) {
    var parts = [];
    for (var i = 0; i < chipsData.length; i++) {
      var c = chipsData[i];
      parts.push(c.name ? '"' + c.name + '" <' + c.email + '>' : c.email);
    }
    return parts.join(', ');
  }

  function RecipientChips(fieldName, options) {
    this.fieldName = fieldName;
    this.options = Object.assign({
      searchUrl: '',
      parseInitial: function (value) { return parseRfc5322(value); },
      serializeChips: serializeRfc5322,
      defaultExtra: {}
    }, options);

    this.hiddenInput = document.getElementById(fieldName + '-hidden');
    this.container = document.querySelector('[data-chips-container="' + fieldName + '"]');
    this.dropdown = document.querySelector('[data-dropdown="' + fieldName + '"]');
    this.input = this.container.querySelector('[data-chip-input]');
    this.chipsData = [];
    this.debounceTimer = null;
    this.selectedIdx = -1;
    this._parseInitial();
    this._bindEvents();
  }

  RecipientChips.prototype._parseInitial = function () {
    var val = this.hiddenInput.value;
    if (!val) return;
    var list = this.options.parseInitial(val);
    for (var i = 0; i < list.length; i++) {
      this.addChip(list[i].name, list[i].email, list[i].extra || {}, false);
    }
  };

  RecipientChips.prototype._bindEvents = function () {
    var self = this;
    this.input.addEventListener('input', function () { self._onInput(); });
    this.input.addEventListener('keydown', function (e) { self._onKeydown(e); });
    this.input.addEventListener('paste', function (e) { self._onPaste(e); });
    this.container.addEventListener('click', function (e) {
      if (e.target === self.container) self.input.focus();
    });
    document.addEventListener('click', function (e) {
      if (!self.container.contains(e.target) && !self.dropdown.contains(e.target)) {
        self._hideDropdown();
      }
    });
  };

  RecipientChips.prototype.addChip = function (name, email, extra, animate) {
    email = (email || '').trim().toLowerCase();
    if (!email) return false;
    if (!isValidEmail(email)) {
      this._showInvalidFlash();
      return false;
    }
    for (var i = 0; i < this.chipsData.length; i++) {
      if (this.chipsData[i].email === email) return false;
    }

    var displayName = name || email.split('@')[0];
    var chip = document.createElement('span');
    chip.className = 'inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-700 hover:bg-slate-200 cursor-pointer transition-colors group relative';
    chip.title = 'Click to copy ' + email;
    chip.setAttribute('data-email', email);

    chip.innerHTML =
      '<span class="max-w-[200px] truncate">' + escHtml(displayName) + '</span>' +
      '<button type="button" class="ml-0.5 rounded-full p-0.5 hover:bg-slate-300 opacity-0 group-hover:opacity-100 transition-opacity text-slate-500 hover:text-slate-700 shrink-0" data-remove-chip title="Remove">' +
        '<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>' +
      '</button>';

    var self = this;
    chip.addEventListener('click', function (e) {
      if (e.target.closest('[data-remove-chip]')) return;
      copyToClipboard(email).then(function () { self._showCopied(chip, email); });
    });

    chip.querySelector('[data-remove-chip]').addEventListener('click', function (e) {
      e.stopPropagation();
      var data = null;
      for (var j = 0; j < self.chipsData.length; j++) {
        if (self.chipsData[j].element === chip) { data = self.chipsData[j]; break; }
      }
      if (data) self.removeChip(data);
    });

    if (animate) {
      chip.style.opacity = '0';
      chip.style.transform = 'translateY(4px)';
    }

    this.container.insertBefore(chip, this.input);

    if (animate) {
      (function (c) {
        requestAnimationFrame(function () {
          c.style.transition = 'opacity 150ms, transform 150ms';
          c.style.opacity = '1';
          c.style.transform = 'translateY(0)';
        });
      })(chip);
    }

    this.chipsData.push({ name: name, email: email, extra: extra || {}, element: chip });
    this._syncHidden();
    return true;
  };

  RecipientChips.prototype.removeChip = function (chipData) {
    chipData.element.remove();
    var idx = this.chipsData.indexOf(chipData);
    if (idx !== -1) this.chipsData.splice(idx, 1);
    this._syncHidden();
  };

  RecipientChips.prototype._syncHidden = function () {
    this.hiddenInput.value = this.options.serializeChips(this.chipsData);
  };

  RecipientChips.prototype._onInput = function () {
    var self = this;
    var q = this.input.value.trim();
    if (this.debounceTimer) clearTimeout(this.debounceTimer);
    if (q.length < 2 || !this.options.searchUrl) { this._hideDropdown(); return; }
    this.debounceTimer = setTimeout(function () { self._search(q); }, 200);
  };

  RecipientChips.prototype._onKeydown = function (e) {
    if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); this._addFromInput(); return; }
    if (e.key === 'Tab' && this.input.value.trim()) { e.preventDefault(); this._addFromInput(); return; }
    if (e.key === 'Backspace' && !this.input.value && this.chipsData.length > 0) {
      this.removeChip(this.chipsData[this.chipsData.length - 1]); return;
    }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault(); this._navDropdown(e.key === 'ArrowDown' ? 1 : -1); return;
    }
    if (e.key === 'Escape') { this._hideDropdown(); }
  };

  RecipientChips.prototype._onPaste = function (e) {
    e.preventDefault();
    var text = (e.clipboardData || window.clipboardData).getData('text');
    var list = parseRfc5322(text);
    if (list.length > 0) {
      for (var i = 0; i < list.length; i++) this.addChip(list[i].name, list[i].email, this.options.defaultExtra, true);
      this.input.value = '';
    } else {
      this.input.value = text;
    }
  };

  RecipientChips.prototype._search = function (q) {
    var self = this;
    fetch(this.options.searchUrl + '?q=' + encodeURIComponent(q))
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (results) { self._showDropdown(results); })
      .catch(function () {});
  };

  RecipientChips.prototype._showDropdown = function (results) {
    var self = this;
    if (!results || results.length === 0) { this._hideDropdown(); return; }
    this.selectedIdx = -1;
    this.dropdown.innerHTML = '';

    for (var i = 0; i < results.length; i++) {
      (function (result, idx) {
        var initials = (result.fn || '?').split(' ').map(function (w) { return w[0]; }).join('').substring(0, 2).toUpperCase();
        var emailStr = (result.emails || []).map(function (e) { return e.email; }).join(', ');
        var item = document.createElement('div');
        item.className = 'px-3 py-2 hover:bg-slate-50 cursor-pointer flex items-center gap-3 text-sm';
        item.setAttribute('data-index', idx);
        item.innerHTML =
          '<div class="h-8 w-8 rounded-full bg-indigo-50 flex items-center justify-center text-xs font-medium text-indigo-600 shrink-0">' + escHtml(initials) + '</div>' +
          '<div class="min-w-0 flex-1">' +
            '<div class="font-medium text-slate-900 truncate">' + escHtml(result.fn || 'Unknown') + '</div>' +
            '<div class="text-xs text-slate-500 truncate">' + escHtml(emailStr) + '</div>' +
          '</div>';
        item.addEventListener('mousedown', function (e) {
          e.preventDefault();
          self._selectResult(result);
        });
        self.dropdown.appendChild(item);
      })(results[i], i);
    }
    this.dropdown.classList.remove('hidden');
  };

  RecipientChips.prototype._hideDropdown = function () {
    this.dropdown.classList.add('hidden');
    this.dropdown.innerHTML = '';
    this.selectedIdx = -1;
  };

  RecipientChips.prototype._navDropdown = function (dir) {
    var items = this.dropdown.querySelectorAll('[data-index]');
    if (!items.length) return;
    this.selectedIdx += dir;
    if (this.selectedIdx < 0) this.selectedIdx = items.length - 1;
    if (this.selectedIdx >= items.length) this.selectedIdx = 0;
    for (var i = 0; i < items.length; i++) {
      items[i].classList.toggle('bg-slate-50', i === this.selectedIdx);
    }
  };

  RecipientChips.prototype._selectResult = function (result) {
    if (result.emails && result.emails.length > 0) {
      var email = (result.emails[0].email || '').trim();
      if (!isValidEmail(email)) {
        this._showInvalidFlash();
        this.input.value = '';
        this._hideDropdown();
        this.input.focus();
        return;
      }
      this.addChip(result.fn || '', email, this.options.defaultExtra, true);
    }
    this.input.value = '';
    this._hideDropdown();
    this.input.focus();
  };

  RecipientChips.prototype._addFromInput = function () {
    var text = this.input.value.trim().replace(/,$/, '');
    if (!text) return;
    var list = parseRfc5322(text);
    if (list.length > 0) {
      for (var i = 0; i < list.length; i++) this.addChip(list[i].name, list[i].email, this.options.defaultExtra, true);
    } else {
      this._showInvalidFlash();
    }
    this.input.value = '';
    this._hideDropdown();
  };

  RecipientChips.prototype._showCopied = function (chip, email) {
    var tip = document.createElement('div');
    tip.className = 'absolute -top-7 left-1/2 -translate-x-1/2 bg-slate-800 text-white text-xs rounded px-2 py-1 whitespace-nowrap z-50 pointer-events-none';
    tip.textContent = 'Copied ' + email;
    chip.style.position = 'relative';
    chip.appendChild(tip);
    setTimeout(function () {
      tip.style.transition = 'opacity 300ms';
      tip.style.opacity = '0';
      setTimeout(function () { tip.remove(); }, 300);
    }, 1200);
  };

  RecipientChips.prototype._showInvalidFlash = function () {
    var self = this;
    this.input.classList.add('!border-rose-400', '!ring-rose-200');
    this.input.placeholder = 'Invalid email address';
    setTimeout(function () {
      self.input.classList.remove('!border-rose-400', '!ring-rose-200');
      self.input.placeholder = '';
    }, 2000);
  };

  RecipientChips.prototype.getRecipients = function () {
    return this.chipsData.map(function (c) { return { name: c.name, email: c.email }; });
  };

  RecipientChips.prototype.getChips = function () {
    return this.chipsData.map(function (c) {
      return Object.assign({ name: c.name, email: c.email }, c.extra || {});
    });
  };

  RecipientChips.prototype.focus = function () { this.input.focus(); };

  RecipientChips.prototype.setTabindex = function (idx) { this.input.setAttribute('tabindex', idx); };

  root.RecipientChips = RecipientChips;
  root.parseRfc5322 = parseRfc5322;
  root.isValidEmail = isValidEmail;
  root.escHtml = escHtml;
})(window);
