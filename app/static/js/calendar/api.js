/*
 * LRCal API layer (U12.56): JSON endpoints powering the calendar UI.
 * Includes a memoized range cache invalidated on navigation and SSE pushes.
 */
(function () {
  'use strict';

  var LRCal = (window.LRCal = window.LRCal || {});
  var rangeCache = {};

  function jsonFetch(url, options) {
    return fetch(url, options).then(function (r) {
      return r.json().catch(function () {
        return {};
      });
    });
  }

  function fetchEvents(start, end, force) {
    var key = start + '|' + end;
    if (!force && rangeCache[key]) return Promise.resolve(rangeCache[key]);
    var url =
      LRCal.boot.EVENTS_URL + '?start=' + encodeURIComponent(start) + '&end=' + encodeURIComponent(end);
    return jsonFetch(url)
      .then(function (data) {
        if (!Array.isArray(data)) data = [];
        rangeCache[key] = data;
        return data;
      })
      .catch(function () {
        return [];
      });
  }

  function getEvent(id) {
    return jsonFetch(LRCal.boot.EVENTS_URL + '/' + id);
  }

  function createEvent(payload) {
    return jsonFetch(LRCal.boot.EVENTS_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  }

  function updateEvent(id, payload) {
    return jsonFetch(LRCal.boot.EVENTS_URL + '/' + id, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  }

  function moveEvent(id, dtstart, dtend) {
    return jsonFetch(LRCal.boot.EVENTS_URL + '/' + id + '/move', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dtstart: dtstart, dtend: dtend }),
    });
  }

  function deleteEvent(id, sendNotification) {
    return jsonFetch(LRCal.boot.EVENT_DELETE_URL.replace('__ID__', id), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
      body: JSON.stringify({ send_notification: !!sendNotification }),
    });
  }

  function sendInvite(eventId, method) {
    return jsonFetch(LRCal.boot.SEND_INVITE_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ event_id: eventId, method: method || 'REQUEST' }),
    });
  }

  function quickCreate(payload) {
    return jsonFetch(LRCal.boot.QUICK_CREATE_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  }

  LRCal.api = {
    fetchEvents: fetchEvents,
    getEvent: getEvent,
    createEvent: createEvent,
    updateEvent: updateEvent,
    moveEvent: moveEvent,
    deleteEvent: deleteEvent,
    sendInvite: sendInvite,
    quickCreate: quickCreate,
    invalidateCache: function () {
      rangeCache = {};
    },
  };
})();
