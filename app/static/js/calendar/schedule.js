/*
 * LRCal schedule view (U12.56, U12.15): Google-style scrollable list.
 * Chronological events grouped by day with sticky headers; replaces the old
 * "agenda" view (legacy ?view=agenda URLs map here). Default on phones.
 */
(function () {
  'use strict';

  var LRCal = (window.LRCal = window.LRCal || {});
  var WINDOW_DAYS = 30;

  function esc(s) {
    return LRCal.escapeHtml(s);
  }

  function renderSchedule(container, opts) {
    var start = new Date(opts.date);
    start.setHours(0, 0, 0, 0);
    var todayISO = LRCal.toISO(new Date());

    var render = function (events) {
      events = events || [];
      if (!events.length) {
        container.innerHTML =
          '<div class="p-8 md:p-12 text-center text-sm text-slate-500">' +
          window.LR.t('No upcoming events.') + '</div>';
        if (LRCal.afterRender) LRCal.afterRender('schedule');
        return;
      }

      var byDay = {};
      var order = [];
      events.forEach(function (ev) {
        var span = LRCal.eventSpan(ev);
        if (!span) return;
        var key = LRCal.toISO(span.start);
        if (!byDay[key]) {
          byDay[key] = [];
          order.push(key);
        }
        byDay[key].push({ ev: ev, span: span });
      });
      order.sort();

      var html = '<div class="divide-y divide-slate-100">';
      order.forEach(function (key) {
        var d = LRCal.parseLocalDate(key);
        var isToday = key === todayISO;
        html +=
          '<div class="px-3 md:px-5 py-2 md:py-2.5 bg-slate-50/80 text-xs font-semibold ' +
          (isToday ? 'text-blue-600' : 'text-slate-600') + '">' +
          LRCal.DAYS_SHORT()[d.getDay()] + ', ' + LRCal.MONTHS()[d.getMonth()] + ' ' + d.getDate() +
          (d.getFullYear() !== new Date().getFullYear() ? ', ' + d.getFullYear() : '') +
          (isToday ? ' · ' + window.LR.t('Today') : '') + '</div>';

        byDay[key].forEach(function (item) {
          var ev = item.ev;
          var color = ev.calendar_color || '#4285f4';
          var timeStr = ev.all_day
            ? window.LR.t('All day')
            : LRCal.formatTime(item.span.start) + ' – ' + LRCal.formatTime(item.span.end);
          html +=
            '<div class="cal-event flex items-center gap-3 md:gap-4 px-3 md:px-5 py-3 md:py-3.5 hover:bg-slate-50/60 transition-colors cursor-pointer" data-event-id="' + ev.id + '" data-occurrence="' + esc(ev.recurrence_date || '') + '">' +
            '<span class="w-1.5 h-9 rounded-full flex-shrink-0" style="background-color:' + color + '"></span>' +
            '<div class="flex-1 min-w-0">' +
            '<div class="text-sm font-medium text-slate-900 truncate ' + (ev.status === 'CANCELLED' ? 'line-through opacity-60' : '') + '">' +
            esc(ev.summary || window.LR.t('(no title)')) + '</div>' +
            (ev.location ? '<div class="text-xs text-slate-500 truncate">' + esc(ev.location) + '</div>' : '') +
            '</div>' +
            '<div class="text-xs text-slate-500 flex-shrink-0 tabular-nums">' + timeStr + '</div>' +
            '</div>';
        });
      });
      html += '</div>';

      container.innerHTML = html;
      if (LRCal.afterRender) LRCal.afterRender('schedule');
    };

    if (opts.events) {
      render(opts.events);
    } else {
      LRCal.api
        .fetchEvents(LRCal.toISO(start), LRCal.toISO(LRCal.addDays(start, WINDOW_DAYS)))
        .then(render)
        .catch(function () {
          render([]);
        });
    }
  }

  LRCal.renderSchedule = renderSchedule;
})();
