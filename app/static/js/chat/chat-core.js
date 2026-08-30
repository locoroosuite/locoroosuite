/* Chat core: state, API helpers, room sidebar, SSE sync loop. */
(function () {
  "use strict";

  const state = {
    identity: null,
    rooms: [],
    activeRoomId: null,
    messages: {},
    dmPeers: {},
    typingTimers: {},
    es: null,
    sending: false,
  };

  function el(id) {
    return document.getElementById(id);
  }

  async function api(path, options) {
    const opts = Object.assign({ headers: {} }, options || {});
    if (opts.json !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.json);
      delete opts.json;
    }
    const resp = await fetch(path, opts);
    let data = null;
    try {
      data = await resp.json();
    } catch (e) {
      data = null;
    }
    if (!resp.ok) {
      const code = data && data.error ? data.error.code : "REQUEST_FAILED";
      const message = data && data.error ? data.error.message : "Request failed (" + resp.status + ")";
      const err = new Error(message);
      err.code = code;
      err.status = resp.status;
      throw err;
    }
    return data;
  }

  function banner(text, sticky) {
    const b = el("chat-banner");
    if (!text) {
      b.classList.add("hidden");
      b.textContent = "";
      return;
    }
    b.textContent = text;
    b.classList.remove("hidden");
    if (!sticky) {
      window.setTimeout(function () {
        b.classList.add("hidden");
      }, 6000);
    }
  }

  function initialsFor(name) {
    const parts = String(name || "?").replace(/^@/, "").split(/[\s._:-]+/).filter(Boolean);
    return ((parts[0] || "?")[0] + ((parts[1] || "")[0] || "")).toUpperCase();
  }

  function timeLabel(ts) {
    if (!ts) return "";
    return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function renderRoomList() {
    const roomsUl = el("chat-rooms");
    const dmsUl = el("chat-dms");
    const invitesBox = el("chat-invites");
    const invitesUl = el("chat-invites-list");
    roomsUl.textContent = "";
    dmsUl.textContent = "";
    invitesUl.textContent = "";

    const joined = state.rooms.filter(function (r) {
      return r.membership === "join" && !r.is_direct;
    });
    const dms = state.rooms.filter(function (r) {
      return r.membership === "join" && r.is_direct;
    });
    const invites = state.rooms.filter(function (r) {
      return r.membership === "invite";
    });

    function li(room, inviteMode) {
      const item = document.createElement("li");
      const active = room.room_id === state.activeRoomId;
      const badge = room.notification_count > 0;
      const label =
        room.display_name || room.name || (room.is_direct ? "Direct message" : "Unnamed room");
      const peer = room.is_direct ? state.dmPeers[room.room_id] || {} : {};
      item.className =
        "group flex items-center gap-2 px-2 py-1.5 rounded-lg cursor-pointer text-sm transition-colors " +
        (active ? "bg-slate-900 text-white" : "text-slate-700 hover:bg-slate-100");
      item.title = room.is_direct
        ? (peer.email ? peer.email + " · " : "") + (peer.matrix_user_id || label)
        : "#" + label + (room.topic ? " — " + room.topic : "");
      const avatar = document.createElement("span");
      avatar.className =
        "shrink-0 h-6 w-6 rounded-full flex items-center justify-center text-[10px] font-semibold " +
        (room.is_direct
          ? active
            ? "bg-white text-slate-900"
            : "bg-slate-200 text-slate-600"
          : active
            ? "border border-white/40 text-white"
            : "border border-slate-300 text-slate-400");
      avatar.textContent = room.is_direct ? initialsFor(peer.email || label) : "#";
      const name = document.createElement("span");
      name.className = "truncate flex-1";
      name.textContent = label;
      item.appendChild(avatar);
      item.appendChild(name);
      if (badge) {
        const dot = document.createElement("span");
        dot.className =
          "shrink-0 rounded-full px-1 text-[10px] font-semibold h-5 min-w-[1.25rem] flex items-center justify-center " +
          (active ? "bg-white text-slate-900" : room.highlight_count > 0 ? "bg-red-500 text-white" : "bg-slate-200 text-slate-700");
        dot.textContent = room.notification_count > 99 ? "99+" : String(room.notification_count);
        item.appendChild(dot);
      }
      if (inviteMode) {
        const actions = document.createElement("span");
        actions.className = "shrink-0 flex items-center gap-1";
        const accept = document.createElement("button");
        accept.className = "px-1.5 py-0.5 rounded text-[10px] bg-emerald-500 text-white";
        accept.textContent = "Accept";
        accept.addEventListener("click", function (ev) {
          ev.stopPropagation();
          window.Chat.actions.joinRoom(room.room_id);
        });
        const decline = document.createElement("button");
        decline.className = "px-1.5 py-0.5 rounded text-[10px] border border-slate-300 text-slate-500";
        decline.textContent = "Decline";
        decline.addEventListener("click", function (ev) {
          ev.stopPropagation();
          window.Chat.actions.leaveRoom(room.room_id);
        });
        actions.appendChild(accept);
        actions.appendChild(decline);
        item.appendChild(actions);
      }
      item.addEventListener("click", function () {
        if (room.membership === "invite") {
          window.Chat.actions.joinRoom(room.room_id);
        } else {
          window.Chat.ui.openRoom(room.room_id);
        }
      });
      return item;
    }

    joined.forEach(function (room) {
      roomsUl.appendChild(li(room, false));
    });
    dms.forEach(function (room) {
      dmsUl.appendChild(li(room, false));
    });
    invites.forEach(function (room) {
      invitesUl.appendChild(li(room, true));
    });

    el("chat-rooms-empty").classList.toggle("hidden", joined.length > 0);
    el("chat-dms-empty").classList.toggle("hidden", dms.length > 0);
    invitesBox.classList.toggle("hidden", invites.length === 0);
  }

  function applyRooms(rooms) {
    const byId = {};
    state.rooms.forEach(function (r) {
      byId[r.room_id] = r;
    });
    (rooms || []).forEach(function (r) {
      byId[r.room_id] = Object.assign(byId[r.room_id] || {}, r);
    });
    state.rooms = Object.keys(byId).map(function (k) {
      return byId[k];
    });
    renderRoomList();
  }

  function setSyncStatus(text, isError) {
    const node = el("chat-sync-status");
    node.textContent = text;
    node.className =
      "px-3 py-2 border-t border-slate-100 text-[11px] " + (isError ? "text-red-500" : "text-slate-400");
  }

  function startStream() {
    if (state.es) {
      state.es.close();
      state.es = null;
    }
    const es = new EventSource("/app/chat/api/stream");
    state.es = es;
    es.addEventListener("chat_ready", function () {
      setSyncStatus("Live", false);
    });
    es.addEventListener("chat_sync", function (ev) {
      setSyncStatus("Live", false);
      try {
        const changes = JSON.parse(ev.data);
        window.Chat.sync.handleChanges(changes);
      } catch (e) {
        /* ignore malformed frames */
      }
    });
    es.addEventListener("chat_error", function (ev) {
      let code = "SYNC_ERROR";
      try {
        code = JSON.parse(ev.data).code;
      } catch (e) {
        /* keep default */
      }
      setSyncStatus("Connection issue (" + code + "), retrying…", true);
    });
    es.onerror = function () {
      setSyncStatus("Reconnecting…", true);
    };
  }

  async function bootstrap() {
    const root = el("chat-root");
    const identityError = JSON.parse(root.dataset.identityError || "null");
    if (identityError) {
      banner(
        "Chat unavailable: " + identityError.message + " Refresh after fixing the configuration.",
        true
      );
      setSyncStatus("Unavailable", true);
      return;
    }
    try {
      const data = await api("/app/chat/api/sync-now", { method: "POST" });
      state.identity = data.identity ? data.identity.matrix_user_id : "";
      applyRooms(data.rooms || []);
      if (data.messages) {
        Object.keys(data.messages).forEach(function (roomId) {
          window.Chat.sync.mergeMessages(roomId, data.messages[roomId]);
        });
      }
      startStream();
    } catch (err) {
      banner("Could not load chat: " + err.message, true);
      setSyncStatus("Unavailable", true);
    }
  }

  window.Chat = {
    state: state,
    api: api,
    el: el,
    banner: banner,
    initialsFor: initialsFor,
    timeLabel: timeLabel,
    renderRoomList: renderRoomList,
    applyRooms: applyRooms,
    setSyncStatus: setSyncStatus,
    startStream: startStream,
    bootstrap: bootstrap,
    actions: {},
    ui: {},
    sync: {},
  };

  document.addEventListener("DOMContentLoaded", bootstrap);
})();
