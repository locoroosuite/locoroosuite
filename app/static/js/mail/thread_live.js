/* U4.7f: live conversation updates on the message-detail view.
 *
 * Listens for mail `ui_change` SSE events (send_complete, syncs, actions),
 * fetches the thread fragment for the open conversation, and inserts any
 * genuinely new messages in place — preserving the reader's
 * expand/collapse state. New messages slide in expanded with the same
 * transition curve as the folder-list FLIP animation.
 *
 * The fragment returns only messages the page does not already have
 * (`have=` ids), so existing DOM (sanitized current body, ICS cards,
 * banners) is never re-rendered or destroyed.
 */
(function () {
  'use strict';

  function init(opts) {
    var stackEl = document.getElementById('thread-stack');
    if (!stackEl || typeof window.EventSource === 'undefined' || !window.LRMailThread) {
      return;
    }
    var accountId = String(opts.accountId || '');
    var fragmentUrl = opts.fragmentUrl;
    var timer = null;
    var inFlight = false;

    function rowIds() {
      return Array.prototype.map.call(
        stackEl.querySelectorAll('[data-thread-msg-id]'),
        function (el) { return el.getAttribute('data-thread-msg-id'); }
      );
    }

    function requestUrl() {
      var url = fragmentUrl;
      var ids = rowIds();
      if (ids.length) {
        url += (url.indexOf('?') >= 0 ? '&' : '?') + 'have=' + encodeURIComponent(ids.join(','));
      }
      return url;
    }

    function insertBySortTs(node) {
      var ts = parseInt(node.getAttribute('data-thread-sort-ts') || '0', 10) || 0;
      var siblings = stackEl.querySelectorAll('.thread-msg');
      for (var i = 0; i < siblings.length; i++) {
        var other = siblings[i];
        if (other === node) continue;
        var otherTs = parseInt(other.getAttribute('data-thread-sort-ts') || '0', 10) || 0;
        if (ts > otherTs) {
          stackEl.insertBefore(node, other);
          return;
        }
      }
      stackEl.appendChild(node);
    }

    function updateCount(count) {
      var el = document.querySelector('[data-thread-count]');
      if (!el || !window.LR || typeof window.LR.t !== 'function') return;
      if (count > 1) {
        el.textContent = window.LR.t('{n} messages in this conversation').replace('{n}', count);
        el.classList.remove('hidden');
      } else {
        el.classList.add('hidden');
      }    }

    function refresh() {
      if (inFlight) {
        schedule();
        return;
      }
      inFlight = true;
      fetch(requestUrl(), { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
        .then(function (resp) { return resp.ok ? resp.json() : null; })
        .then(function (data) {
          if (!data || !Array.isArray(data.new_ids) || data.new_ids.length === 0) return;
          var holder = document.createElement('div');
          holder.innerHTML = data.html;
          var bodies = window.LRMailThread.bodies;
          var lrBaseOrigin = window.location.origin + '/';
          Object.keys(data.bodies || {}).forEach(function (id) {
            if (!(id in bodies)) {
              // Same base-href rewrite as the page's inline body map, so
              // relative URLs inside the iframe resolve against the origin.
              bodies[id] = String(data.bodies[id]).replace(
                '<base target="_blank"',
                '<base href="' + lrBaseOrigin + '" target="_blank"'
              );
            }
          });
          var newIdStrs = data.new_ids.map(String);
          var nodes = Array.prototype.slice.call(holder.querySelectorAll('.thread-msg'));
          nodes.forEach(function (node) {
            insertBySortTs(node);
          });
          // New messages enter expanded (U4.7f) with a slide/fade.
          window.LRMailThread.applyStack(stackEl, bodies, newIdStrs, newIdStrs);
          updateCount(data.count);
        })
        .catch(function (err) {
          // Progressive enhancement: a failed background refresh leaves the
          // rendered page intact. Log with context for diagnosis.
          console.warn('thread live refresh failed', err);
        })
        .finally(function () { inFlight = false; });
    }

    function schedule() {
      if (timer) clearTimeout(timer);
      timer = setTimeout(refresh, 400);
    }

    var source = new EventSource('/events/stream');
    source.addEventListener('ui_change', function (evt) {
      var data;
      try {
        data = JSON.parse(evt.data || '{}');
      } catch (e) {
        return;
      }
      if (data.module !== 'mail') return;
      if (data.account_id && String(data.account_id) !== accountId) return;
      schedule();
    });
  }

  window.LRMailThreadLive = { init: init };
})();
