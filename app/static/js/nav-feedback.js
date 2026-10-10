/**
 * Shared navigation pending feedback (HLD U24.39 / UX9).
 *
 * Every customer-facing full-page navigation acknowledges the tap
 * instantly: the tapped link is dimmed and made non-interactive
 * (blocking double-taps) and a thin indeterminate top progress bar
 * signals the document navigation. Fetch-driven callers (mail message
 * rows) reuse LR.navPending(el) directly before window.location.href.
 *
 * Clearing: pageshow (covers bfcache Back-restore, where the dimmed
 * document is resurrected) or a 10s safety timeout for aborted
 * navigations. Anchors handled by in-page JS (defaultPrevented, e.g.
 * preview-pane links), modified clicks, target=_blank, downloads, and
 * same-page hash links are excluded. prefers-reduced-motion is
 * honored in CSS (instant dim, static bar).
 */
(function () {
  'use strict';

  var CLEAR_TIMEOUT_MS = 10000;
  var PROGRESS_ID = 'lr-nav-progress';
  var clearTimer = null;

  function ensureBar() {
    var bar = document.getElementById(PROGRESS_ID);
    if (!bar) {
      bar = document.createElement('div');
      bar.id = PROGRESS_ID;
      bar.className = 'lr-nav-progress';
      bar.setAttribute('aria-hidden', 'true');
      bar.innerHTML = '<span class="lr-nav-progress-bar"></span>';
      document.body.appendChild(bar);
    }
    return bar;
  }

  /* Dim el, show the progress bar, mark the document busy. */
  function navPending(el) {
    if (el && el.classList) {
      el.classList.add('lr-nav-pending');
    }
    ensureBar().classList.add('lr-nav-active');
    document.body.setAttribute('aria-busy', 'true');
    if (clearTimer) window.clearTimeout(clearTimer);
    clearTimer = window.setTimeout(clearNavPending, CLEAR_TIMEOUT_MS);
  }

  /* Remove every pending marker (pageshow, timeout, or manual reset). */
  function clearNavPending() {
    if (clearTimer) {
      window.clearTimeout(clearTimer);
      clearTimer = null;
    }
    var pending = document.querySelectorAll('.lr-nav-pending');
    for (var i = 0; i < pending.length; i++) {
      pending[i].classList.remove('lr-nav-pending');
    }
    var bar = document.getElementById(PROGRESS_ID);
    if (bar) bar.classList.remove('lr-nav-active');
    if (document.body) document.body.removeAttribute('aria-busy');
  }

  /* True when the anchor leads to a same-document full navigation we
   * want to acknowledge. Conservative: anything unusual bails. */
  function isEligible(link) {
    if (link.dataset.navFeedback === 'false') return false;
    if (link.closest('[data-no-nav-feedback]')) return false;
    var raw = link.getAttribute('href') || '';
    if (!raw || raw.charAt(0) === '#' || raw.indexOf('javascript:') === 0) return false;
    if (link.hasAttribute('download')) return false;
    if (link.target && link.target !== '_self') return false;
    var url;
    try {
      url = new URL(link.href, window.location.href);
    } catch (err) {
      return false;
    }
    if (url.origin !== window.location.origin) return false;
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return false;
    return true;
  }

  document.addEventListener('click', function (e) {
    // In-page handlers that already claimed the click (preview links,
    // drawer toggles) run on inner nodes first — respect their verdict.
    if (e.defaultPrevented) return;
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var link = e.target.closest ? e.target.closest('a[href]') : null;
    if (!link || !isEligible(link)) return;
    navPending(link);
  });

  // Fires on every page display: fresh loads (no-op) and bfcache
  // restores (clears the stale dimmed state left by the navigation).
  window.addEventListener('pageshow', clearNavPending);

  window.LR = window.LR || {};
  window.LR.navPending = navPending;
  window.LR.clearNavPending = clearNavPending;
})();
