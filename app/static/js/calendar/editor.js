/*
 * LRCal editor (U12.56b): event create/edit as a centered dialog (desktop)
 * or bottom sheet (mobile). Includes the quick-create popover and the
 * iMIP "send updates / notify guests" confirm (U12.25).
 */
(function () {
  'use strict';

  var LRCal = (window.LRCal = window.LRCal || {});
  var chipsInstance = null;
  var editingEvent = null;

  function esc(s) {
    return LRCal.escapeHtml(s);
  }

  function el(id) {
    return document.getElementById(id);
  }

  function timeOptions() {
    var out = '';
    for (var h = 0; h < 24; h++) {
      [0, 15, 30, 45].forEach(function (m) {
        var v = LRCal.pad(h) + ':' + LRCal.pad(m);
        out += '<option value="' + v + '">' + v + '</option>';
      });
    }
    return out;
  }

  function tzOptions(selected) {
    var zones = LRCal.boot.TIMEZONE_OPTIONS || [];
    var out = '';
    zones.forEach(function (tz) {
      out += '<option value="' + esc(tz) + '"' + (tz === selected ? ' selected' : '') + '>' + esc(tz) + '</option>';
    });
    return out;
  }

  function calendarOptions(selectedId) {
    var out = '';
    var chosen = false;
    (LRCal.boot.CALENDARS || []).forEach(function (cal) {
      var isSel = String(cal.id) === String(selectedId) || (!selectedId && cal.is_default && !chosen);
      if (isSel) chosen = true;
      out += '<option value="' + cal.id + '"' + (isSel ? ' selected' : '') + '>' + esc(cal.displayname) + '</option>';
    });
    return out;
  }

  function buildAttendeesField(initialJson) {
    var wrap = el('ce-attendees-wrap');
    wrap.innerHTML =
      '<input type="hidden" name="attendees" id="cal-attendees-hidden" value=\'' + (initialJson || '[]') + '\' />' +
      '<div class="relative">' +
      '<div class="w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm shadow-sm focus-within:border-slate-300 focus-within:ring-2 focus-within:ring-slate-200 min-h-[38px] flex flex-wrap items-center gap-1.5 cursor-text" data-chips-container="cal-attendees">' +
      '<input type="text" class="flex-1 min-w-[140px] border-0 bg-transparent p-0.5 text-sm outline-none focus:ring-0 placeholder:text-slate-400" placeholder="' + window.LR.t('Type a name or email…') + '" data-chip-input autocomplete="off" />' +
      '</div>' +
      '<div class="hidden absolute left-0 right-0 top-full mt-1 z-50 bg-white rounded-lg border border-slate-200 shadow-lg max-h-56 overflow-y-auto" data-dropdown="cal-attendees"></div>' +
      '</div>';
    if (chipsInstance) {
      /* old instance bound to removed DOM; replace */
      chipsInstance = null;
    }
    chipsInstance = new RecipientChips('cal-attendees', {
      searchUrl: LRCal.boot.CONTACTS_SEARCH_URL,
      parseInitial: function (value) {
        try {
          var list = JSON.parse(value || '[]');
          return list.map(function (a) {
            return {
              name: a.cn || '',
              email: a.email,
              extra: {
                cn: a.cn || a.email,
                role: a.role || 'REQ-PARTICIPANT',
                partstat: a.partstat || 'NEEDS-ACTION',
                rsvp: a.rsvp || 'TRUE',
              },
            };
          });
        } catch (e) {
          return [];
        }
      },
      serializeChips: function (chipData) {
        return JSON.stringify(
          chipData.map(function (c) {
            return {
              email: c.email,
              cn: (c.extra && c.extra.cn) || c.name || c.email,
              role: (c.extra && c.extra.role) || 'REQ-PARTICIPANT',
              partstat: (c.extra && c.extra.partstat) || 'NEEDS-ACTION',
              rsvp: (c.extra && c.extra.rsvp) || 'TRUE',
            };
          })
        );
      },
      defaultExtra: { cn: '', role: 'REQ-PARTICIPANT', partstat: 'NEEDS-ACTION', rsvp: 'TRUE' },
    });
  }

  function attendeesJson() {
    var hidden = el('cal-attendees-hidden');
    if (!hidden || !hidden.value) return [];
    try {
      var v = JSON.parse(hidden.value);
      return Array.isArray(v) ? v : [];
    } catch (e) {
      return [];
    }
  }

  function splitDT(iso) {
    if (!iso) return ['', '09:00'];
    if (iso.length === 10) return [iso, '09:00'];
    var m = iso.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/);
    if (m) return [m[1], m[2]];
    return [iso.slice(0, 10), '09:00'];
  }

  /* Convert event dt (event frame) -> browser-local date+time inputs. */
  function eventToFormDates(ev) {
    var span = LRCal.eventSpan(ev);
    if (!span) return { sd: '', st: '09:00', ed: '', et: '10:00' };
    var s = splitDT(LRCal.toISO(span.start) + 'T' + LRCal.formatTime(span.start));
    var e = splitDT(LRCal.toISO(span.end) + 'T' + LRCal.formatTime(span.end));
    return { sd: s[0], st: s[1], ed: e[0], et: e[1] };
  }

  function open(opts) {
    opts = opts || {};
    var isEdit = !!(opts.event && opts.event.id);
    editingEvent = isEdit ? opts.event : null;

    var defaults = { sd: '', st: '09:00', ed: '', et: '10:00' };
    if (opts.dtstart) {      var s = splitDT(opts.dtstart);
      defaults.sd = s[0];
      defaults.st = s[1];
      if (opts.dtend) {
        var e = splitDT(opts.dtend);
        defaults.ed = e[0];
        defaults.et = e[1];
      } else {
        var endMin = parseInt(s[1].slice(0, 2), 10) * 60 + parseInt(s[1].slice(3), 10) + 60;
        defaults.ed = s[0];
        defaults.et = LRCal.pad(Math.floor(endMin / 60) % 24) + ':' + LRCal.pad(endMin % 60);
      }
    }

    var ev = opts.event || {};
    var dates = isEdit ? eventToFormDates(ev) : defaults;
    var allDay = isEdit ? !!ev.all_day : !!opts.allDay;
    el('ce-title-label').textContent = isEdit ? window.LR.t('Edit Event') : window.LR.t('New Event');

    el('ce-title').value = ev.summary || opts.summary || '';
    el('ce-calendar').innerHTML = calendarOptions(
      isEdit ? ev.calendar_id : opts.calendarId || ''
    );
    el('ce-allday').checked = allDay;
    el('ce-start-date').value = dates.sd;
    el('ce-start-time').value = dates.st;
    el('ce-end-date').value = dates.ed;
    el('ce-end-time').value = dates.et;
    el('ce-location').value = ev.location || '';
    el('ce-description').value = ev.description || '';
    el('ce-rrule').value = ev.rrule || '';
    var reminder = '';
    if (isEdit && ev.reminders && ev.reminders.length) reminder = ev.reminders[0].trigger_val || ev.reminders[0].trigger || '';
    el('ce-reminder').value = reminder;
    el('ce-status').value = ev.status || 'CONFIRMED';
    el('ce-class').value = ev.class || ev.class_ || 'PUBLIC';

    var tz = ev.timezone || LRCal.boot.USER_TIMEZONE || '';
    el('ce-timezone').innerHTML = tzOptions(tz);
    el('ce-tz-toggle').textContent = tz || window.LR.t('Timezone');
    el('ce-tz-section').classList.add('hidden');

    var initialAttendees = '[]';
    if (isEdit && Array.isArray(ev.attendees)) initialAttendees = JSON.stringify(ev.attendees);
    else if (opts.attendee) {
      initialAttendees = JSON.stringify([
        { email: opts.attendee, cn: opts.attendee, role: 'REQ-PARTICIPANT', partstat: 'NEEDS-ACTION', rsvp: 'TRUE' },
      ]);
    }
    buildAttendeesField(initialAttendees);

    el('ce-delete').classList.toggle('hidden', !isEdit);
    el('ce-save-label').textContent = isEdit ? window.LR.t('Save') : window.LR.t('Create Event');
    el('ce-error').classList.add('hidden');
    el('ce-save').disabled = false;
    el('ce-spinner').classList.add('hidden');

    toggleAllDay();

    var modal = el('cal-editor');
    modal.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
    requestAnimationFrame(function () {
      modal.classList.add('cal-sheet-open');
    });
    setTimeout(function () {
      el('ce-title').focus();
    }, 80);
  }

  function close() {
    var modal = el('cal-editor');
    if (!modal || modal.classList.contains('hidden')) return;
    modal.classList.remove('cal-sheet-open');
    setTimeout(function () {
      modal.classList.add('hidden');
      document.body.style.overflow = '';
    }, 180);
  }

  function toggleAllDay() {
    var allDay = el('ce-allday').checked;
    el('ce-start-time').classList.toggle('hidden', allDay);
    el('ce-end-time').classList.toggle('hidden', allDay);
  }

  function syncEndFromStart() {
    var sd = el('ce-start-date').value;
    var st = el('ce-start-time').value;
    var ed = el('ce-end-date').value;
    var et = el('ce-end-time').value;
    if (!sd || !st) return;
    var startMin = parseInt(st.slice(0, 2), 10) * 60 + parseInt(st.slice(3), 10);
    var endMin = startMin + 60;
    var endDate = ed;
    if (!endDate || endDate === sd) {
      if (endMin >= 1440) {
        endMin -= 1440;
        endDate = LRCal.toISO(LRCal.addDays(LRCal.parseLocalDate(sd), 1));
      } else if (!endDate) {
        endDate = sd;
      }
    }
    if (!ed) el('ce-end-date').value = endDate;
    if (!et || et === st) {
      el('ce-end-time').value = LRCal.pad(Math.floor(endMin / 60) % 24) + ':' + LRCal.pad(endMin % 60);
    }
  }

  function collectPayload() {
    var allDay = el('ce-allday').checked;
    return {
      summary: el('ce-title').value.trim(),
      calendar_id: parseInt(el('ce-calendar').value, 10),
      all_day: allDay,
      dtstart_date: el('ce-start-date').value,
      dtstart_time: el('ce-start-time').value,
      dtend_date: el('ce-end-date').value || el('ce-start-date').value,
      dtend_time: el('ce-end-time').value,
      timezone: allDay ? '' : el('ce-timezone').value,
      location: el('ce-location').value.trim(),
      description: el('ce-description').value.trim(),
      rrule: el('ce-rrule').value,
      reminder_trigger: el('ce-reminder').value,
      status: el('ce-status').value,
      class_: el('ce-class').value,
      attendees: attendeesJson(),
    };
  }

  function save() {
    var payload = collectPayload();
    if (!payload.summary) {
      showError(window.LR.t('Event title is required.'));
      return;
    }
    if (!payload.dtstart_date) {
      showError(window.LR.t('Start date is required.'));
      return;
    }

    var btn = el('ce-save');
    btn.disabled = true;
    el('ce-spinner').classList.remove('hidden');
    el('ce-error').classList.add('hidden');

    var req = editingEvent
      ? LRCal.api.updateEvent(editingEvent.id, payload)
      : LRCal.api.createEvent(payload);

    req
      .then(function (data) {
        btn.disabled = false;
        el('ce-spinner').classList.add('hidden');
        if (data && data.ok) {
          close();
          LRCal.refresh();
          if (data.notify_guests && data.event_id) {
            LRCal.promptSendUpdates(data.event_id);
          }
        } else {
          showError((data && data.error) || window.LR.t('Failed to save event. Please check your connection and retry.'));
        }
      })
      .catch(function () {
        btn.disabled = false;
        el('ce-spinner').classList.add('hidden');
        showError(window.LR.t('Network error. Please retry.'));
      });
  }

  function showError(msg) {
    var errEl = el('ce-error');
    errEl.textContent = msg;
    errEl.classList.remove('hidden');
  }

  /* U12.25: ask the organizer whether to email guests after save/move. */
  LRCal.promptSendUpdates = function (eventId) {
    var modal = el('cal-guests-modal');
    if (!modal) return;
    modal.dataset.eventId = eventId;
    modal.classList.remove('hidden');
  };

  function answerSendUpdates(send) {
    var modal = el('cal-guests-modal');
    var eventId = parseInt(modal.dataset.eventId, 10);
    modal.classList.add('hidden');
    if (!send || !eventId) return;
    LRCal.setBusy(true);
    LRCal.api
      .sendInvite(eventId, 'REQUEST')
      .then(function () {
        LRCal.setBusy(false);
      })
      .catch(function () {
        LRCal.setBusy(false);
        LRCal.notifyError(window.LR.t('Failed to send invitation.'));
      });
  }

  /* U12.25: delete + optional guest notification. */
  function deleteWithGuestsPrompt() {
    if (!editingEvent) return;
    var hasGuests = Array.isArray(editingEvent.attendees) && editingEvent.attendees.length > 0;
    doDelete(hasGuests ? null : false, hasGuests);
  }

  function doDelete(sendNotification, ask) {
    if (ask) {
      var modal = el('cal-delete-modal');
      modal.dataset.send = sendNotification === null ? '' : '0';
      modal.classList.remove('hidden');
      modal.dataset.mode = 'editor';
      return;
    }
    LRCal.setBusy(true);
    LRCal.api
      .deleteEvent(editingEvent.id, !!sendNotification)
      .then(function (data) {
        LRCal.setBusy(false);
        if (data && data.ok) {
          close();
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

  /* ---- quick-create popover (U12.19) ---- */

  var qc = {
    open: function (anchorRect, date, startMin, endMin, allDay) {
      var popover = el('quick-create-popover');
      if (!popover) return;
      var calSelect = el('qc-calendar');
      calSelect.innerHTML = calendarOptions('');
      if (!calSelect.options.length) return;

      var d = LRCal.parseLocalDate(date);
      var timeDisplay = el('qc-time-display');
      if (allDay) {
        timeDisplay.textContent =
          LRCal.DAYS_SHORT()[d.getDay()] + ', ' + LRCal.MONTHS()[d.getMonth()] + ' ' + d.getDate() + ' · ' + window.LR.t('All day');
      } else {
        timeDisplay.textContent =
          LRCal.DAYS_SHORT()[d.getDay()] + ', ' + LRCal.MONTHS()[d.getMonth()] + ' ' + d.getDate() +
          ' \u00B7 ' + minutesLabel(startMin) + ' \u2013 ' + minutesLabel(endMin);
      }

      popover.dataset.date = date;
      popover.dataset.startMin = String(Math.round(startMin));
      popover.dataset.endMin = String(Math.round(endMin));
      popover.dataset.allDay = allDay ? '1' : '0';

      el('qc-summary').value = '';
      el('qc-error').classList.add('hidden');
      el('qc-save').disabled = false;
      el('qc-spinner').classList.add('hidden');

      popover.classList.remove('hidden');
      var left = anchorRect ? anchorRect.right + 8 : 40;
      var top = anchorRect ? anchorRect.top : 40;
      popover.style.left = left + 'px';
      popover.style.top = top + 'px';
      requestAnimationFrame(function () {
        var popRect = popover.getBoundingClientRect();
        if (left + popRect.width > window.innerWidth - 8) left = (anchorRect ? anchorRect.left : 0) - popRect.width - 8;
        if (left < 8) left = 8;
        if (top + popRect.height > window.innerHeight - 8) top = window.innerHeight - popRect.height - 8;
        if (top < 8) top = 8;
        popover.style.left = left + 'px';
        popover.style.top = top + 'px';
      });
      setTimeout(function () {
        el('qc-summary').focus();
      }, 50);
    },

    hide: function () {
      var popover = el('quick-create-popover');
      if (popover) popover.classList.add('hidden');
      document.querySelectorAll('.qc-highlighted').forEach(function (n) {
        n.classList.remove('qc-highlighted');
      });
    },

    save: function () {
      var popover = el('quick-create-popover');
      var summary = el('qc-summary').value.trim() || window.LR.t('(no title)');
      var calendarId = el('qc-calendar').value;
      var date = popover.dataset.date;
      var startMin = parseInt(popover.dataset.startMin, 10) || 0;
      var endMin = parseInt(popover.dataset.endMin, 10) || startMin + 60;
      var allDay = popover.dataset.allDay === '1';

      var saveBtn = el('qc-save');
      saveBtn.disabled = true;
      el('qc-spinner').classList.remove('hidden');
      el('qc-error').classList.add('hidden');

      var dtstart, dtend;
      if (allDay) {
        dtstart = date;
        dtend = '';
      } else {
        dtstart = date + 'T' + minutesLabel(startMin) + ':00';
        dtend = date + 'T' + minutesLabel(endMin) + ':00';
      }

      LRCal.api
        .quickCreate({
          summary: summary,
          dtstart: dtstart,
          dtend: dtend,
          calendar_id: parseInt(calendarId, 10),
          all_day: allDay,
          timezone: LRCal.browserTZ(),
        })
        .then(function (data) {
          if (data && data.ok) {
            qc.hide();
            LRCal.refresh();
          } else {
            qcError((data && data.error) || window.LR.t('Failed to create event.'));
          }
        })
        .catch(function () {
          qcError(window.LR.t('Network error. Please retry.'));
        });
    },

    moreOptions: function () {
      var popover = el('quick-create-popover');
      var allDay = popover.dataset.allDay === '1';
      var date = popover.dataset.date;
      var startMin = parseInt(popover.dataset.startMin, 10) || 0;
      var endMin = parseInt(popover.dataset.endMin, 10) || startMin + 60;
      var summary = el('qc-summary').value.trim();
      var calId = el('qc-calendar').value;
      qc.hide();
      open({
        summary: summary,
        calendarId: calId,
        allDay: allDay,
        dtstart: allDay ? date : date + 'T' + minutesLabel(startMin),
        dtend: allDay ? '' : date + 'T' + minutesLabel(endMin),
      });
    },
  };

  function minutesLabel(min) {
    min = Math.max(0, Math.min(1439, Math.round(min)));
    return LRCal.pad(Math.floor(min / 60)) + ':' + LRCal.pad(min % 60);
  }

  function qcError(msg) {
    var errEl = el('qc-error');
    errEl.textContent = msg;
    errEl.classList.remove('hidden');
    el('qc-save').disabled = false;
    el('qc-spinner').classList.add('hidden');
  }

  /* ---- month view empty-cell click: all-day quick create ---- */
  function wireMonthQuickCreate() {
    var grid = document.getElementById('calendar-grid');
    if (!grid) return;
    grid.addEventListener('click', function (e) {
      var cell = e.target.closest('.month-day-cell');
      if (!cell || e.target.closest('.cal-event') || e.target.closest('.month-day-number') || e.target.closest('.month-more-link')) return;
      qc.hide();
      cell.classList.add('qc-highlighted');
      qc.open(cell.getBoundingClientRect(), cell.dataset.date, 0, 0, true);
    });
    /* touch: tap an empty time cell -> quick create for that hour */
    if (window.matchMedia('(pointer: coarse)').matches) {
      grid.addEventListener('click', function (e) {
        var cell = e.target.closest('.time-cell');
        if (!cell || e.target.closest('.cal-event')) return;
        qc.hide();
        cell.classList.add('qc-highlighted');
        var startHour = parseInt(cell.dataset.hour, 10);
        qc.open(cell.getBoundingClientRect(), cell.dataset.date, startHour * 60, Math.min(1440, (startHour + 1) * 60), false);
      });
    }
  }

  function performDelete(send) {
    var eventId = editingEvent
      ? editingEvent.id
      : parseInt(el('cal-delete-modal').dataset.eventId, 10);
    if (!eventId) return;
    LRCal.setBusy(true);
    LRCal.api
      .deleteEvent(eventId, send)
      .then(function (data) {
        LRCal.setBusy(false);
        if (data && data.ok) {
          close();
          LRCal.popup.hide();
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

  function init() {
    var startSel = el('ce-start-time');
    var endSel = el('ce-end-time');
    startSel.innerHTML = timeOptions();
    endSel.innerHTML = timeOptions();

    el('ce-close').addEventListener('click', close);
    el('ce-cancel').addEventListener('click', close);
    el('ce-save').addEventListener('click', save);
    el('ce-delete').addEventListener('click', deleteWithGuestsPrompt);
    el('ce-allday').addEventListener('change', toggleAllDay);
    el('ce-start-date').addEventListener('change', function () {
      if (!el('ce-end-date').value) syncEndFromStart();
    });
    el('ce-start-time').addEventListener('change', syncEndFromStart);
    el('ce-tz-toggle').addEventListener('click', function () {
      var section = el('ce-tz-section');
      var openState = !section.classList.contains('hidden');
      if (openState) {
        section.classList.add('hidden');
        el('ce-tz-toggle').textContent = el('ce-timezone').value || window.LR.t('Timezone');
      } else {
        section.classList.remove('hidden');
        el('ce-tz-toggle').textContent = window.LR.t('Hide timezone');
      }
    });
    el('cal-editor').addEventListener('mousedown', function (e) {
      if (e.target === el('cal-editor') || e.target.hasAttribute('data-cal-backdrop')) close();
    });

    el('qc-form').addEventListener('submit', function (e) {
      e.preventDefault();
      qc.save();
    });
    el('qc-more-options').addEventListener('click', function (e) {
      e.preventDefault();
      qc.moreOptions();
    });

    el('cal-guests-yes').addEventListener('click', function () {
      answerSendUpdates(true);
    });
    el('cal-guests-no').addEventListener('click', function () {
      answerSendUpdates(false);
    });

    el('cal-delete-notify').addEventListener('click', function () {
      el('cal-delete-modal').classList.add('hidden');
      performDelete(true);
    });
    el('cal-delete-silent').addEventListener('click', function () {
      el('cal-delete-modal').classList.add('hidden');
      performDelete(false);
    });
    el('cal-delete-cancel').addEventListener('click', function () {
      el('cal-delete-modal').classList.add('hidden');
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        qc.hide();
        close();
        LRCal.popup.hide();
      }
    });

    wireMonthQuickCreate();
    LRCal.quickCreate = qc;
  }

  LRCal.editor = { open: open, close: close };
  LRCal.editorInit = init;
})();
