(function () {
  "use strict";

  function esc(text) {
    var d = document.createElement("div");
    d.textContent = text == null ? "" : String(text);
    return d.innerHTML;
  }

  function formatBytes(n) {
    if (!n || n < 0) return "0 B";
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / (1024 * 1024)).toFixed(1) + " MB";
  }

  function uuid() {
    try {
      if (window.crypto && typeof window.crypto.randomUUID === "function") {
        return window.crypto.randomUUID();
      }
    } catch (e) {}
    return "xxxxxxxxxxxx4xxxyxxxxxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      var v = c === "x" ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  function extOf(name) {
    var i = (name || "").lastIndexOf(".");
    return i >= 0 ? name.slice(i + 1).toLowerCase() : "";
  }

  function categoryIcon(name) {
    var ext = extOf(name);
    var color = "#64748b";
    var label = ext ? ext.toUpperCase().slice(0, 4) : "FILE";
    if (["pdf"].indexOf(ext) >= 0) color = "#dc2626";
    else if (["doc", "docx", "odt", "rtf", "txt", "md"].indexOf(ext) >= 0) color = "#2563eb";
    else if (["xls", "xlsx", "ods", "csv"].indexOf(ext) >= 0) color = "#16a34a";
    else if (["ppt", "pptx", "odp"].indexOf(ext) >= 0) color = "#ea580c";
    else if (["png", "jpg", "jpeg", "gif", "webp", "svg"].indexOf(ext) >= 0) color = "#7c3aed";
    else if (["zip", "gz", "tar", "7z", "rar"].indexOf(ext) >= 0) color = "#a16207";
    var svg =
      '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">' +
      '<path d="M6 2h7l5 5v15a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1z" fill="' + color + '22"/>' +
      '<path d="M6 2h7l5 5v15a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1z" stroke="' + color + '" stroke-width="1.4"/>' +
      '<path d="M13 2v5a1 1 0 0 0 1 1h4" stroke="' + color + '" stroke-width="1.4" fill="none"/>' +
      "</svg>";
    return {
      svg: svg,
      badge: '<span class="inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-bold tracking-wide" style="color:' + color + ";background:" + color + '15">' + esc(label) + "</span>",
    };
  }

  function el(tag, cls, html) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (html != null) node.innerHTML = html;
    return node;
  }

  function ComposeAttachments(mount, options) {
    this.mount = mount;
    this.opts = options || {};
    this.sessionId = uuid();
    this.used = 0;
    this.items = {}; // id -> {name, size, mime}
    this._build();
  }

  ComposeAttachments.prototype._build = function () {
    var self = this;
    var o = this.opts;

    this.sessionInput = el("input");
    this.sessionInput.type = "hidden";
    this.sessionInput.name = "compose_session_id";
    this.sessionInput.value = this.sessionId;

    this.idsInput = el("input");
    this.idsInput.type = "hidden";
    this.idsInput.name = "attachment_ids";
    this.idsInput.value = "";

    // Dropzone
    var zone = el(
      "div",
      "group relative flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-slate-200 bg-slate-50/60 px-4 py-6 text-center transition-colors hover:border-slate-300 cursor-pointer"
    );
    zone.setAttribute("tabindex", "6");
    zone.setAttribute("role", "button");
    zone.setAttribute("aria-label", "Add attachments");
    zone.innerHTML =
      '<svg viewBox="0 0 24 24" width="26" height="26" fill="none" class="text-slate-400 group-hover:text-slate-500" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">' +
      '<path d="M12 16V4m0 0L8 8m4-4l4 4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>' +
      '<path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>' +
      "</svg>" +
      '<div class="text-sm text-slate-600"><span class="font-medium text-slate-800">Drag &amp; drop files here</span> ' +
      'or <span class="text-blue-600 font-medium underline-offset-2 group-hover:underline">browse</span></div>' +
      '<div class="text-xs text-slate-400">Up to ' +
      formatBytes(o.maxFileBytes) + " per file · " + formatBytes(o.maxTotalBytes) + " total</div>";

    this.zone = zone;

    this.fileInput = el("input");
    this.fileInput.type = "file";
    this.fileInput.multiple = true;
    this.fileInput.className = "hidden";
    this.fileInput.setAttribute("aria-hidden", "true");
    this.fileInput.tabIndex = -1;

    zone.addEventListener("click", function () {
      self.fileInput.click();
    });
    zone.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        self.fileInput.click();
      }
    });
    this.fileInput.addEventListener("change", function () {
      if (self.fileInput.files && self.fileInput.files.length) {
        self.addFiles(self.fileInput.files);
      }
      self.fileInput.value = "";
    });

    ["dragenter", "dragover"].forEach(function (evt) {
      zone.addEventListener(evt, function (e) {
        e.preventDefault();
        e.stopPropagation();
        zone.classList.add("!border-blue-400", "!bg-blue-50/60");
      });
    });
    ["dragleave", "dragend", "drop"].forEach(function (evt) {
      zone.addEventListener(evt, function (e) {
        e.preventDefault();
        e.stopPropagation();
        zone.classList.remove("!border-blue-400", "!bg-blue-50/60");
      });
    });
    zone.addEventListener("drop", function (e) {
      var dt = e.dataTransfer;
      if (dt && dt.files && dt.files.length) {
        self.addFiles(dt.files);
      }
    });

    // Actions row
    var actions = el("div", "mt-2 flex flex-wrap items-center gap-2");
    var browseBtn = el(
      "button",
      "inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:border-slate-300 hover:text-slate-900",
      '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" stroke="currentColor" stroke-width="1.6"/></svg> Browse files'
    );
    browseBtn.type = "button";
    browseBtn.addEventListener("click", function () {
      self.fileInput.click();
    });

    var docsBtn = el(
      "button",
      "inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:border-slate-300 hover:text-slate-900",
      '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M7 3h7l4 4v12a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z" stroke="currentColor" stroke-width="1.6"/><path d="M14 3v4a1 1 0 0 0 1 1h3" stroke="currentColor" stroke-width="1.6"/></svg> From Docs'
    );
    docsBtn.type = "button";
    docsBtn.addEventListener("click", function () {
      self.openDocsPicker();
    });

    actions.appendChild(browseBtn);
    actions.appendChild(docsBtn);

    this.list = el("div", "mt-3 space-y-2");

    var wrapper = el("div");
    wrapper.appendChild(this.sessionInput);
    wrapper.appendChild(this.idsInput);
    wrapper.appendChild(zone);
    wrapper.appendChild(this.fileInput);
    wrapper.appendChild(actions);
    wrapper.appendChild(this.list);

    this.mount.appendChild(wrapper);
  };

  ComposeAttachments.prototype._updateTotal = function () {
    var total = 0;
    Object.keys(this.items).forEach(function (id) {
      total += this.items[id].size;
    }, this);
    this.used = total;
    if (this.opts.totalLabel) {
      var o = this.opts;
      this.opts.totalLabel.textContent = total > 0 ? formatBytes(total) + " / " + formatBytes(o.maxTotalBytes) : "";
    }
  };

  ComposeAttachments.prototype._syncIds = function () {
    this.idsInput.value = Object.keys(this.items).join(",");
  };

  ComposeAttachments.prototype._makeCard = function (name, size, opts) {
    opts = opts || {};
    var ic = categoryIcon(name);
    var card = el(
      "div",
      "flex items-center gap-3 rounded-xl border border-slate-200 bg-white px-3 py-2.5 shadow-sm"
    );

    var iconWrap = el("div", "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-slate-50");
    iconWrap.innerHTML = ic.svg;

    var meta = el("div", "min-w-0 flex-1");
    var nameRow = el("div", "flex items-center gap-2");
    nameRow.appendChild(el("span", "truncate text-sm font-medium text-slate-800", esc(name)));
    nameRow.appendChild((function () {
      var b = el("span");
      b.innerHTML = ic.badge;
      return b;
    })());
    meta.appendChild(nameRow);
    meta.appendChild(el("div", "mt-0.5 text-xs text-slate-400", esc(formatBytes(size))));

    // Progress bar (hidden until uploading)
    var barWrap = el("div", "mt-1.5 hidden h-1.5 w-full overflow-hidden rounded-full bg-slate-100");
    var bar = el("div", "h-full w-0 rounded-full bg-blue-500 transition-[width] duration-150");
    barWrap.appendChild(bar);

    var pct = el("div", "mt-0.5 hidden text-[11px] text-slate-400");

    var status = el("div", "mt-0.5 hidden text-xs");

    meta.appendChild(barWrap);
    meta.appendChild(pct);
    meta.appendChild(status);

    var removeBtn = el(
      "button",
      "ml-1 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-slate-400 hover:bg-slate-100 hover:text-slate-600 disabled:opacity-40",
      '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>'
    );
    removeBtn.type = "button";
    removeBtn.title = "Remove";
    removeBtn.disabled = true;

    card.appendChild(iconWrap);
    card.appendChild(meta);
    card.appendChild(removeBtn);

    return {
      card: card,
      bar: bar,
      barWrap: barWrap,
      pct: pct,
      status: status,
      removeBtn: removeBtn,
    };
  };

  ComposeAttachments.prototype.addFiles = function (fileList) {
    var self = this;
    var arr = Array.prototype.slice.call(fileList);
    arr.forEach(function (file) {
      self._uploadFile(file, { name: file.name, size: file.size, mime: file.type });
    });
  };

  ComposeAttachments.prototype._validate = function (size, name) {
    var o = this.opts;
    if (size <= 0) return "This file is empty.";
    if (size > o.maxFileBytes) {
      return '"' + name + '" is larger than the ' + formatBytes(o.maxFileBytes) + " per-file limit.";
    }
    if (this.used + size > o.maxTotalBytes) {
      return "Adding files would exceed the " + formatBytes(o.maxTotalBytes) + " total limit.";
    }
    return null;
  };

  ComposeAttachments.prototype._uploadFile = function (fileBlob, meta) {
    var self = this;
    var name = meta.name;
    var size = meta.size;

    var err = this._validate(size, name);
    if (err) {
      this._addErrorCard(name, size, err);
      return;
    }

    var ui = this._makeCard(name, size);
    this.list.appendChild(ui.card);

    ui.barWrap.classList.remove("hidden");
    ui.pct.classList.remove("hidden");
    ui.status.classList.remove("hidden");
    ui.status.textContent = "Uploading…";
    ui.status.className = "mt-0.5 text-xs text-slate-400";

    var xhr = new XMLHttpRequest();
    xhr.open("POST", this.opts.stageUrl);
    xhr.upload.onprogress = function (e) {
      if (e.lengthComputable) {
        var p = Math.round((e.loaded / e.total) * 100);
        ui.bar.style.width = p + "%";
        ui.pct.textContent = p + "%";
      }
    };
    xhr.onload = function () {
      var data = null;
      try { data = JSON.parse(xhr.responseText); } catch (e) {}
      if (xhr.status >= 200 && xhr.status < 300 && data && data.id) {
        ui.bar.style.width = "100%";
        ui.barWrap.classList.add("hidden");
        ui.pct.classList.add("hidden");
        ui.status.textContent = "Attached";
        ui.status.className = "mt-0.5 text-xs text-emerald-600";
        ui.removeBtn.disabled = false;
        self.items[data.id] = { name: data.name || name, size: data.size || size, mime: data.mime || meta.mime };
        self._syncIds();
        self._updateTotal();
        self._bindRemove(ui.removeBtn, data.id);
      } else {
        var msg = (data && data.error && data.error.message) || "Upload failed.";
        if (xhr.status === 413 && data && data.error && data.error.limit) {
          msg += " (limit " + formatBytes(data.error.limit) + ")";
        }
        self._failCard(ui, msg);
      }
    };
    xhr.onerror = function () {
      self._failCard(ui, "Network error. Check your connection and retry.");
    };

    var fd = new FormData();
    fd.append("file", fileBlob, name);
    fd.append("compose_session_id", this.sessionId);
    try {
      xhr.send(fd);
    } catch (e) {
      self._failCard(ui, "Unable to start upload.");
    }
  };

  ComposeAttachments.prototype._addErrorCard = function (name, size, message) {
    var ui = this._makeCard(name, size || 0);
    ui.barWrap.classList.add("hidden");
    ui.pct.classList.add("hidden");
    this.list.appendChild(ui.card);
    this._failCard(ui, message);
  };

  ComposeAttachments.prototype._failCard = function (ui, message) {
    ui.barWrap.classList.add("hidden");
    ui.pct.classList.add("hidden");
    ui.status.textContent = message;
    ui.status.className = "mt-0.5 text-xs text-rose-600";
    ui.removeBtn.disabled = false;
    ui.removeBtn.title = "Dismiss";
    var self = this;
    ui.removeBtn.onclick = function () {
      ui.card.remove();
    };
  };

  ComposeAttachments.prototype._bindRemove = function (btn, id) {
    var self = this;
    btn.onclick = function () {
      btn.disabled = true;
      var url = self.opts.deleteUrlBase + "/" + encodeURIComponent(id) + "?compose_session_id=" + encodeURIComponent(self.sessionId);
      fetch(url, { method: "DELETE" }).catch(function () {}).then(function () {
        delete self.items[id];
        self._syncIds();
        self._updateTotal();
        var card = btn.closest(".flex.items-center.gap-3");
        if (card) card.remove();
      });
    };
  };

  /* ---------- Docs picker ---------- */

  ComposeAttachments.prototype.openDocsPicker = function () {
    var self = this;
    if (this._docsOpen) return;
    this._docsOpen = true;

    var overlay = el("div", "fixed inset-0 z-[60] flex items-start justify-center bg-slate-900/40 p-4 backdrop-blur-sm");
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    var modal = el(
      "div",
      "mt-10 w-full max-w-lg overflow-hidden rounded-2xl bg-white shadow-xl ring-1 ring-slate-200"
    );
    var header = el("div", "flex items-center justify-between border-b border-slate-100 px-4 py-3");
    header.appendChild(el("h3", "text-sm font-semibold text-slate-900", "Attach from Docs"));
    var closeBtn = el("button", "inline-flex h-7 w-7 items-center justify-center rounded-md text-slate-400 hover:bg-slate-100 hover:text-slate-600",
      '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>');
    closeBtn.type = "button";
    header.appendChild(closeBtn);

    var searchWrap = el("div", "border-b border-slate-100 px-4 py-2");
    var search = el("input", "w-full rounded-lg border border-slate-200 px-3 py-1.5 text-sm outline-none focus:border-slate-300 focus:ring-2 focus:ring-slate-200");
    search.type = "text";
    search.placeholder = "Search documents…";
    searchWrap.appendChild(search);

    var body = el("div", "max-h-80 overflow-y-auto px-2 py-2 text-sm");
    body.appendChild(el("div", "px-2 py-6 text-center text-slate-400", "Loading…"));

    modal.appendChild(header);
    modal.appendChild(searchWrap);
    modal.appendChild(body);
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    function close() {
      self._docsOpen = false;
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
    }
    closeBtn.addEventListener("click", close);
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) close();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") close();
    });

    var allDocs = [];

    function render(filter) {
      body.innerHTML = "";
      var f = (filter || "").trim().toLowerCase();
      var docs = allDocs.filter(function (d) {
        return !f || (d.name || "").toLowerCase().indexOf(f) >= 0;
      });
      if (!docs.length) {
        body.appendChild(el("div", "px-2 py-6 text-center text-slate-400", allDocs.length ? "No matching documents." : "No documents available."));
        return;
      }
      docs.forEach(function (doc) {
        var filename = doc.name + "." + (doc.ext || doc.doc_type || "odt");
        var ic = categoryIcon(filename);
        var row = el("div", "flex cursor-pointer items-center gap-3 rounded-lg px-2 py-2 hover:bg-slate-50");
        var iconWrap = el("div", "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-slate-50");
        iconWrap.innerHTML = ic.svg;
        var meta = el("div", "min-w-0 flex-1");
        var nameRow = el("div", "flex items-center gap-2");
        nameRow.appendChild(el("span", "truncate text-sm font-medium text-slate-800", esc(doc.name || "Untitled")));
        var badge = el("span"); badge.innerHTML = ic.badge; nameRow.appendChild(badge);
        meta.appendChild(nameRow);
        meta.appendChild(el("div", "mt-0.5 text-xs text-slate-400", esc(formatBytes(doc.file_size))));
        row.appendChild(iconWrap);
        row.appendChild(meta);
        row.addEventListener("click", function () {
          close();
          self._attachDoc(doc);
        });
        body.appendChild(row);
      });
    }

    search.addEventListener("input", function () {
      render(search.value);
    });

    var url = this.opts.docsListUrl + (this.opts.docsListUrl.indexOf("?") >= 0 ? "&" : "?") + "account_id=" + encodeURIComponent(this.opts.accountId);
    fetch(url, { headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (json) {
        allDocs = (json && json.documents) || [];
        render("");
      })
      .catch(function () {
        body.innerHTML = "";
        body.appendChild(el("div", "px-2 py-6 text-center text-rose-600", "Could not load documents. Retry from the Docs button."));
      });
  };

  ComposeAttachments.prototype._attachDoc = function (doc) {
    var self = this;
    var filename = (doc.name || "document") + "." + (doc.ext || doc.doc_type || "odt");
    var size = doc.file_size || 0;

    var ui = this._makeCard(filename, size);
    this.list.appendChild(ui.card);
    ui.barWrap.classList.remove("hidden");
    ui.pct.classList.remove("hidden");
    ui.status.classList.remove("hidden");
    ui.status.textContent = "Fetching from Docs…";
    ui.status.className = "mt-0.5 text-xs text-slate-400";

    var err = this._validate(size, filename);
    if (err) {
      this._failCard(ui, err);
      return;
    }

    var downloadUrl = this.opts.docsDownloadUrl.replace("__DOCID__", encodeURIComponent(doc.id)) + "?account_id=" + encodeURIComponent(this.opts.accountId);

    fetch(downloadUrl)
      .then(function (resp) {
        if (!resp.ok) throw new Error("download failed");
        return resp.blob();
      })
      .then(function (blob) {
        ui.barWrap.classList.add("hidden");
        ui.pct.classList.add("hidden");
        var file = new File([blob], filename, { type: blob.type || "application/octet-stream" });
        // Replace the placeholder card before uploading for real progress.
        ui.card.remove();
        self._uploadFile(file, { name: filename, size: blob.size, mime: blob.type });
      })
      .catch(function () {
        self._failCard(ui, "Could not load the document from Docs. Retry.");
      });
  };

  window.ComposeAttachments = ComposeAttachments;
})();
