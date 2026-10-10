/*
 * LRCal state + navigation (U12.56): view/date state, header title, URL
 * sync, renderer dispatch, now-line timer, SSE refresh hook.
 */
(function () {
  'use strict';

  var LRCal = (window.LRCal = window.LRCal || {});

  LRCal.state = {
    view: 'week',
    date: new Date(),
    renderers: {},
  };

  var nowTimer = null;

  function stopNowTimer() {
    if (nowTimer) {
      clearInterval(nowTimer);
      nowTimer = null;
    }
  }

  function positionNowLine() {
    var line = document.getElementById('cal-now-line');
    if (!line || !line.isConnected) {
      stopNowTimer();
      return;
    }
    var now = new Date();
    var pxPerHour = parseFloat(line.dataset.pxPerHour || '48');
    var topOffset = parseFloat(line.dataset.topOffset || '0');
    line.style.top = topOffset + (now.getHours() + now.getMinutes() / 60) * pxPerHour + 'px';
  }

  LRCal.startNowTimer = function () {
    stopNowTimer();
    positionNowLine();
    nowTimer = setInterval(positionNowLine, 30000);
  };
  LRCal.stopNowTimer = stopNowTimer;

  function headerTitle() {
    var MONTHS = LRCal.MONTHS();
    var DAYS = LRCal.DAYS_SHORT();
    var d = LRCal.state.date;
    switch (LRCal.state.view) {
      case 'month':
        return MONTHS[d.getMonth()] + ' ' + d.getFullYear();
      case 'week': {
        var s = LRCal.startOfWeek(d);
        var e = LRCal.addDays(s, 6);
        return (
          MONTHS[s.getMonth()] + ' ' + s.getDate() + ' – ' + MONTHS[e.getMonth()] + ' ' + e.getDate() + ', ' + e.getFullYear()
        );
      }
      case 'threeday': {
        var s2 = LRCal.state.threedayStart || d;
        var e2 = LRCal.addDays(s2, 2);
        return (
          MONTHS[s2.getMonth()] + ' ' + s2.getDate() + ' – ' + MONTHS[e2.getMonth()] + ' ' + e2.getDate() + ', ' + e2.getFullYear()
        );
      }
      case 'schedule':
        return window.LR.t('Schedule');
      default:
        return DAYS[d.getDay()] + ', ' + MONTHS[d.getMonth()] + ' ' + d.getDate() + ', ' + d.getFullYear();
    }
  }

  function updateHeader() {
    var h = document.getElementById('cal-header-date');
    if (h) h.textContent = headerTitle();
  }

  /* Which absolute days does the current view cover? */
  function currentRange() {
    var d = LRCal.state.date;
    switch (LRCal.state.view) {
      case 'month': {
        var first = new Date(d.getFullYear(), d.getMonth(), 1);
        var last = new Date(d.getFullYear(), d.getMonth() + 1, 0);
        return {
          fetchStart: LRCal.toISO(LRCal.addDays(LRCal.startOfWeek(first), -7)),
          fetchEnd: LRCal.toISO(LRCal.addDays(last, 8)),
        };
      }
      case 'week': {
        var s = LRCal.startOfWeek(d);
        return {
          days: [0, 1, 2, 3, 4, 5, 6].map(function (i) { return LRCal.addDays(s, i); }),
          fetchStart: LRCal.toISO(s),
          fetchEnd: LRCal.toISO(LRCal.addDays(s, 7)),
        };
      }
      case 'threeday': {
        var s2 = LRCal.state.threedayStart || d;
        return {
          days: [0, 1, 2].map(function (i) { return LRCal.addDays(s2, i); }),
          fetchStart: LRCal.toISO(s2),
          fetchEnd: LRCal.toISO(LRCal.addDays(s2, 3)),
        };
      }
      case 'day':
        return {
          days: [new Date(d)],
          fetchStart: LRCal.toISO(d),
          fetchEnd: LRCal.toISO(LRCal.addDays(d, 1)),
        };
      default:
        return { fetchStart: LRCal.toISO(d), fetchEnd: LRCal.toISO(LRCal.addDays(d, 31)) };
    }
  }

  LRCal.currentRange = currentRange;

  function updateViewButtons() {
    document.querySelectorAll('.view-btn').forEach(function (b) {
      b.className =
        'view-btn px-3 py-2 text-xs ' +
        (b.dataset.view === LRCal.state.view ? 'bg-slate-900 text-white' : 'text-slate-600 hover:bg-slate-50');
    });
    var select = document.getElementById('cal-view-select');
    if (select) select.value = LRCal.state.view;
  }

  /* U12.56i: view/date changes go through pushState so Back/Forward
   * navigate calendar history. Re-renders of the same view+date (SSE
   * refresh, drag-save) dedupe to zero history entries. */
  var lastSynced = null; // {view, date} last written to the URL
  var suppressPush = false;

  function syncUrl() {
    var view = LRCal.state.view;
    var dateISO = LRCal.toISO(LRCal.state.date);
    if (lastSynced && lastSynced.view === view && lastSynced.date === dateISO) {
      suppressPush = false;
      return;
    }
    var url = new URL(window.location);
    url.searchParams.set('view', view);
    url.searchParams.set('date', dateISO);
    var entry = { calView: view, calDate: dateISO };
    if (lastSynced && !suppressPush) history.pushState(entry, '', url);
    else history.replaceState(entry, '', url); // boot + popstate replays
    suppressPush = false;
    lastSynced = { view: view, date: dateISO };
  }

  function applyUrlState() {
    var params = new URLSearchParams(window.location.search);
    var view = params.get('view');
    if (view === 'agenda') view = 'schedule'; // legacy alias
    var phone = window.matchMedia('(max-width: 767px)').matches;
    var phoneViews = ['schedule', 'day', 'threeday', 'month'];
    if (view && LRCal.state.renderers[view] && !(phone && phoneViews.indexOf(view) === -1)) {
      LRCal.state.view = view;
    } else if (phone && phoneViews.indexOf(LRCal.state.view) === -1) {
      LRCal.state.view = 'schedule';
    }
    var date = params.get('date');
    if (date && /^\d{4}-\d{2}-\d{2}$/.test(date)) {
      var d = LRCal.parseLocalDate(date);
      if (!isNaN(d.getTime())) LRCal.state.date = d;
    }
    LRCal.state.threedayStart = null;
  }

  window.addEventListener('popstate', function () {
    /* U12.56i: Back closes an open mobile drawer and applies the URL's
     * view/date. suppressPush stays set until navigate's async syncUrl
     * consumes it (replaceState), so the replay never duplicates the
     * popped entry. */
    if (LRCal.sidebarDrawerOpen && LRCal.sidebarDrawerOpen() && LRCal.closeSidebarDrawer) {
      LRCal.closeSidebarDrawer({ fromHistory: true });
    }
    suppressPush = true;
    applyUrlState();
    LRCal.navigate();
  });

  var navSeq = 0;

  function navigate(view) {
    if (view) LRCal.state.view = view;
    if (LRCal.state.view === 'agenda') LRCal.state.view = 'schedule'; // legacy alias

    var grid = document.getElementById('calendar-grid');
    if (!grid) return;

    var renderer = LRCal.state.renderers[LRCal.state.view];
    if (!renderer) return;

    var range = currentRange();
    updateHeader();
    updateViewButtons();
    renderMini();

    // U24.39: surface a busy indicator while the view's events load, and
    // ignore superseded responses so a slow earlier fetch can't render a
    // stale range over a newer navigation.
    var seq = ++navSeq;
    LRCal.setBusy(true);
    LRCal.api.fetchEvents(range.fetchStart, range.fetchEnd).then(function (events) {
      if (seq !== navSeq) return;
      LRCal.lastEventsById = {};
      events.forEach(function (ev) {
        LRCal.lastEventsById[ev.id] = ev;
      });
      renderer(grid, {
        date: LRCal.state.date,
        range: range,
        events: events,
      });
      syncUrl();
    }).finally(function () {
      if (seq === navSeq) LRCal.setBusy(false);
    });
  }

  function renderMini() {
    var el = document.getElementById('mini-calendar');
    if (el) LRCal.renderMiniCalendar(el, { date: LRCal.state.date });
  }

  LRCal.navigate = navigate;

  /* prev/next period per view (U12.56d swipe uses the same). */
  LRCal.shiftPeriod = function (delta) {
    var s = LRCal.state;
    switch (s.view) {
      case 'month':
        s.date = new Date(s.date.getFullYear(), s.date.getMonth() + delta, 1);
        break;
      case 'week':
        s.date = LRCal.addDays(s.date, 7 * delta);
        break;
      case 'threeday':
        s.threedayStart = LRCal.addDays(s.threedayStart || s.date, 3 * delta);
        s.date = s.threedayStart;
        break;
      case 'schedule':
        s.date = LRCal.addDays(s.date, 7 * delta);
        break;
      default:
        s.date = LRCal.addDays(s.date, delta);
    }
    navigate();
  };

  /* Re-render current view after data changes (save/move/SSE). */
  LRCal.refresh = function () {
    LRCal.api.invalidateCache();
    navigate();
  };
})();
