/*
 * LRCal date/time utilities (U12.56).
 * Timezone-aware parsing of event dtstart/dtend into absolute Dates, plus
 * formatting helpers shared by every calendar view.
 */
(function () {
  'use strict';

  var LRCal = (window.LRCal = window.LRCal || {});

  function pad(n) {
    return n < 10 ? '0' + n : '' + n;
  }

  function toISO(d) {
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  }

  function toFullISO(d) {
    return toISO(d) + 'T' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':00';
  }

  function browserTZ() {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone;
    } catch (e) {
      return '';
    }
  }

  var MONTHS = null;
  var DAYS_SHORT = null;
  var DAYS_MINI = null;

  function initNames() {
    if (MONTHS) return;
    MONTHS = [
      window.LR.t('January'), window.LR.t('February'), window.LR.t('March'),
      window.LR.t('April'), window.LR.t('May'), window.LR.t('June'),
      window.LR.t('July'), window.LR.t('August'), window.LR.t('September'),
      window.LR.t('October'), window.LR.t('November'), window.LR.t('December'),
    ];
    DAYS_SHORT = [
      window.LR.t('Sun'), window.LR.t('Mon'), window.LR.t('Tue'), window.LR.t('Wed'),
      window.LR.t('Thu'), window.LR.t('Fri'), window.LR.t('Sat'),
    ];
    // Single letters collide across days in gettext; derive from Intl (es: D/L/M/X/J/V/S).
    // window.LR_I18N.locale carries gettext catalog names ("es_ES"); Intl needs
    // BCP-47 tags ("es-ES") — normalize, and fall back to English on bad tags.
    var locale = String((window.LR_I18N && window.LR_I18N.locale) || 'en').replace(/_/g, '-');
    var fmt;
    try {
      fmt = new Intl.DateTimeFormat(locale, { weekday: 'narrow' });
    } catch (e) {
      fmt = new Intl.DateTimeFormat('en', { weekday: 'narrow' });
    }
    DAYS_MINI = [0, 1, 2, 3, 4, 5, 6].map(function (i) {
      return fmt.format(new Date(2024, 0, 7 + i)); // 2024-01-07 = Sunday
    });
  }

  /* Parse a wall-clock ISO string in a named timezone into an absolute Date. */
  function parseTZAware(isoStr, tzName) {
    if (!isoStr) return new Date();
    if (isoStr.indexOf('+') !== -1 || isoStr.indexOf('Z') === isoStr.length - 1) {
      return new Date(isoStr);
    }
    var naive = new Date(isoStr);
    var utcGuess = Date.UTC(
      naive.getFullYear(), naive.getMonth(), naive.getDate(),
      naive.getHours(), naive.getMinutes(), naive.getSeconds()
    );
    try {
      var formatter = new Intl.DateTimeFormat('en-US', {
        timeZone: tzName,
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit',
        hour12: false,
      });
      function tzOffset(instant) {
        var parts = formatter
          .format(new Date(instant))
          .match(/(\d+)\/(\d+)\/(\d+),?\s+(\d+):(\d+):(\d+)/);
        if (!parts) return 0;
        var asUTC = Date.UTC(+parts[3], +parts[1] - 1, +parts[2], +parts[4], +parts[5], +parts[6]);
        return asUTC - instant;
      }
      var offset = tzOffset(utcGuess);
      offset = tzOffset(utcGuess - offset);
      return new Date(utcGuess - offset);
    } catch (ex) {
      return naive;
    }
  }

  /* Absolute start/end Dates for a serialized event (U8.9 TZID resolution done server-side). */
  function eventSpan(ev) {
    if (!ev.dtstart) return null;
    if (ev.all_day) {
      var s = parseLocalDate(ev.dtstart);
      var end = ev.dtend ? parseLocalDate(ev.dtend) : new Date(s.getTime() + 86400000);
      return { start: s, end: end, allDay: true };
    }
    var tzName = ev.timezone_resolved || ev.timezone;
    var start = tzName ? parseTZAware(ev.dtstart, tzName) : new Date(ev.dtstart);
    var e = ev.dtend
      ? tzName
        ? parseTZAware(ev.dtend, tzName)
        : new Date(ev.dtend)
      : new Date(start.getTime() + 3600000);
    return { start: start, end: e, allDay: false };
  }

  function parseLocalDate(s) {
    var p = s.split('-');
    return new Date(+p[0], +p[1] - 1, +p[2]);
  }

  function startOfWeek(d) {
    var r = new Date(d);
    r.setHours(0, 0, 0, 0);
    r.setDate(r.getDate() - r.getDay());
    return r;
  }

  function addDays(d, n) {
    var r = new Date(d);
    r.setDate(r.getDate() + n);
    return r;
  }

  function sameDay(a, b) {
    return toISO(a) === toISO(b);
  }

  function formatTime(d) {
    return pad(d.getHours()) + ':' + pad(d.getMinutes());
  }

  /* Hex color -> rgba() tint for Google-style event chips. */
  function tint(hex, alpha) {
    var m = /^#?([0-9a-f]{6})$/i.exec(hex || '');
    if (!m) return 'rgba(66,133,244,' + alpha + ')';
    var int = parseInt(m[1], 16);
    var r = (int >> 16) & 255;
    var g = (int >> 8) & 255;
    var b = int & 255;
    return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha + ')';
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  LRCal.pad = pad;
  LRCal.toISO = toISO;
  LRCal.toFullISO = toFullISO;
  LRCal.browserTZ = browserTZ;
  LRCal.initNames = initNames;
  LRCal.MONTHS = function () { initNames(); return MONTHS; };
  LRCal.DAYS_SHORT = function () { initNames(); return DAYS_SHORT; };
  LRCal.DAYS_MINI = function () { initNames(); return DAYS_MINI; };
  LRCal.parseTZAware = parseTZAware;
  LRCal.eventSpan = eventSpan;
  LRCal.parseLocalDate = parseLocalDate;
  LRCal.startOfWeek = startOfWeek;
  LRCal.addDays = addDays;
  LRCal.sameDay = sameDay;
  LRCal.formatTime = formatTime;
  LRCal.tint = tint;
  LRCal.escapeHtml = escapeHtml;
})();
