/*
 * LRCal event popup (U12.56c): clicking an event shows a detail card —
 * anchored popup on desktop, bottom sheet on phones — with Edit / Delete /
 * More details actions.
 */
(function () {
  'use strict';

  var LRCal = (window.LRCal = window.LRCal || {});

  function esc(s) {
    return LRCal.escapeHtml(s);
  }

  function isPhone() {
    return window.matchMedia('(max-width: 767px)').matches;
  }

  function formatWhen(ev) {
    var span = LRCal.eventSpan(ev);
    if (!span) return '';
    var DAYS = LRCal.DAYS_SHORT();
    var MONTHS = LRCal.MONTHS();
    var s = span.start;
    var dayLabel = function (d) {
      return DAYS[d.getDay()] + ', ' + MONTHS[d.getMonth()] + ' ' + d.getDate();
    };
    if (span.allDay) {
      if (LRCal.toISO(span.start) !== LRCal.toISO(LRCal.addDays(span.end, -1))) {
        return dayLabel(span.start) + ' – ' + dayLabel(LRCal.addDays(span.end, -1));
      }
      return dayLabel(span.start);
    }
    var sameDay = LRCal.toISO(span.start) === LRCal.toISO(span.end);
    if (sameDay) {
      return dayLabel(s) + ' \u00B7 ' + LRCal.formatTime(span.start) + ' – ' + LRCal.formatTime(span.end);
    }
    return dayLabel(span.start) + ' ' + LRCal.formatTime(span.start) + ' – ' + dayLabel(span.end) + ' ' + LRCal.formatTime(span.end);
  }

  function show(ev, anchorEl) {
    hide();
    var color = ev.calendar_color || '#4285f4';
    var attendees = Array.isArray(ev.attendees) ? ev.attendees : [];
    var cancelled = ev.status === 'CANCELLED';

    var pop = document.createElement('div');
    pop.id = 'cal-event-popup';
    if (isPhone()) {
      pop.className =
        'fixed inset-x-0 bottom-0 z-50 rounded-t-2xl bg-white shadow-2xl border-t border-slate-200 max-h-[80vh] overflow-y-auto cal-sheet';
    } else {
      pop.className =
        'fixed z-50 w-80 rounded-2xl bg-white shadow-2xl border border-slate-200 overflow-hidden';
    }

    var html =
      '<div class="flex items-start gap-3 px-4 pt-4">' +
      '<span class="mt-1 h-3 w-3 rounded-full flex-shrink-0" style="background-color:' + color + '"></span>' +
      '<div class="flex-1 min-w-0">' +
      '<div class="text-base font-semibold text-slate-900 break-words ' + (cancelled ? 'line-through opacity-60' : '') + '">' +
      esc(ev.summary || window.LR.t('(no title)')) + '</div>' +
      '<div class="text-sm text-slate-600 mt-0.5">' + formatWhen(ev) + '</div>' +
      (ev.timezone ? '<div class="text-xs text-slate-400 mt-0.5">' + esc(ev.timezone) + '</div>' : '') +
      '</div>' +
      '<button type="button" id="cep-close" class="h-8 w-8 grid place-items-center rounded-lg text-slate-400 hover:bg-slate-100 flex-shrink-0" aria-label="' + window.LR.t('Close') + '">' +
      '<svg class="h-4 w-4" viewBox="0 0 20 20" fill="currentColor"><path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z"/></svg></button>' +
      '</div>';

    html += '<div class="px-4 pb-2 pt-2 space-y-1.5 text-sm">';
    if (ev.location) {
      html +=
        '<div class="flex items-start gap-2 text-slate-600"><svg class="h-4 w-4 mt-0.5 text-slate-400 flex-shrink-0" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M9.69 18.933l.003.001C9.89 19.02 10 19 10 19s.11.02.308-.066l.002-.001.006-.003.018-.008a5.741 5.741 0 00.281-.14c.186-.096.446-.24.757-.433.62-.384 1.445-.966 2.274-1.765C15.302 14.988 17 12.493 17 9A7 7 0 103 9c0 3.492 1.698 5.988 3.355 7.584a13.731 13.731 0 002.273 1.765 11.842 11.842 0 001.039.573l.018.008.006.003zM10 11.25a2.25 2.25 0 100-4.5 2.25 2.25 0 000 4.5z" clip-rule="evenodd"/></svg>' +
        '<span class="break-words">' + esc(ev.location) + '</span></div>';
    }
    if (ev.description) {
      var short = ev.description.length > 220 ? ev.description.slice(0, 220) + '…' : ev.description;
      html += '<div class="text-slate-600 whitespace-pre-line break-words text-sm">' + esc(short) + '</div>';
    }
    if (attendees.length) {
      html +=
        '<div class="flex items-start gap-2 text-slate-600"><svg class="h-4 w-4 mt-0.5 text-slate-400 flex-shrink-0" viewBox="0 0 20 20" fill="currentColor"><path d="M10 9a3 3 0 100-6 3 3 0 000 6zM6 12a3 3 0 100-6 3 3 0 000 6zM2 8a3 3 0 015.83-1H13a1 1 0 110 2H7.83A3.001 3.001 0 012 8zm14 6a3 3 0 01-5.83 1H7a1 1 0 110-2h3.17A3.001 3.001 0 0116 14zm-6-1a3 3 0 100-6 3 3 0 000 6z"/></svg>' +
        '<span>' + attendees.length + ' ' + (attendees.length === 1 ? window.LR.t('guest') : window.LR.t('guests')) + '</span></div>';
    }
    html += '</div>';

    html +=
      '<div class="flex items-center justify-between gap-2 px-4 py-3 border-t border-slate-100 bg-slate-50/50">' +
      '<a href="' + LRCal.boot.EVENT_DETAIL_URL.replace('__ID__', ev.id) + '" class="text-xs text-slate-500 hover:text-slate-800">' + window.LR.t('More details') + '</a>' +
      '<div class="flex items-center gap-2">' +
      '<button type="button" id="cep-delete" class="rounded-lg px-3 py-1.5 text-xs font-medium text-rose-600 hover:bg-rose-50">' + window.LR.t('Delete') + '</button>' +
      '<button type="button" id="cep-edit" class="rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800">' + window.LR.t('Edit') + '</button>' +
      '</div></div>';

    pop.innerHTML = html;
    document.body.appendChild(pop);

    if (!isPhone() && anchorEl) {
      var rect = anchorEl.getBoundingClientRect();
      var left = Math.min(rect.right + 8, window.innerWidth - 340);
      var top = Math.min(Math.max(8, rect.top - 8), window.innerHeight - pop.offsetHeight - 8);
      pop.style.left = Math.max(8, left) + 'px';
      pop.style.top = top + 'px';
    }

    pop.querySelector('#cep-close').addEventListener('click', hide);
    pop.querySelector('#cep-edit').addEventListener('click', function () {
      hide();
      /* fetch fresh full event (incl. reminders) for the editor */
      LRCal.setBusy(true);
      LRCal.api
        .getEvent(ev.id)
        .then(function (full) {
          LRCal.setBusy(false);
          if (full && full.id) LRCal.editor.open({ event: full });
          else LRCal.editor.open({ event: ev });
        })
        .catch(function () {
          LRCal.setBusy(false);
          LRCal.editor.open({ event: ev });
        });
    });
    pop.querySelector('#cep-delete').addEventListener('click', function () {
      if (attendees.length) {
        var modal = document.getElementById('cal-delete-modal');
        modal.dataset.eventId = String(ev.id);
        modal.classList.remove('hidden');
        pop.dataset.openPopup = '1';
      } else {
        LRCal.setBusy(true);
        LRCal.api
          .deleteEvent(ev.id, false)
          .then(function (data) {
            LRCal.setBusy(false);
            if (data && data.ok) {
              hide();
              LRCal.refresh();
            } else {
              LRCal.notifyError((data && data.error) || window.LR.t('Failed to delete event.'));
            }
          })
          .catch(function () {
            LRCal.setBusy(false);
            LRCal.notifyError(window.LR.t('Network error. Please retry.'));
          });
      }
    });

    if (!isPhone()) {
      setTimeout(function () {
        document.addEventListener('mousedown', outsideClose);
      }, 0);
    } else {
      var backdrop = document.createElement('div');
      backdrop.id = 'cal-popup-backdrop';
      backdrop.className = 'fixed inset-0 z-40 bg-slate-900/40';
      backdrop.addEventListener('click', hide);
      document.body.appendChild(backdrop);
    }
  }

  function outsideClose(e) {
    var pop = document.getElementById('cal-event-popup');
    if (pop && !pop.contains(e.target)) {
      hide();
      document.removeEventListener('mousedown', outsideClose);
    }
  }

  function hide() {
    var pop = document.getElementById('cal-event-popup');
    if (pop) pop.remove();
    var backdrop = document.getElementById('cal-popup-backdrop');
    if (backdrop) backdrop.remove();
    document.removeEventListener('mousedown', outsideClose);
  }

  LRCal.popup = { show: show, hide: hide };
})();
