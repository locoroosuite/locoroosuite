/* U27.5: global AI activity indicator + feed.
 *
 * Listens for `ai_activity` SSE events on /events/stream (pushed by the
 * REST API hook and the MCP event bridge) and shows a header badge while
 * an AI agent is working on the user's account: pulsing while events keep
 * arriving, idle after 15s of silence, hidden after a further 30s.
 * Clicking the badge opens the activity feed popover (last 50 events,
 * in-memory per tab). Tool names and summaries stay English (U26.8);
 * labels and status words are localized via window.LR.t (U27.5).
 */
(function () {
  'use strict';

  var ACTIVE_MS = 15000;   // pulsing while events keep arriving
  var HIDE_MS = 30000;     // hide the badge this long after going idle
  var MAX_FEED = 50;

  var root = null;
  var badgeButton = null;
  var panel = null;
  var feed = [];
  var lastEventAt = 0;
  var panelOpen = false;
  var tickTimer = null;

  function statusLabel(status) {
    if (!status || status === 'ok') return window.LR.t('OK');
    return String(status);
  }

  function timeLabel(ts) {
    var delta = Math.max(0, Math.round((Date.now() - ts) / 1000));
    if (delta < 5) return window.LR.t('just now');
    if (delta < 60) return window.LR.t('{n}s ago').replace('{n}', String(delta));
    var mins = Math.floor(delta / 60);
    if (mins < 60) return window.LR.t('{n}m ago').replace('{n}', String(mins));
    return window.LR.t('{n}h ago').replace('{n}', String(Math.floor(mins / 60)));
  }

  function renderPanel() {
    if (!panel) return;
    var list = panel.querySelector('[data-ai-feed]');
    if (!list) return;
    list.innerHTML = '';
    var entries = feed.slice(0, MAX_FEED);
    if (!entries.length) {
      var empty = document.createElement('li');
      empty.className = 'px-4 py-6 text-sm text-slate-400 text-center';
      empty.textContent = window.LR.t('No recent AI activity.');
      list.appendChild(empty);
      return;
    }
    entries.forEach(function (entry) {
      var li = document.createElement('li');
      li.className = 'px-4 py-2.5 border-b border-slate-100 last:border-b-0';
      var row = document.createElement('div');
      row.className = 'flex items-start gap-2';
      var dot = document.createElement('span');
      var isError = entry.status && entry.status !== 'ok';
      dot.className = isError
        ? 'mt-1.5 h-2 w-2 flex-shrink-0 rounded-full bg-red-500'
        : 'mt-1.5 h-2 w-2 flex-shrink-0 rounded-full bg-indigo-400';
      row.appendChild(dot);
      var body = document.createElement('div');
      body.className = 'min-w-0 flex-1';
      var title = document.createElement('div');
      title.className = 'text-sm font-medium text-slate-700 truncate';
      title.textContent = entry.tool || 'AI';
      body.appendChild(title);
      if (entry.summary) {
        var sum = document.createElement('div');
        sum.className = 'text-xs text-slate-500 truncate';
        sum.textContent = entry.summary;
        body.appendChild(sum);
      }
      var meta = document.createElement('div');
      meta.className = 'text-[0.6875rem] text-slate-400 mt-0.5';
      var bits = [timeLabel(entry.ts)];
      if (entry.count && entry.count > 1) bits.push('×' + entry.count);
      if (isError) bits.push(statusLabel(entry.status));
      if (typeof entry.duration_ms === 'number' && entry.duration_ms > 0) {
        bits.push(entry.duration_ms + ' ms');
      }
      meta.textContent = bits.join(' · ');
      body.appendChild(meta);
      row.appendChild(body);
      li.appendChild(row);
      list.appendChild(li);
    });
  }

  function setBadgeState(state) {
    if (!badgeButton) return;
    badgeButton.setAttribute('data-ai-state', state);
    badgeButton.classList.toggle('hidden', state === 'hidden');
    var label = badgeButton.querySelector('[data-ai-label]');
    if (label) {
      label.textContent = state === 'active'
        ? window.LR.t('AI is working')
        : window.LR.t('AI activity');
    }
  }

  function tick() {
    var since = Date.now() - lastEventAt;
    if (since < ACTIVE_MS) {
      setBadgeState('active');
    } else if (since < ACTIVE_MS + HIDE_MS) {
      setBadgeState('idle');
    } else {
      setBadgeState('hidden');
      if (tickTimer) { clearInterval(tickTimer); tickTimer = null; }
    }
    if (panelOpen) renderPanel();
  }

  function onActivity(data) {
    lastEventAt = Date.now();
    feed.unshift({
      tool: data && data.tool,
      summary: data && data.summary,
      status: data && data.status,
      count: (data && data.count) || 1,
      duration_ms: data && data.duration_ms,
      ts: Date.now(),
    });
    if (feed.length > MAX_FEED) feed.length = MAX_FEED;
    if (!tickTimer) tickTimer = setInterval(tick, 1000);
    tick();
  }

  function build() {
    root = document.getElementById('ai-activity-root');
    if (!root) return false;

    root.className = 'relative';

    badgeButton = document.createElement('button');
    badgeButton.type = 'button';
    badgeButton.id = 'ai-activity-badge';
    badgeButton.className = 'hidden h-9 px-3 rounded-lg hover:bg-slate-100 flex items-center gap-2 transition-colors';
    badgeButton.setAttribute('aria-label', window.LR.t('AI activity'));
    badgeButton.setAttribute('aria-expanded', 'false');
    badgeButton.setAttribute('aria-controls', 'ai-activity-panel');
    badgeButton.innerHTML =
      '<span data-ai-dot class="relative flex h-2.5 w-2.5">' +
      '  <span data-ai-ping class="absolute inline-flex h-full w-full rounded-full bg-indigo-400 opacity-75 animate-ping"></span>' +
      '  <span data-ai-core class="relative inline-flex rounded-full h-2.5 w-2.5 bg-indigo-500"></span>' +
      '</span>' +
      '<span data-ai-label class="text-sm font-medium text-slate-600"></span>';
    badgeButton.addEventListener('click', function () {
      panelOpen = !panelOpen;
      if (panel) panel.classList.toggle('hidden', !panelOpen);
      badgeButton.setAttribute('aria-expanded', panelOpen ? 'true' : 'false');
      if (panelOpen) renderPanel();
    });
    root.appendChild(badgeButton);

    panel = document.createElement('div');
    panel.id = 'ai-activity-panel';
    panel.className = 'hidden absolute right-0 mt-2 w-80 max-w-[calc(100vw-1.5rem)] rounded-xl border border-slate-200 bg-white shadow-lg overflow-hidden z-50';
    panel.innerHTML =
      '<div class="px-4 py-2.5 border-b border-slate-100 flex items-center gap-2">' +
      '  <span class="text-sm font-semibold text-slate-700">' + escapeHtml(window.LR.t('AI activity')) + '</span>' +
      '  <span class="text-[0.6875rem] text-slate-400">' + escapeHtml(window.LR.t('Actions taken by AI agents on your account')) + '</span>' +
      '</div>' +
      '<ul data-ai-feed class="max-h-80 overflow-y-auto"></ul>';
    root.appendChild(panel);

    document.addEventListener('click', function (e) {
      if (!panelOpen) return;
      if (root.contains(e.target)) return;
      panelOpen = false;
      panel.classList.add('hidden');
      badgeButton.setAttribute('aria-expanded', 'false');
    });

    return true;
  }

  function escapeHtml(text) {
    var div = document.createElement('div');
    div.textContent = String(text == null ? '' : text);
    return div.innerHTML;
  }

  // Badge visual state is driven by data-ai-state; classes must stay static
  // literals so the Tailwind JIT scanner emits them. The composing shimmer
  // (U27.6) is a custom keyframe — self-contained here so no CSS build step
  // is required for it.
  var style = document.createElement('style');
  style.textContent =
    '#ai-activity-badge[data-ai-state="active"] [data-ai-ping]{display:inline-flex}' +
    '#ai-activity-badge[data-ai-state="idle"] [data-ai-ping]{display:none}' +
    '#ai-activity-badge[data-ai-state="idle"] [data-ai-core]{background-color:#94a3b8}' +
    '@keyframes lr-ai-shimmer{0%{opacity:1}50%{opacity:.45}100%{opacity:1}}' +
    '.lr-ai-composing [data-thread-header]{animation:lr-ai-shimmer 1.6s ease-in-out infinite}';
  document.head.appendChild(style);

  function init() {
    if (typeof window.EventSource === 'undefined') return;
    if (!build()) return;
    var source = new EventSource('/events/stream');
    source.addEventListener('ai_activity', function (evt) {
      var data;
      try { data = JSON.parse(evt.data || '{}'); } catch (e) { return; }
      onActivity(data);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.LRAiActivity = { onActivity: onActivity };
})();
