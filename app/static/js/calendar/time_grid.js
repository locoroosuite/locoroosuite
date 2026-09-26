/*
 * LRCal time grid renderer (U12.56): Day, 3-day and Week views.
 * Google-style: hour gutter, tinted event chips with side-by-side overlap
 * layout (U12.56a), all-day pills lane, red dot + line "now" indicator,
 * today column tint. Grid cells keep `.time-cell` + data-date/data-hour
 * hooks for drag interactions and tests.
 */
(function () {
  'use strict';

  var LRCal = (window.LRCal = window.LRCal || {});
  var HOUR_H = 48; // px per hour

  function esc(s) {
    return LRCal.escapeHtml(s);
  }

  function renderTimeGrid(container, opts) {
    var days = opts.days;
    var events = opts.events || [];
    var now = new Date();
    var todayISO = LRCal.toISO(now);

    var timed = [];
    var allDay = [];
    events.forEach(function (ev) {
      var span = LRCal.eventSpan(ev);
      if (!span) return;
      if (span.allDay) allDay.push({ ev: ev, span: span });
      else timed.push({ ev: ev, span: span });
    });

    var isCompact = days.length >= 3;
    var gutter = days.length === 1 ? 56 : isCompact ? 36 : 48;
    /* Grid template must be inline CSS, NOT a Tailwind arbitrary class: the
     * JIT scanner only emits classes it sees as literals, so a runtime-built
     * grid-cols class silently does not exist in the compiled CSS. */
    var gridStyle =
      'grid-template-columns:' + gutter + 'px repeat(' + days.length + ',minmax(0,1fr))';

    var html = '';

    /* Sticky day header row */
    html += '<div class="grid border-b border-slate-100 bg-white" style="' + gridStyle + '">';
    html += '<div class="py-2"></div>';
    days.forEach(function (d) {
      var iso = LRCal.toISO(d);
      var isToday = iso === todayISO;
      html +=
        '<div class="py-1.5 md:py-2 text-center border-l border-slate-100 ' +
        (isToday ? 'bg-blue-50/40' : '') +
        '"><div class="text-[12px] md:text-[11px] uppercase tracking-wide ' +
        (isToday ? 'text-blue-600 font-semibold' : 'text-slate-500') +
        '">' + LRCal.DAYS_SHORT()[d.getDay()] + '</div>' +
        '<div class="mx-auto mt-0.5 h-7 w-7 md:h-8 md:w-8 grid place-items-center text-base md:text-lg font-semibold ' +
        (isToday
          ? 'rounded-full bg-blue-600 text-white'
          : 'text-slate-700') +
        '">' + d.getDate() + '</div></div>';
    });
    html += '</div>';

    /* All-day lane (Google: pills, overlap -> stacked rows) */
    var allDayRows = buildAllDayRows(allDay, days);
    html +=
      '<div class="border-b border-slate-100 bg-slate-50/30 ' +
      (allDayRows ? '' : 'h-0 overflow-hidden ') +
      '"><div class="grid" style="' + gridStyle + '">';
    html +=
      '<div class="py-1 pr-1 text-[11px] md:text-[10px] text-slate-500 text-right">' +
      (allDayRows ? window.LR.t('all-day') : '') +
      '</div>';
    days.forEach(function (d) {
      var iso = LRCal.toISO(d);
      html += '<div class="relative min-h-[26px] border-l border-slate-100 px-0.5 allday-cell" data-date="' + iso + '">';
      allDayRows.forEach(function (row) {
        var placed = row[iso];
        if (placed) {
          var color = placed.ev.calendar_color || '#4285f4';
          var continues = placed.continuesRight;
          html +=
            '<div class="cal-event cal-allday ' + (continues ? 'rounded-l' : '') +
            ' flex items-center rounded px-1.5 h-[22px] mb-0.5 text-[12px] md:text-[11px] font-medium text-slate-900 overflow-hidden cursor-pointer"' +
            ' style="background-color:' + LRCal.tint(color, 0.35) +
            ';border-left:3px solid ' + color + ';touch-action:none" data-event-id="' +
            placed.ev.id + '" data-occurrence="' + esc(placed.ev.recurrence_date || '') + '">' +
            esc(placed.ev.summary || window.LR.t('(no title)')) + '</div>';
        }
      });
      html += '</div>';
    });
    html += '</div></div>';

    /* Scrollable hour grid */
    html += '<div class="overflow-y-auto cal-scroll" style="max-height:calc(100vh - 300px)">';
    html += '<div class="grid relative cal-timegrid" style="' + gridStyle + '">';
    html += '<div class="relative">';
    for (var h = 0; h < 24; h++) {
      html +=
        '<div class="h-12 border-b border-slate-100 text-[11px] md:text-[10px] text-slate-400 text-right pr-2 pt-0.5" style="height:' + HOUR_H + 'px">' +
        (h === 0 ? '' : LRCal.pad(h) + ':00') + '</div>';
    }
    html += '</div>';
    /* Timed chips + now-line live INSIDE each day column: percentages then
     * resolve against the column itself, so no gutter-width drift (the old
     * full-width overlay computed left as % of the whole grid incl. gutter). */
    var perDayHtml = renderTimedEvents(timed, days);
    days.forEach(function (d, dayIdx) {
      var iso = LRCal.toISO(d);
      var isToday = iso === todayISO;
      html += '<div class="relative border-l ' + (isToday ? 'border-slate-200 bg-blue-50/20' : 'border-slate-200') + '">';
      for (var h2 = 0; h2 < 24; h2++) {
        html +=
          '<div class="time-cell border-b border-slate-100 relative" style="height:' + HOUR_H + 'px" data-date="' + iso + '" data-hour="' + h2 + '"></div>';
      }
      html += perDayHtml[dayIdx] || '';
      if (isToday) html += renderNowLine();
      html += '</div>';
    });

    html += '</div></div>';

    container.innerHTML = html;

    var scroll = container.querySelector('.cal-scroll');
    if (scroll) {
      scroll.scrollTop = Math.max(0, (now.getHours() - 1) * HOUR_H);
    }
    LRCal.startNowTimer();
  }

  function buildAllDayRows(allDay, days) {
    var rows = [];
    var firstISO = LRCal.toISO(days[0]);
    var lastISO = LRCal.toISO(days[days.length - 1]);
    allDay.forEach(function (item) {
      var startISO = LRCal.toISO(item.span.start);
      var endISO = LRCal.toISO(LRCal.addDays(item.span.end, -1)); // exclusive end
      var placed = false;
      for (var r = 0; r < rows.length; r++) {
        var conflict = false;
        for (var d = new Date(days[0]); d <= new Date(days[days.length - 1]); d.setDate(d.getDate() + 1)) {
          var iso = LRCal.toISO(d);
          if (iso >= startISO && iso <= endISO && rows[r][iso]) {
            conflict = true;
            break;
          }
        }
        if (!conflict) {
          for (var d2 = new Date(days[0]); d2 <= new Date(days[days.length - 1]); d2.setDate(d2.getDate() + 1)) {
            var iso2 = LRCal.toISO(d2);
            if (iso2 >= startISO && iso2 <= endISO) {
              rows[r][iso2] = { ev: item.ev, continuesRight: endISO > iso2 && iso2 !== endISO && iso2 >= firstISO };
            }
          }
          placed = true;
          break;
        }
      }
      if (!placed) {
        var row = {};
        for (var d3 = new Date(days[0]); d3 <= new Date(days[days.length - 1]); d3.setDate(d3.getDate() + 1)) {
          var iso3 = LRCal.toISO(d3);
          if (iso3 >= startISO && iso3 <= endISO) {
            row[iso3] = { ev: item.ev, continuesRight: endISO > iso3 && iso3 !== endISO && iso3 >= firstISO && iso3 <= lastISO };
          }
        }
        rows.push(row);
      }
    });
    return rows;
  }

  function renderTimedEvents(timed, days) {
    var perDay = days.map(function () {
      return '';
    });

    days.forEach(function (day, dayIdx) {
      var items = [];
      timed.forEach(function (item) {
        var start = item.span.start;
        var end = item.span.end;
        // split multi-day events at day boundaries for this column
        var dayStart = new Date(day);
        dayStart.setHours(0, 0, 0, 0);
        var dayEnd = LRCal.addDays(dayStart, 1);
        var s = Math.max(start.getTime(), dayStart.getTime());
        var e = Math.min(end.getTime(), dayEnd.getTime());
        if (e <= s) return;
        var startMin = (s - dayStart.getTime()) / 60000;
        var endMin = (e - dayStart.getTime()) / 60000;
        items.push({
          ev: item.ev,
          startMin: startMin,
          endMin: endMin,
          displayStart: new Date(s),
          continuesLeft: start < dayStart,
          continuesRight: end > dayEnd,
        });
      });
      if (!items.length) return;

      LRCal.layoutOverlaps(items);
      items.forEach(function (it) {
        var color = it.ev.calendar_color || '#4285f4';
        var top = (it.startMin / 60) * HOUR_H;
        var height = Math.max(((it.endMin - it.startMin) / 60) * HOUR_H, 18);
        /* Percentages resolve against the day column (the chip's parent). */
        var leftFrac = (it.col / it.cols).toFixed(6);
        var widthFrac = (1 / it.cols).toFixed(6);
        var solid = height < 34; // short events render as a solid chip
        var cancelled = it.ev.status === 'CANCELLED';
        perDay[dayIdx] +=
          '<div class="cal-event rounded-md px-1 py-0.5 overflow-hidden cursor-pointer ' +
          (solid ? 'font-medium text-white' : 'text-slate-900 font-medium') +
          (cancelled ? ' line-through opacity-60' : '') +
          '" style="position:absolute;pointer-events:auto;top:' + top + 'px;height:' + height + 'px;' +
          'left:calc((100% - 4px) * ' + leftFrac + ' + 2px);' +
          'width:calc((100% - 4px) * ' + widthFrac + ' - 1px);' +
          (solid
            ? 'background-color:' + color + ';'
            : 'background-color:' + LRCal.tint(color, 0.22) + ';border-left:3px solid ' + color + ';') +
          'touch-action:none" data-event-id="' + it.ev.id + '" data-occurrence="' + esc(it.ev.recurrence_date || '') + '" title="' + esc(it.ev.summary || '') + '">' +
          '<div class="text-[12px] md:text-[11px] leading-tight truncate">' +
          esc(it.ev.summary || window.LR.t('(no title)')) + '</div>' +
          (height >= 34 && !solid
            ? '<div class="text-[11px] md:text-[10px] text-slate-600 leading-tight">' +
              LRCal.formatTime(it.displayStart) + '</div>'
            : '') +
          '<div class="cal-resize-handle" style="position:absolute;left:0;right:0;bottom:0;height:8px;cursor:ns-resize"></div>' +
          '</div>';
      });
    });
    return perDay;
  }

  /* Rendered inside today's day column (caller checks isToday). */
  function renderNowLine() {
    return (
      '<div id="cal-now-line" data-px-per-hour="' + HOUR_H + '" data-top-offset="0" ' +
      'style="position:absolute;left:2px;width:calc(100% - 4px);height:2px;background-color:#ef4444;border-radius:1px">' +
      '<span style="position:absolute;left:-4px;top:50%;transform:translateY(-50%);width:9px;height:9px;border-radius:50%;background-color:#ef4444"></span>' +
      '</div>'
    );
  }

  function esc(s) {
    return LRCal.escapeHtml(s);
  }

  LRCal.renderTimeGrid = renderTimeGrid;
  LRCal.HOUR_H = HOUR_H;
})();
