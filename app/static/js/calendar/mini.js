/*
 * LRCal mini calendar (U12.14): sidebar month with prev/next navigation,
 * today circled, selected day highlighted, days with events dotted.
 */
(function () {
  'use strict';

  var LRCal = (window.LRCal = window.LRCal || {});

  function renderMiniCalendar(el, opts) {
    if (!el) return;
    var anchor = opts.date;
    var year = anchor.getFullYear();
    var month = anchor.getMonth();
    var todayISO = LRCal.toISO(new Date());

    var monthStart = new Date(year, month, 1);
    var monthEnd = new Date(year, month + 1, 0);

    LRCal.api
      .fetchEvents(LRCal.toISO(monthStart), LRCal.toISO(monthEnd))
      .then(function (events) {
        var eventDays = {};
        events.forEach(function (ev) {
          var span = LRCal.eventSpan(ev);
          if (!span) return;
          var d = new Date(span.start);
          d.setHours(0, 0, 0, 0);
          while (d < span.end && d <= monthEnd) {
            eventDays[LRCal.toISO(d)] = true;
            d = LRCal.addDays(d, 1);
          }
        });
        draw(el, anchor, eventDays, todayISO);
      });
  }

  function draw(el, anchor, eventDays, todayISO) {
    var year = anchor.getFullYear();
    var month = anchor.getMonth();
    var firstDay = new Date(year, month, 1).getDay();
    var daysInMonth = new Date(year, month + 1, 0).getDate();
    var selectedISO = LRCal.toISO(anchor);

    var html =
      '<div class="flex items-center justify-between px-1 mb-1.5">' +
      '<div class="text-sm font-semibold text-slate-900">' + LRCal.MONTHS()[month] + ' ' + year + '</div>' +
      '<div class="flex items-center gap-0.5">' +
      '<button type="button" id="mini-prev" class="h-7 w-7 grid place-items-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-700 lr-hit" aria-label="' + window.LR.t('Previous month') + '">' +
      '<svg class="h-4 w-4" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M12.707 5.293a1 1 0 010 1.414L9.414 10l3.293 3.293a1 1 0 01-1.414 1.414l-4-4a1 1 0 010-1.414l4-4a1 1 0 011.414 0z" clip-rule="evenodd"/></svg></button>' +
      '<button type="button" id="mini-next" class="h-7 w-7 grid place-items-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-700 lr-hit" aria-label="' + window.LR.t('Next month') + '">' +
      '<svg class="h-4 w-4" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z" clip-rule="evenodd"/></svg></button>' +
      '</div></div>';

    html += '<div class="rounded-xl border border-slate-200/80 bg-white shadow-sm p-2">';
    html += '<div class="grid grid-cols-7 text-center mb-1">';
    LRCal.DAYS_MINI().forEach(function (d) {
      html += '<div class="py-0.5 text-[11px] md:text-[10px] text-slate-500">' + d + '</div>';
    });
    html += '</div>';
    html += '<div class="grid grid-cols-7 text-center">';
    for (var i = 0; i < firstDay; i++) html += '<div></div>';
    for (var day = 1; day <= daysInMonth; day++) {
      var iso = year + '-' + LRCal.pad(month + 1) + '-' + LRCal.pad(day);
      var isToday = iso === todayISO;
      var isSelected = iso === selectedISO;
      html +=
        '<div class="py-0.5 grid place-items-center"><button data-nav-date="' + iso + '" class="mini-day relative h-7 w-7 text-[12px] md:text-[11px] rounded-full grid place-items-center ' +
        (isToday
          ? 'bg-blue-600 text-white font-semibold'
          : isSelected
            ? 'bg-slate-200 font-medium text-slate-900'
            : 'text-slate-600 hover:bg-slate-100') +
        '">' + day +
        (eventDays[iso] && !isToday
          ? '<span class="absolute bottom-0.5 h-1 w-1 rounded-full" style="background-color:' + (isSelected ? '#475569' : '#94a3b8') + '"></span>'
          : '') +
        '</button></div>';
    }
    html += '</div></div>';
    el.innerHTML = html;

    el.querySelectorAll('.mini-day').forEach(function (btn) {
      btn.addEventListener('click', function () {
        LRCal.state.date = LRCal.parseLocalDate(btn.dataset.navDate);
        LRCal.navigate(LRCal.state.view);
      });
    });
    var prev = el.querySelector('#mini-prev');
    var next = el.querySelector('#mini-next');
    if (prev) {
      prev.addEventListener('click', function () {
        LRCal.state.date = new Date(anchor.getFullYear(), anchor.getMonth() - 1, 1);
        renderMiniCalendar(el, { date: LRCal.state.date });
      });
    }
    if (next) {
      next.addEventListener('click', function () {
        LRCal.state.date = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 1);
        renderMiniCalendar(el, { date: LRCal.state.date });
      });
    }
  }

  LRCal.renderMiniCalendar = renderMiniCalendar;
})();
