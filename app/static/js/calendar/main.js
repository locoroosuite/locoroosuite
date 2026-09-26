/*
 * LRCal bootstrap (U12.56): registers renderers, wires toolbar, sidebar
 * drawer, mobile view select + FAB, swipe navigation (U12.56d), prefill
 * deep links (?new=1 / ?edit=<id>), and SSE live refresh.
 */
(function () {
  'use strict';

  var LRCal = (window.LRCal = window.LRCal || {});

  LRCal.setBusy = function (busy) {
    var bar = document.getElementById('cal-busy');
    if (bar) bar.classList.toggle('opacity-0', !busy);
  };

  LRCal.notifyError = function (msg) {
    if (window.LR && window.LR.notifyError) window.LR.notifyError(msg);
  };

  function isPhoneViewport() {
    return window.matchMedia('(max-width: 767px)').matches;
  }

  /* ---- renderers ---- */

  LRCal.state.renderers.week = function (grid, opts) {
    var days = (opts.range && opts.range.days) || [];
    LRCal.renderTimeGrid(grid, { days: days, events: opts.events });
  };

  LRCal.state.renderers.day = function (grid, opts) {
    LRCal.renderTimeGrid(grid, { days: [new Date(LRCal.state.date)], events: opts.events });
  };

  LRCal.state.renderers.threeday = function (grid, opts) {
    var days = (opts.range && opts.range.days) || [];
    LRCal.renderTimeGrid(grid, { days: days, events: opts.events });
  };

  LRCal.state.renderers.month = function (grid, opts) {
    LRCal.renderMonth(grid, { date: LRCal.state.date, events: opts.events });
  };

  LRCal.state.renderers.schedule = function (grid, opts) {
    LRCal.renderSchedule(grid, { date: LRCal.state.date, events: opts.events });
  };

  /* ---- sidebar drawer (same recipe as mail) ---- */

  function initDrawer() {
    var sidebar = document.getElementById('cal-sidebar');
    var backdrop = document.getElementById('cal-sidebar-backdrop');
    var toggle = document.getElementById('cal-sidebar-toggle');
    var closeBtn = document.getElementById('cal-sidebar-close');
    var isOpen = function () {
      return sidebar && !sidebar.classList.contains('-translate-x-full');
    };
    function setOpen(open) {
      if (!sidebar || !backdrop) return;
      sidebar.classList.toggle('-translate-x-full', !open);
      backdrop.classList.toggle('hidden', !open);
      document.body.style.overflow = open ? 'hidden' : '';
      if (toggle) toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
    if (toggle) toggle.addEventListener('click', function () { setOpen(!isOpen()); });
    if (closeBtn) closeBtn.addEventListener('click', function () { setOpen(false); });
    if (backdrop) backdrop.addEventListener('click', function () { setOpen(false); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && isOpen()) setOpen(false);
    });
    window.addEventListener('resize', function () {
      if (window.matchMedia('(min-width: 1024px)').matches && isOpen()) setOpen(false);
    });
  }

  /* ---- swipe navigation (U12.56d) ---- */

  function initSwipe() {
    var grid = document.getElementById('calendar-grid');
    if (!grid) return;
    var touch = null;
    grid.addEventListener(
      'touchstart',
      function (e) {
        if (e.touches.length !== 1 || LRCal.dnd.active) {
          touch = null;
          return;
        }
        touch = {
          x: e.touches[0].clientX,
          y: e.touches[0].clientY,
          view: LRCal.state.view,
        };
      },
      { passive: true }
    );
    grid.addEventListener(
      'touchend',
      function (e) {
        if (!touch || LRCal.dnd.active) {
          touch = null;
          return;
        }
        var t = e.changedTouches[0];
        var dx = t.clientX - touch.x;
        var dy = t.clientY - touch.y;
        var viewAtStart = touch.view;
        touch = null;
        if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
        var editor = document.getElementById('cal-editor');
        if (editor && !editor.classList.contains('hidden')) return;
        if (viewAtStart !== LRCal.state.view) return; // re-rendered mid-gesture
        LRCal.shiftPeriod(dx < 0 ? 1 : -1);
      },
      { passive: true }
    );
  }

  /* ---- toolbar ---- */

  function initToolbar() {
    document.getElementById('cal-today').addEventListener('click', function () {
      LRCal.state.date = new Date();
      LRCal.state.threedayStart = null;
      LRCal.navigate();
    });
    document.getElementById('cal-prev').addEventListener('click', function () {
      LRCal.shiftPeriod(-1);
    });
    document.getElementById('cal-next').addEventListener('click', function () {
      LRCal.shiftPeriod(1);
    });
    document.querySelectorAll('.view-btn').forEach(function (b) {
      b.addEventListener('click', function () {
        LRCal.navigate(b.dataset.view);
      });
    });
    var select = document.getElementById('cal-view-select');
    if (select) {
      select.addEventListener('change', function () {
        LRCal.navigate(select.value);
      });
    }
    var newBtn = document.getElementById('cal-new-event');
    if (newBtn) {
      newBtn.addEventListener('click', function (e) {
        e.preventDefault();
        openEditorWithDefaults();
      });
    }
    var fab = document.getElementById('cal-fab');
    if (fab) {
      fab.addEventListener('click', function () {
        openEditorWithDefaults();
      });
    }
  }

  function openEditorWithDefaults() {
    var d = LRCal.state.date;
    var iso = LRCal.toISO(d);
    LRCal.editor.open({ dtstart: iso + 'T09:00', dtend: iso + 'T10:00' });
  }

  /* ---- deep-link prefill (?new=1, ?edit=<id>) ---- */

  function handleQueryPrefill() {
    var params = new URLSearchParams(window.location.search);
    if (params.get('new') === '1') {
      LRCal.editor.open({
        summary: params.get('summary') || '',
        description: params.get('description') || '',
        attendee: params.get('attendee') || '',
        dtstart: params.get('dtstart') || '',
        dtend: params.get('dtend') || '',
        calendarId: params.get('calendar_id') || '',
      });
      params.delete('new');
      params.delete('summary');
      params.delete('description');
      params.delete('attendee');
      params.delete('dtstart');
      params.delete('dtend');
      params.delete('calendar_id');
      var url = new URL(window.location);
      window.history.replaceState(null, '', url.pathname + (params.toString() ? '?' + params : ''));
      return;
    }
    var editId = params.get('edit');
    if (editId) {
      LRCal.setBusy(true);
      LRCal.api
        .getEvent(editId)
        .then(function (ev) {
          LRCal.setBusy(false);
          if (ev && ev.id) LRCal.editor.open({ event: ev });
        })
        .catch(function () {
          LRCal.setBusy(false);
        });
      var url2 = new URL(window.location);
      url2.searchParams.delete('edit');
      window.history.replaceState(null, '', url2);
    }
  }

  /* ---- SSE live updates ---- */

  function initSse() {
    if (!window.EventSource) return;
    var source = new EventSource(LRCal.boot.SSE_URL);
    source.addEventListener('ui_change', function (evt) {
      try {
        var d = JSON.parse(evt.data || '{}');
        if (d.module === 'calendar') LRCal.refresh();
      } catch (e) {
        /* malformed payload: ignore */
      }
    });
  }

  /* ---- boot ---- */

  function boot() {
    LRCal.initNames();
    LRCal.boot = window.LRCAL_BOOT || {};
    if (!LRCal.boot.CALENDARS) LRCal.boot.CALENDARS = [];

    LRCal.state.view = LRCal.boot.INITIAL_VIEW || 'week';
    LRCal.state.date = LRCal.boot.INITIAL_DATE
      ? new Date(LRCal.boot.INITIAL_DATE + 'T00:00:00')
      : new Date();

    /* U12.56: phones use Schedule by default and never render the 7-column
     * week view; unsupported views (incl. explicit ?view=week URLs) map to
     * Schedule. The persisted desktop view is untouched. */
    var phoneViews = ['schedule', 'day', 'threeday', 'month'];
    if (isPhoneViewport() && phoneViews.indexOf(LRCal.state.view) === -1) {
      LRCal.state.view = 'schedule';
    }

    initDrawer();
    initToolbar();
    initSwipe();
    LRCal.dndInit();
    LRCal.editorInit();
    LRCal.navigate();
    handleQueryPrefill();
    initSse();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
