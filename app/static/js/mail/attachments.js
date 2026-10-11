// Attachment interactions (U8.8a-U8.8d): chip previews, overlay viewer, and
// gesture-safe "Open in Docs".
//
// Wiring is done with document-level event delegation on purpose: the
// message preview pane is injected via innerHTML, which never executes
// inline <script> tags — per-element listeners wired at load time are dead
// there. Delegation survives DOM injection.
(function () {
  "use strict";

  function t(key, fallback) {
    if (window.LR && typeof window.LR.t === "function") return window.LR.t(key);
    return fallback || key;
  }

  function notifyError(message) {
    if (window.LR && typeof window.LR.notifyError === "function") {
      window.LR.notifyError(message);
      return;
    }
    alert(message);
  }

  var SPINNER_ICON =
    '<svg class="h-4 w-4 animate-spin" viewBox="0 0 20 20" fill="currentColor"><path d="M10 3a7 7 0 017 7h-3a4 4 0 00-4-4V3z"/></svg>';
  var DOWNLOAD_ICON =
    '<svg class="h-4 w-4" viewBox="0 0 20 20" fill="currentColor"><path d="M10.75 2.75a.75.75 0 00-1.5 0v8.614L6.295 8.235a.75.75 0 10-1.09 1.03l4.25 4.5a.75.75 0 001.09 0l4.25-4.5a.75.75 0 00-1.09-1.03l-2.955 3.129V2.75z"/><path d="M3.5 12.75a.75.75 0 00-1.5 0v2.5A2.75 2.75 0 004.75 18h10.5A2.75 2.75 0 0018 15.25v-2.5a.75.75 0 00-1.5 0v2.5c0 .69-.56 1.25-1.25 1.25H4.75c-.69 0-1.25-.56-1.25-1.25v-2.5z"/></svg>';
  var CLOSE_ICON =
    '<svg class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z"/></svg>';

  // ------------------------------------------------------------------ overlay

  var overlayEl = null;

  function buildOverlay() {
    var root = document.createElement("div");
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.className = "fixed inset-0 z-[70] hidden";
    root.innerHTML =
      '<div class="absolute inset-0 bg-slate-900/60" data-att-backdrop></div>' +
      '<div class="absolute inset-x-0 bottom-0 flex max-h-[92vh] w-full flex-col rounded-t-2xl bg-white shadow-2xl md:inset-0 md:m-auto md:h-[86vh] md:max-h-[92vh] md:max-w-5xl md:rounded-2xl">' +
      '  <div class="flex items-center gap-2 border-b border-slate-200 px-3 py-2.5 md:px-4 md:py-3">' +
      '    <div class="min-w-0 flex-1 truncate text-sm font-semibold text-slate-900" data-att-title></div>' +
      '    <a data-att-download class="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-600 transition hover:border-slate-300 hover:bg-slate-50 hover:text-slate-900">' +
      DOWNLOAD_ICON +
      '      <span data-att-download-label>' + t("Download", "Download") + "</span>" +
      "    </a>" +
      '    <button type="button" data-att-close class="rounded-lg p-2 text-slate-400 transition hover:bg-slate-100 hover:text-slate-600" aria-label="' + t("Close", "Close") + '">' +
      CLOSE_ICON +
      "    </button>" +
      "  </div>" +
      '  <div class="flex-1 overflow-auto bg-slate-100 p-2 md:p-4" data-att-body></div>' +
      "</div>";
    root.addEventListener("click", function (e) {
      if (e.target.closest("[data-att-close]") || e.target.closest("[data-att-backdrop]")) {
        closeOverlay();
      }
    });
    return root;
  }

  function ensureOverlay() {
    if (overlayEl && document.body.contains(overlayEl)) return overlayEl;
    overlayEl = buildOverlay();
    document.body.appendChild(overlayEl);
    return overlayEl;
  }

  function openOverlay(filename, downloadUrl) {
    var root = ensureOverlay();
    root.querySelector("[data-att-title]").textContent = filename || "";
    var download = root.querySelector("[data-att-download]");
    if (downloadUrl) {
      download.href = downloadUrl;
      download.classList.remove("hidden");
    } else {
      download.removeAttribute("href");
      download.classList.add("hidden");
    }
    var body = root.querySelector("[data-att-body]");
    body.innerHTML = "";
    root.classList.remove("hidden");
    document.documentElement.classList.add("overflow-hidden");
    return body;
  }

  function closeOverlay() {
    if (!overlayEl) return;
    overlayEl.classList.add("hidden");
    overlayEl.querySelector("[data-att-body]").innerHTML = "";
    document.documentElement.classList.remove("overflow-hidden");
  }

  function showLoading(body) {
    body.innerHTML =
      '<div class="flex h-full min-h-[40vh] flex-col items-center justify-center gap-3 text-slate-500">' +
      '  <div class="h-8 w-8 animate-spin rounded-full border-2 border-slate-300 border-t-blue-600"></div>' +
      '  <p class="text-sm">' + t("Loading preview...", "Loading preview...") + "</p>" +
      "</div>";
  }

  function showPreviewError(body, filename, downloadUrl) {
    var actions =
      '<button type="button" data-att-close class="inline-flex items-center rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 transition hover:bg-slate-50">' +
      t("Close", "Close") +
      "</button>";
    if (downloadUrl) {
      actions =
        '<a href="' + downloadUrl + '" class="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-blue-700">' +
        DOWNLOAD_ICON +
        t("Download file", "Download file") +
        "</a> " +
        actions;
    }
    body.innerHTML =
      '<div class="flex h-full min-h-[40vh] flex-col items-center justify-center gap-3 text-slate-500">' +
      '  <p class="max-w-md text-center text-sm">' +
      t("Could not load the attachment preview.", "Could not load the attachment preview.") +
      (filename ? '<span class="mt-1 block truncate text-xs text-slate-400">' + filename + "</span>" : "") +
      "  </p>" +
      '  <div class="flex items-center gap-2">' +
      actions +
      "  </div>" +
      "</div>";
  }

  // ----------------------------------------------------------------- previews

  function previewImage(body, filename, viewUrl, downloadUrl) {
    showLoading(body);
    var img = document.createElement("img");
    img.className = "mx-auto max-h-[80vh] max-w-full rounded-lg bg-white object-contain shadow-sm";
    img.alt = filename || "";
    img.onload = function () {
      body.innerHTML = "";
      body.appendChild(img);
    };
    img.onerror = function () {
      showPreviewError(body, filename, downloadUrl);
    };
    img.src = viewUrl;
  }

  function previewDoc(body, filename, viewUrl, downloadUrl) {
    showLoading(body);
    var separator = viewUrl.indexOf("?") >= 0 ? "&" : "?";
    fetch(viewUrl + separator + "fragment=1", {
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        if (!data || data.ok !== true || !data.html) throw new Error("Bad payload");
        body.innerHTML = "";
        var frame = document.createElement("iframe");
        frame.className = "h-[76vh] w-full rounded-lg border border-slate-200 bg-white";
        frame.setAttribute("sandbox", "");
        frame.setAttribute("referrerpolicy", "no-referrer");
        frame.srcdoc = data.html;
        body.appendChild(frame);
      })
      .catch(function () {
        showPreviewError(body, filename, downloadUrl);
      });
  }

  function previewAttachment(target) {
    var kind = target.getAttribute("data-kind") || "other";
    var viewUrl = target.getAttribute("data-preview-url");
    var downloadUrl = target.getAttribute("data-download-url");
    var filename = target.getAttribute("data-filename") || "";
    if (kind === "pdf") {
      // iOS cannot render PDFs inside iframes — open a top-level tab while
      // we are still inside the user gesture (U8.8c).
      window.open(viewUrl, "_blank");
      return;
    }
    if (kind === "other" || !viewUrl) {
      window.location.href = downloadUrl;
      return;
    }
    var body = openOverlay(filename, downloadUrl);
    if (kind === "image") {
      previewImage(body, filename, viewUrl, downloadUrl);
    } else {
      previewDoc(body, filename, viewUrl, downloadUrl);
    }
  }

  // ------------------------------------------------------------ open in docs

  function openInDocs(btn) {
    var downloadUrl = btn.getAttribute("data-download-url");
    var filename = btn.getAttribute("data-filename") || "attachment";
    var origHTML = btn.innerHTML;
    // Open the editor tab synchronously inside the gesture; its location is
    // set once the upload finishes. window.open after awaited fetches is
    // popup-blocked on iOS (U8.8d).
    var win = window.open("", "_blank");
    btn.disabled = true;
    btn.innerHTML = SPINNER_ICON;
    fetch(downloadUrl)
      .then(function (r) {
        if (!r.ok) throw new Error(t("Failed to download attachment", "Failed to download attachment"));
        return r.blob();
      })
      .then(function (blob) {
        var fd = new FormData();
        fd.append("file", blob, filename);
        return fetch("/app/docs/upload", {
          method: "POST",
          body: fd,
          headers: { "X-Requested-With": "XMLHttpRequest" },
        });
      })
      .then(function (r) {
        if (!r.ok) {
          return r.json().then(function (d) {
            throw new Error((d && d.error) || t("Upload failed", "Upload failed"));
          });
        }
        return r.json();
      })
      .then(function (data) {
        var url =
          (data && data.editor_url) ||
          (data && data.doc_id ? "/app/docs/" + data.doc_id + "/edit" : "");
        if (!url) throw new Error(t("Upload failed", "Upload failed"));
        if (win) {
          win.location.href = url;
        } else {
          window.location.href = url;
        }
        btn.disabled = false;
        btn.innerHTML = origHTML;
      })
      .catch(function (err) {
        if (win) win.close();
        btn.disabled = false;
        btn.innerHTML = origHTML;
        notifyError(t("Could not open in Docs: ", "Could not open in Docs: ") + err.message);
      });
  }

  // --------------------------------------------------------------- delegation

  document.addEventListener("click", function (e) {
    var docsBtn = e.target.closest("[data-open-in-docs]");
    if (docsBtn) {
      e.preventDefault();
      e.stopPropagation();
      openInDocs(docsBtn);
      return;
    }
    var previewTarget = e.target.closest("[data-attachment-preview]");
    if (previewTarget) {
      e.preventDefault();
      e.stopPropagation();
      previewAttachment(previewTarget);
    }
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeOverlay();
  });
})();
