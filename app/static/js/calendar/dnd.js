/*
 * LRCal drag interactions (U12.19, U12.20, U12.56e).
 * Unified pointer events (mouse + touch), 15-minute snap:
 *  - drag-create on empty time cells (mouse; touch taps quick-create instead)
 *  - drag-move on event chips (both axes, day rollover)
 *  - drag-resize via bottom handle
 *  - all-day pills: move + resize by whole days in week view
 */
(function () {
  'use strict';

  var LRCal = (window.LRCal = window.LRCal || {});
  var SNAP_MIN = 15;
  var MIN_DURATION_MIN = 15;
  var DRAG_THRESHOLD_PX = 6;

  LRCal.dnd = { active: false };

  var drag = null;
  var ghost = null;

  function esc(s) {
    return LRCal.escapeHtml(s);
  }

  function snapMinutes(min) {
    return Math.round(min / SNAP_MIN) * SNAP_MIN;
  }

  /* Position -> {dateISO, minutes} using the time cells under the pointer. */
  function cellAt(x, y) {
    var el = document.elementFromPoint(x, y);
    if (!el) return null;
    var cell = el.closest('.time-cell');
    if (!cell) return null;
    var rect = cell.getBoundingClientRect();
    var minutes = parseInt(cell.dataset.hour, 10) * 60 + ((y - rect.top) / rect.height) * 60;
    return { date: cell.dataset.date, minutes: minutes, column: cell.parentElement };
  }

  function minutesToISOTime(min) {
    min = Math.max(0, Math.min(24 * 60 - MIN_DURATION_MIN, snapMinutes(min)));
    return LRCal.pad(Math.floor(min / 60)) + ':' + LRCal.pad(min % 60);
  }

  function ensureGhost() {
    if (ghost) return ghost;
    ghost = document.createElement('div');
    ghost.className = 'qc-ghost';
    ghost.style.cssText =
      'position:fixed;z-index:45;pointer-events:none;border-radius:6px;' +
      'background:rgba(59,130,246,0.18);border:1px solid rgba(59,130,246,0.5);display:none';
    document.body.appendChild(ghost);
    return ghost;
  }

  function showGhost(rect) {
    var g = ensureGhost();
    g.style.display = 'block';
    g.style.left = rect.left + 'px';
    g.style.top = rect.top + 'px';
    g.style.width = rect.width + 'px';
    g.style.height = rect.height + 'px';
  }

  function hideGhost() {
    if (ghost) ghost.style.display = 'none';
  }

  function clearHighlights() {
    document.querySelectorAll('.qc-highlighted').forEach(function (el) {
      el.classList.remove('qc-highlighted');
    });
  }

  function highlightCell(dateISO, startMin, endMin) {
    clearHighlights();
    for (var h = Math.floor(startMin / 60); h <= Math.min(23, Math.floor((endMin - 0.01) / 60)); h++) {
      var c = document.querySelector('.time-cell[data-date="' + dateISO + '"][data-hour="' + h + '"]');
      if (c) c.classList.add('qc-highlighted');
    }
  }

  /* ---- drag-create (mouse on empty cells) ---- */

  function beginCreate(e, cell) {
    var rect = cell.getBoundingClientRect();
    var rawMin =
      parseInt(cell.dataset.hour, 10) * 60 + ((e.clientY - rect.top) / rect.height) * 60;
    drag = {
      mode: 'create',
      date: cell.dataset.date,
      startMin: Math.max(0, Math.min(23 * 60 + 45, snapMinutes(rawMin))),
      anchorY: e.clientY,
    };
  }

  function updateCreate(e) {
    var cell = cellAt(e.clientX, e.clientY);
    if (!cell || cell.date !== drag.date) {
      showGhost({
        left: 0,
        top: 0,
        width: 0,
        height: 0,
      });
      drag.endMin = drag.startMin + 60;
      drag.liveDate = null;
      return;
    }
    var endMin = Math.max(drag.startMin + MIN_DURATION_MIN, snapMinutes(cell.minutes));
    drag.endMin = endMin;
    drag.liveDate = cell.date;
    var firstCell = document.querySelector('.time-cell[data-date="' + drag.date + '"][data-hour="' + Math.floor(drag.startMin / 60) + '"]');
    if (firstCell) {
      var hourH = firstCell.getBoundingClientRect().height;
      var col = firstCell.parentElement.getBoundingClientRect();
      showGhost({
        left: col.left,
        top: col.top + ((drag.startMin / 60) * hourH),
        width: col.width,
        height: Math.max(((endMin - drag.startMin) / 60) * hourH, 14),
      });
      highlightCell(drag.date, drag.startMin, endMin);
    }
  }

  /* ---- move / resize existing events ---- */

  function beginEventDrag(e, chip, mode) {
    var ev = LRCal.lastEventsById && LRCal.lastEventsById[chip.dataset.eventId];
    if (!ev) return;
    var span = LRCal.eventSpan(ev);
    if (!span) return;
    drag = {
      mode: mode, // 'move' | 'resize' | 'allday-move' | 'allday-resize'
      ev: ev,
      chip: chip,
      originStart: span.start.getTime(),
      originEnd: span.end.getTime(),
      originX: e.clientX,
      originY: e.clientY,
      moved: false,
      baseLeft: chip.style.left,
      baseTop: chip.style.top,
      baseWidth: chip.style.width,
    };
    if (e.pointerType === 'touch') {
      drag.holdTimer = setTimeout(function () {
        if (drag) drag.confirmed = true;
      }, 180);
    } else {
      drag.confirmed = true;
    }
  }

  function updateEventDrag(e) {
    if (!drag.confirmed) {
      if (Math.abs(e.clientY - drag.originY) > 12 || Math.abs(e.clientX - drag.originX) > 12) {
        clearTimeout(drag.holdTimer);
        drag = null;
      }
      return;
    }
    if (!drag.moved && Math.hypot(e.clientX - drag.originX, e.clientY - drag.originY) < DRAG_THRESHOLD_PX) return;
    drag.moved = true;
    LRCal.dnd.active = true;

    if (drag.mode === 'move' || drag.mode === 'resize') {
      var cell = cellAt(e.clientX, e.clientY);
      if (!cell) return;
      var dayStart = LRCal.parseLocalDate(cell.date).getTime();
      var minutes = snapMinutes(Math.max(0, cell.minutes));
      var duration = drag.originEnd - drag.originStart;

      if (drag.mode === 'move') {
        var newStart = dayStart + minutes * 60000;
        drag.newStart = newStart;
        drag.newEnd = newStart + duration;
      } else {
        var start = drag.originStart;
        var newEnd = dayStart + minutes * 60000;
        if (newEnd - start < MIN_DURATION_MIN * 60000) newEnd = start + MIN_DURATION_MIN * 60000;
        drag.newStart = start;
        drag.newEnd = newEnd;
      }
      /* Live feedback: translate the chip vertically to follow the pointer. */
      var dy = e.clientY - drag.originY;
      drag.chip.style.transform = 'translateY(' + dy + 'px)';
      drag.chip.style.opacity = '0.85';
      drag.chip.style.zIndex = '40';
    } else {
      /* all-day: horizontal by day columns */
      var laneCell = document.elementFromPoint(e.clientX, e.clientY);
      var target = laneCell && laneCell.closest('.allday-cell');
      if (!target || !target.dataset.date) return;
      var days = Math.round(
        (LRCal.parseLocalDate(target.dataset.date).getTime() -
          new Date(drag.originStart).setHours(0, 0, 0, 0)) /
          86400000
      );
      if (drag.mode === 'allday-move') {
        drag.dayDelta = days;
      } else {
        drag.dayDeltaEnd = days;
      }
      var dx = e.clientX - drag.originX;
      drag.chip.style.transform = 'translateX(' + dx + 'px)';
      drag.chip.style.opacity = '0.85';
      drag.chip.style.zIndex = '40';
    }
  }

  function finishEventDrag(e) {
    clearTimeout(drag.holdTimer);
    var d = drag;
    drag = null;
    LRCal.dnd.active = false;
    d.chip.style.transform = '';
    d.chip.style.opacity = '';
    d.chip.style.zIndex = '';
    if (!d.moved) {
      /* treated as a click -> popup */
      LRCal.popup.show(d.ev, d.chip);
      return;
    }

    var ev = d.ev;
    var tz = ev.timezone_resolved || ev.timezone || '';
    var payloadStart;
    var payloadEnd;

    if (d.mode === 'move' || d.mode === 'resize') {
      payloadStart = toLocalISODT(new Date(d.newStart), ev.all_day, tz);
      payloadEnd = toLocalISODT(new Date(d.newEnd), ev.all_day, tz);
    } else {
      var originStart = new Date(d.originStart);
      var originEnd = new Date(d.originEnd);
      var startShift = d.dayDelta || 0;
      var endShift = d.dayDeltaEnd !== undefined ? d.dayDeltaEnd : startShift;
      var ns = LRCal.addDays(originStart, startShift);
      var ne = LRCal.addDays(originEnd, endShift);
      payloadStart = toLocalISODT(ns, true, '');
      payloadEnd = toLocalISODT(ne, true, '');
    }

    LRCal.setBusy(true);
    LRCal.api
      .moveEvent(ev.id, payloadStart, payloadEnd)
      .then(function (data) {
        LRCal.setBusy(false);
        if (data && data.ok) {
          LRCal.refresh();
          if (data.notify_guests) LRCal.promptSendUpdates(ev.id);
        } else {
          LRCal.notifyError(
            (data && data.error) || window.LR.t('Failed to move event. Please check your connection and retry.')
          );
          LRCal.refresh();
        }
      })
      .catch(function () {
        LRCal.setBusy(false);
        LRCal.notifyError(window.LR.t('Network error. Please retry.'));
        LRCal.refresh();
      });
  }

  /* Absolute Date -> payload string in the event's own frame. */
  function toLocalISODT(absDate, allDay, tz) {
    if (allDay) return LRCal.toISO(absDate);
    if (tz) {
      try {
        var parts = new Intl.DateTimeFormat('en-CA', {
          timeZone: tz,
          year: 'numeric', month: '2-digit', day: '2-digit',
          hour: '2-digit', minute: '2-digit', hour12: false,
        }).formatToParts(absDate).reduce(function (acc, p) {
          acc[p.type] = p.value;
          return acc;
        }, {});
        return parts.year + '-' + parts.month + '-' + parts.day + 'T' + parts.hour + ':' + parts.minute + ':00';
      } catch (err) {
        /* fall through to browser-local */
      }
    }
    return LRCal.toFullISO(absDate);
  }

  /* ---- wiring ---- */

  function init() {
    var grid = document.getElementById('calendar-grid');
    if (!grid) return;

    grid.addEventListener('pointerdown', function (e) {
      if (drag) return;
      if (e.button !== undefined && e.button !== 0) return;

      var chip = e.target.closest('.cal-event');
      if (chip && chip.dataset.eventId) {
        var handle = e.target.closest('.cal-resize-handle');
        var isAllDay = chip.classList.contains('cal-allday');
        var mode = handle ? 'resize' : isAllDay ? 'allday-move' : 'move';
        beginEventDrag(e, chip, mode);
        e.preventDefault();
        return;
      }

      if (e.pointerType === 'mouse') {
        var cell = e.target.closest('.time-cell');
        if (cell && !e.target.closest('.cal-event')) {
          LRCal.quickCreate.hide();
          beginCreate(e, cell);
          e.preventDefault();
        }
      }
    });

    document.addEventListener('pointermove', function (e) {
      if (!drag) return;
      if (drag.mode === 'create') updateCreate(e);
      else updateEventDrag(e);
    });

    document.addEventListener('pointerup', function () {
      if (!drag) return;
      var d = drag;
      if (d.mode === 'create') {
        drag = null;
        finishCreateWith(d);
      } else {
        finishEventDrag();
      }
    });

    document.addEventListener('pointercancel', function () {
      if (!drag) return;
      clearTimeout(drag.holdTimer);
      if (drag.chip) {
        drag.chip.style.transform = '';
        drag.chip.style.opacity = '';
      }
      drag = null;
      LRCal.dnd.active = false;
      hideGhost();
      clearHighlights();
    });
  }

  function finishCreateWith(d) {
    var endMin = d.endMin || d.startMin + 60;
    var date = d.liveDate || d.date;
    hideGhost();
    clearHighlights();
    var firstCell = document.querySelector(
      '.time-cell[data-date="' + date + '"][data-hour="' + Math.floor(d.startMin / 60) + '"]'
    );
    var rect = firstCell ? firstCell.getBoundingClientRect() : null;
    LRCal.quickCreate.open(rect, date, d.startMin, endMin, false);
  }

  LRCal.dndInit = init;
  LRCal.dndCellAt = cellAt;
})();
