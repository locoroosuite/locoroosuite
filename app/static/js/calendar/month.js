/*
 * LRCal month view (U12.56, U12.11, U12.56g).
 * Google-style 6-row grid: day cells with event chips (dot + title for
 * timed, pill for all-day), today circled, "+N more" day popover, day
 * number click drills into the Day view.
 */
(function () {
  'use strict';

  var LRCal = (window.LRCal = window.LRCal || {});

  function esc(s) {
    return LRCal.escapeHtml(s);
  }

  function renderMonth(container, opts) {
    var anchor = opts.date;
    var events = opts.events || [];
    var year = anchor.getFullYear();
    var month = anchor.getMonth();
    var firstDay = new Date(year, month, 1);
    var gridStart = LRCal.startOfWeek(firstDay);
    var todayISO = LRCal.toISO(new Date());

    var dayEvents = {};
    events.forEach(function (ev) {
      var span = LRCal.eventSpan(ev);
      if (!span) return;
      var d = new Date(span.start);
      d.setHours(0, 0, 0, 0);
      var end = new Date(span.end);
      while (d < end) {
        var key = LRCal.toISO(d);
        if (!dayEvents[key]) dayEvents[key] = [];
        dayEvents[key].push({ ev: ev, span: span });
        d = LRCal.addDays(d, 1);
      }
    });

    var html = '';
    html += '<div class="grid grid-cols-7 border-b border-slate-100 bg-white">';
    LRCal.DAYS_SHORT().forEach(function (d) {
      html +=
        '<div class="py-2 text-center text-[12px] md:text-[11px] uppercase tracking-wide text-slate-500 font-medium">' +
        d + '</div>';
    });
    html += '</div>';

    html += '<div class="grid grid-cols-7">';
    for (var i = 0; i < 42; i++) {
      var d = LRCal.addDays(gridStart, i);
      var iso = LRCal.toISO(d);
      var inMonth = d.getMonth() === month;
      var isToday = iso === todayISO;
      var evts = dayEvents[iso] || [];
      html +=
        '<div class="month-day-cell min-h-[86px] md:min-h-[104px] border-b border-r border-slate-100 p-1 ' +
        (inMonth ? 'bg-white' : 'bg-slate-50/40') +
        ' cursor-pointer hover:bg-blue-50/30 transition-colors" data-date="' + iso + '">';

      // Multiday spans already pushed per-day; sort by start then summary
      evts.sort(function (a, b) {
        return a.span.start - b.span.start;
      });

      if (isToday) {
        html +=
          '<button type="button" class="month-day-number h-6 w-6 rounded-full bg-blue-600 text-white text-xs font-semibold grid place-items-center" data-date="' + iso + '">' +
          d.getDate() + '</button>';
      } else {
        html +=
          '<button type="button" class="month-day-number h-6 w-6 rounded-full text-xs font-medium ' +
          (inMonth ? 'text-slate-700 hover:bg-slate-100' : 'text-slate-300 hover:bg-slate-100') +
          ' grid place-items-center" data-date="' + iso + '">' + d.getDate() + '</button>';
      }

      var maxChips = window.matchMedia('(max-width: 767px)').matches ? 2 : 3;
      var shown = evts.slice(0, maxChips);
      shown.forEach(function (item) {
        var ev = item.ev;
        var color = ev.calendar_color || '#4285f4';
        var cancelled = ev.status === 'CANCELLED';
        if (ev.all_day) {
          html +=
            '<div class="cal-event mb-0.5 flex items-center rounded px-1.5 h-[20px] text-[11px] md:text-[11px] font-medium text-slate-900 truncate ' +
            (cancelled ? 'line-through opacity-60' : '') +
            '" style="background-color:' + LRCal.tint(color, 0.35) + ';border-left:3px solid ' + color + '" data-event-id="' + ev.id + '" data-occurrence="' + esc(ev.recurrence_date || '') + '">' +
            esc(ev.summary || window.LR.t('(no title)')) + '</div>';
        } else {
          html +=
            '<div class="cal-event mb-0.5 flex items-center gap-1 rounded px-1 h-[20px] text-[11px] md:text-[11px] truncate ' +
            (cancelled ? 'line-through opacity-60' : '') +
            '" data-event-id="' + ev.id + '" data-occurrence="' + esc(ev.recurrence_date || '') + '">' +
            '<span class="h-2 w-2 rounded-full flex-shrink-0" style="background-color:' + color + '"></span>' +
            '<span class="text-slate-500 flex-shrink-0 hidden sm:inline">' + LRCal.formatTime(item.span.start) + '</span>' +
            '<span class="text-slate-800 font-medium truncate">' + esc(ev.summary || window.LR.t('(no title)')) + '</span></div>';
        }
      });
      if (evts.length > maxChips) {
        html +=
          '<button type="button" class="month-more-link block w-full text-left pl-1 text-[11px] md:text-[10px] text-slate-500 hover:text-slate-800 font-medium" data-date="' + iso + '" data-count="' + evts.length + '">' +
          window.LR.t('+{n} more', { n: evts.length - maxChips }) + '</button>';
      }
      html += '</div>';
    }
    html += '</div>';

    container.innerHTML = html;

    container.querySelectorAll('.month-day-number').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        LRCal.state.date = LRCal.parseLocalDate(btn.dataset.date);
        LRCal.navigate('day');
      });
    });

    container.querySelectorAll('.month-more-link').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        showDayPopover(btn, btn.dataset.date, dayEvents[btn.dataset.date] || []);
      });
    });
  }

  /* U12.56g: "+N more" opens a day popover listing every event that day. */
  function showDayPopover(anchor, dateISO, evts) {
    var existing = document.getElementById('cal-day-popover');
    if (existing) existing.remove();

    var d = LRCal.parseLocalDate(dateISO);
    var pop = document.createElement('div');
    pop.id = 'cal-day-popover';
    pop.className = 'fixed z-50 w-72 max-w-[92vw] rounded-xl border border-slate-200 bg-white shadow-xl overflow-hidden';
    var html =
      '<div class="px-4 py-3 border-b border-slate-100 flex items-center justify-between">' +
      '<div><div class="text-xs uppercase tracking-wide text-slate-500">' + LRCal.DAYS_SHORT()[d.getDay()] +
      '</div><div class="text-base font-semibold text-slate-900">' + LRCal.MONTHS()[d.getMonth()] + ' ' + d.getDate() + '</div></div>' +
      '<button type="button" class="h-7 w-7 grid place-items-center rounded-lg text-slate-400 hover:bg-slate-100" aria-label="' + window.LR.t('Close') + '">&#10005;</button></div>' +
      '<div class="max-h-72 overflow-y-auto p-2 space-y-1">';
    evts.forEach(function (item) {
      var ev = item.ev;
      var color = ev.calendar_color || '#4285f4';
      var when = ev.all_day
        ? window.LR.t('All day')
        : LRCal.formatTime(item.span.start) + ' – ' + LRCal.formatTime(item.span.end);
      html +=
        '<div class="cal-event flex items-start gap-2 rounded-lg px-2 py-1.5 hover:bg-slate-50 cursor-pointer" data-event-id="' + ev.id + '" data-occurrence="' + esc(ev.recurrence_date || '') + '">' +
        '<span class="mt-1 h-2.5 w-2.5 rounded-full flex-shrink-0" style="background-color:' + color + '"></span>' +
        '<div class="min-w-0"><div class="text-sm font-medium text-slate-900 truncate ' + (ev.status === 'CANCELLED' ? 'line-through opacity-60' : '') + '">' +
        esc(ev.summary || window.LR.t('(no title)')) + '</div>' +
        '<div class="text-xs text-slate-500">' + when + '</div></div></div>';
    });
    html += '</div>';
    pop.innerHTML = html;
    document.body.appendChild(pop);

    var rect = anchor.getBoundingClientRect();
    var left = Math.min(rect.left, window.innerWidth - 300);
    pop.style.left = Math.max(8, left) + 'px';
    pop.style.top = Math.min(rect.bottom + 4, window.innerHeight - pop.offsetHeight - 8) + 'px';

    function close(e) {
      if (!pop.contains(e.target)) {
        pop.remove();
        document.removeEventListener('mousedown', close);
      }
    }
    document.addEventListener('mousedown', close);
    pop.querySelector('button').addEventListener('click', function () {
      pop.remove();
      document.removeEventListener('mousedown', close);
    });
  }

  LRCal.renderMonth = renderMonth;
})();
