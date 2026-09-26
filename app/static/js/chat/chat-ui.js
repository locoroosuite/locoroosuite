/* Chat UI: timeline rendering, composer, uploads, dialogs, actions, sync merge. */
(function () {
  "use strict";

  const Chat = window.Chat;
  const state = Chat.state;
  const REACTIONS = ["👍", "❤️", "😂", "🎉", "👀"];

  function el(id) {
    return Chat.el(id);
  }

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text == null ? "" : String(text);
    return div.innerHTML;
  }

  function mxcToUrl(mxc, endpoint) {
    if (!mxc || mxc.indexOf("mxc://") !== 0) return null;
    const rest = mxc.slice("mxc://".length).split("/");
    if (rest.length < 2) return null;
    return "/app/chat/api/" + endpoint + "/" + encodeURIComponent(rest[0]) + "/" + encodeURIComponent(rest[1]);
  }

  /* ---------------- timeline ---------------- */

  function findRoom(roomId) {
    return state.rooms.find(function (r) {
      return r.room_id === roomId;
    });
  }

  function showEmptyState() {
    state.activeRoomId = null;
    const root = el("chat-root");
    if (root) root.classList.remove("room-open");
    el("chat-room").classList.add("hidden");
    el("chat-empty").classList.remove("hidden");
    Chat.renderRoomList();
  }

  async function openRoom(roomId) {
    if (!roomId) return;
    state.activeRoomId = roomId;
    const room = findRoom(roomId);
    Chat.renderRoomList();
    if (!room) return;
    const root = el("chat-root");
    if (root) root.classList.add("room-open");
    el("chat-empty").classList.add("hidden");
    el("chat-room").classList.remove("hidden");
    el("chat-room-name").textContent = (room.is_direct ? "" : "# ") + (room.display_name || room.name || window.LR.t("Room"));
    const peer = state.dmPeers[roomId];
    const subtitle = room.topic || (peer ? peer.email + "  ·  " + peer.matrix_user_id : "");
    el("chat-room-subtitle").textContent = subtitle;
    el("chat-room-members").textContent = room.member_count ? window.LR.t("{n} members", {n: room.member_count}) : "";
    el("chat-room-invite").classList.toggle("hidden", !!room.is_direct);
    el("chat-messages").textContent = "";
    state.messages[roomId] = [];
    try {
      const data = await Chat.api("/app/chat/api/rooms/" + encodeURIComponent(roomId) + "/messages?limit=50");
      mergeMessages(roomId, data.messages || []);
      renderTimeline(roomId);
      markRoomRead(roomId);
    } catch (err) {
      Chat.banner(window.LR.t("Could not load messages: {error}", {error: err.message}));
    }
  }

  function mergeMessages(roomId, incoming) {
    const list = (state.messages[roomId] = state.messages[roomId] || []);
    const byId = {};
    list.forEach(function (m) {
      byId[m.event_id] = m;
    });
    (incoming || []).forEach(function (m) {
      if (m.type !== "m.room.message") return;
      byId[m.event_id] = m;
    });
    state.messages[roomId] = Object.keys(byId)
      .map(function (k) {
        return byId[k];
      })
      .sort(function (a, b) {
        return a.origin_server_ts - b.origin_server_ts;
      });
    if (roomId === state.activeRoomId) {
      renderTimeline(roomId);
    }
  }

  function renderTimeline(roomId) {
    const box = el("chat-messages");
    const messages = state.messages[roomId] || [];
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    box.textContent = "";
    if (!messages.length) {
      const room = findRoom(roomId) || {};
      const peer = state.dmPeers[roomId];
      const who = room.is_direct
        ? (peer ? peer.email : room.display_name || window.LR.t("this person"))
        : (room.display_name || window.LR.t("this room"));
      const empty = document.createElement("div");
      empty.className = "h-full flex flex-col items-center justify-center text-slate-400 py-16 text-center";
      const identity = peer
        ? '<div class="text-[12px] md:text-[11px] font-mono text-slate-300 mt-1">' + escapeHtml(peer.matrix_user_id) + "</div>"
        : "";
      empty.innerHTML =
        '<svg class="h-8 w-8 mb-2" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M3.43 2.524A49.19 49.19 0 0110 2c1.657 0 3.28.088 4.86.262.897.1 1.69.757 1.69 1.654v6.168c0 .897-.793 1.554-1.69 1.654a38.53 38.53 0 01-2.34.196l-2.975 2.942a.75.75 0 01-1.28-.53v-2.39a42.36 42.36 0 01-2.835-.178c-.897-.1-1.69-.757-1.69-1.654V4.178c0-.897.793-1.554 1.69-1.654z" clip-rule="evenodd"/></svg>' +
        '<p class="text-sm">' + window.LR.t("This is the beginning of your conversation with {who}.", {who: escapeHtml(who)}) + "</p>" +
        '<p class="text-xs mt-1">' + window.LR.t("Say hello — messages appear instantly for everyone.") + "</p>" +
        identity;
      box.appendChild(empty);
      return;
    }
    let lastSender = null;
    let lastDay = null;
    messages.forEach(function (m) {
      const day = new Date(m.origin_server_ts).toDateString();
      if (day !== lastDay) {
        lastDay = day;
        lastSender = null;
        const divider = document.createElement("div");
        divider.className = "flex items-center gap-3 py-2 text-[12px] md:text-[11px] text-slate-500";
        divider.innerHTML = "<span class='flex-1 h-px bg-slate-200'></span>" + escapeHtml(day) + "<span class='flex-1 h-px bg-slate-200'></span>";
        box.appendChild(divider);
      }
      const grouped = m.sender === lastSender;
      lastSender = m.sender;
      box.appendChild(renderMessage(m, grouped));
    });
    if (nearBottom || true) {
      box.scrollTop = box.scrollHeight;
    }
  }

  const AVATAR_COLORS = [
    "bg-slate-200 text-slate-700",
    "bg-rose-100 text-rose-700",
    "bg-amber-100 text-amber-700",
    "bg-emerald-100 text-emerald-700",
    "bg-sky-100 text-sky-700",
    "bg-violet-100 text-violet-700",
  ];

  function avatarColor(sender) {
    let hash = 0;
    for (let i = 0; i < sender.length; i++) {
      hash = (hash * 31 + sender.charCodeAt(i)) & 0xffff;
    }
    return AVATAR_COLORS[hash % AVATAR_COLORS.length];
  }

  function renderMessage(m, grouped) {
    const row = document.createElement("div");
    row.className = "group/msg flex gap-3 " + (grouped ? "mt-0.5" : "mt-3");
    row.dataset.eventId = m.event_id;

    const avatar = document.createElement("div");
    avatar.className =
      "shrink-0 h-8 w-8 rounded-full text-[12px] md:text-[11px] font-semibold flex items-center justify-center " +
      avatarColor(m.sender) +
      (grouped ? " invisible" : "");
    avatar.textContent = Chat.initialsFor(m.sender);
    row.appendChild(avatar);

    const body = document.createElement("div");
    body.className = "flex-1 min-w-0";
    if (!grouped) {
      const header = document.createElement("div");
      header.className = "flex items-baseline gap-2";
      const who = document.createElement("span");
      who.className = "text-sm font-semibold text-slate-800";
      who.textContent = m.sender.replace(/^@/, "").split(":")[0];
      const when = document.createElement("span");
      when.className = "text-[12px] md:text-[11px] text-slate-500";
      when.textContent = Chat.timeLabel(m.origin_server_ts);
      header.appendChild(who);
      header.appendChild(when);
      body.appendChild(header);
    }

    const contentBox = document.createElement("div");
    contentBox.className = "text-sm text-slate-700 break-words";
    if (m.redacted) {
      contentBox.className += " italic text-slate-400";
      contentBox.textContent = window.LR.t("Message deleted");
    } else {
      appendContent(contentBox, m);
    }
    body.appendChild(contentBox);

    const reactionsRow = document.createElement("div");
    reactionsRow.className = "flex flex-wrap gap-1 mt-1";
    (m.reactions || []).forEach(function (r) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className =
        "px-1.5 py-0.5 rounded-full text-xs border " +
        (r.mine ? "bg-slate-900 text-white border-slate-900" : "bg-slate-100 text-slate-600 border-slate-200 hover:border-slate-400");
      chip.textContent = r.key + " " + r.count;
      chip.addEventListener("click", function () {
        Chat.actions.react(m.event_id, r.key);
      });
      reactionsRow.appendChild(chip);
    });
    if ((m.reactions || []).length > 0) {
      body.appendChild(reactionsRow);
    }

    const hover = document.createElement("div");
    // UX3d: hidden + non-interactive until hover (hover-capable devices only);
    // always visible on touch via .lr-touch-visible.
    hover.className = "opacity-0 pointer-events-none group-hover/msg:opacity-100 group-hover/msg:pointer-events-auto lr-touch-visible transition-opacity flex gap-1 items-center";
    REACTIONS.slice(0, 3).forEach(function (key) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "h-8 w-8 rounded hover:bg-slate-200 text-sm flex items-center justify-center";
      btn.textContent = key;
      btn.title = window.LR.t("React {key}", {key: key});
      btn.addEventListener("click", function () {
        Chat.actions.react(m.event_id, key);
      });
      hover.appendChild(btn);
    });
    if (m.sender === state.identity && !m.redacted) {
      const editBtn = document.createElement("button");
      editBtn.type = "button";
      editBtn.className = "h-8 px-2 rounded hover:bg-slate-200 text-xs text-slate-500";
      editBtn.textContent = window.LR.t("Edit");
      editBtn.addEventListener("click", function () {
        startEdit(m);
      });
      hover.appendChild(editBtn);
      const delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.className = "h-8 px-2 rounded hover:bg-slate-200 text-xs text-slate-500";
      delBtn.textContent = window.LR.t("Delete");
      delBtn.addEventListener("click", function () {
        if (window.confirm(window.LR.t("Delete this message?"))) {
          Chat.actions.redact(m.event_id);
        }
      });
      hover.appendChild(delBtn);
    }
    body.appendChild(hover);

    row.appendChild(body);
    return row;
  }

  function appendContent(contentBox, m) {
    const c = m.content || {};
    const msgtype = c.msgtype || "m.text";
    if (m.edited) {
      const tag = document.createElement("span");
      tag.className = "text-[11px] md:text-[10px] text-slate-500 align-super ml-1";
      tag.textContent = window.LR.t("(edited)");
      contentBox.appendChild(tag);
    }
    if (msgtype === "m.image" && c.url) {
      const url = mxcToUrl(c.url, "thumbnail") + "?w=640&h=480";
      const full = mxcToUrl(c.url, "media");
      const img = document.createElement("img");
      img.src = url;
      img.alt = m.body || window.LR.t("image");
      img.loading = "lazy";
      img.className = "mt-1 max-w-sm max-h-72 rounded-lg border border-slate-200 cursor-zoom-in";
      img.addEventListener("click", function () {
        window.open(full, "_blank");
      });
      contentBox.appendChild(img);
      return;
    }
    if (msgtype === "m.video" && c.url) {
      const video = document.createElement("video");
      video.src = mxcToUrl(c.url, "media");
      video.controls = true;
      video.className = "mt-1 max-w-sm rounded-lg border border-slate-200";
      contentBox.appendChild(video);
      return;
    }
    if (msgtype === "m.audio" && c.url) {
      const audio = document.createElement("audio");
      audio.src = mxcToUrl(c.url, "media");
      audio.controls = true;
      audio.className = "mt-1";
      contentBox.appendChild(audio);
      return;
    }
    if (msgtype === "m.file" && c.url) {
      const link = document.createElement("a");
      link.href = mxcToUrl(c.url, "media");
      link.className = "inline-flex items-center gap-1.5 mt-1 px-2 py-1 rounded border border-slate-200 bg-white text-slate-600 hover:bg-slate-50";
      link.setAttribute("download", m.body || "file");
      link.textContent = "📎 " + (m.body || window.LR.t("Download file")) + (c.info && c.info.size ? " (" + Math.round(c.info.size / 1024) + " KB)" : "");
      contentBox.appendChild(link);
      return;
    }
    const text = document.createElement("span");
    text.textContent = c.body != null ? c.body : m.body || "";
    if (m.edited && text.firstChild) {
      contentBox.insertBefore(text, contentBox.firstChild);
    } else {
      contentBox.appendChild(text);
    }
  }

  function startEdit(m) {
    const next = window.prompt(window.LR.t("Edit message"), (m.content || {}).body || m.body || "");
    if (next === null) return;
    const trimmed = next.trim();
    if (!trimmed) {
      Chat.banner(window.LR.t("Message cannot be empty."));
      return;
    }
    Chat.actions.edit(m.event_id, trimmed);
  }

  async function markRoomRead(roomId) {
    const messages = state.messages[roomId] || [];
    const last = messages[messages.length - 1];
    if (!last) return;
    try {
      await Chat.api("/app/chat/api/rooms/" + encodeURIComponent(roomId) + "/read", {
        method: "POST",
        json: { event_id: last.event_id },
      });
      const room = findRoom(roomId);
      if (room) {
        room.notification_count = 0;
        room.highlight_count = 0;
      }
      Chat.renderRoomList();
    } catch (err) {
      /* non-fatal: unread badge may lag */
    }
  }

  /* ---------------- composer ---------------- */

  let typingActive = false;
  let typingTimer = null;

  async function sendCurrent() {
    const input = el("chat-input");
    const body = input.value.trim();
    if (!body || !state.activeRoomId || state.sending) return;
    state.sending = true;
    const sendBtn = el("chat-send");
    sendBtn.disabled = true;
    try {
      await Chat.api("/app/chat/api/rooms/" + encodeURIComponent(state.activeRoomId) + "/send", {
        method: "POST",
        json: { body: body },
      });
      input.value = "";
      input.style.height = "auto";
      await pollRoom(state.activeRoomId);
    } catch (err) {
      Chat.banner(window.LR.t("Could not send: {error} Check your connection and retry.", {error: err.message}));
    } finally {
      state.sending = false;
      sendBtn.disabled = false;
      input.focus();
    }
  }

  async function pollRoom(roomId) {
    try {
      const data = await Chat.api("/app/chat/api/rooms/" + encodeURIComponent(roomId) + "/messages?limit=50");
      mergeMessages(roomId, data.messages || []);
      markRoomRead(roomId);
    } catch (err) {
      /* SSE will refresh shortly */
    }
  }

  function onInputChange() {
    const input = el("chat-input");
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 160) + "px";
    if (!state.activeRoomId) return;
    if (!typingActive) {
      typingActive = true;
      sendTyping(true);
    }
    window.clearTimeout(typingTimer);
    typingTimer = window.setTimeout(function () {
      typingActive = false;
      sendTyping(false);
    }, 2500);
  }

  let typingInFlight = false;
  async function sendTyping(isTyping) {
    if (!state.activeRoomId || typingInFlight) return;
    typingInFlight = true;
    try {
      await Chat.api("/app/chat/api/rooms/" + encodeURIComponent(state.activeRoomId) + "/typing", {
        method: "POST",
        json: { typing: isTyping },
      });
    } catch (err) {
      /* non-fatal */
    } finally {
      typingInFlight = false;
    }
  }

  function showTyping(roomId, users) {
    if (roomId !== state.activeRoomId) return;
    const box = el("chat-typing");
    if (!users || users.length === 0) {
      box.textContent = "";
      return;
    }
    const names = users.map(function (u) {
      return u.replace(/^@/, "").split(":")[0];
    });
    box.textContent = window.LR.t(names.length === 1 ? "{names} is typing…" : "{names} are typing…", {names: names.join(", ")});
  }

  /* ---------------- uploads ---------------- */

  async function uploadFiles(files) {
    if (!state.activeRoomId || !files || files.length === 0) return;
    const progress = el("chat-upload-progress");
    const label = el("chat-upload-label");
    progress.classList.remove("hidden");
    try {
      for (let i = 0; i < files.length; i++) {
        label.textContent = window.LR.t("Uploading {name}…", {name: files[i].name});
        const fd = new FormData();
        fd.append("file", files[i]);
        const resp = await fetch("/app/chat/api/rooms/" + encodeURIComponent(state.activeRoomId) + "/upload", {
          method: "POST",
          body: fd,
        });
        if (!resp.ok) {
          let message = window.LR.t("Upload failed ({status})", {status: resp.status});
          try {
            message = (await resp.json()).error.message;
          } catch (e) {
            /* keep default */
          }
          throw new Error(message);
        }
      }
      await pollRoom(state.activeRoomId);
    } catch (err) {
      Chat.banner(window.LR.t("Upload failed: {error} Retry or check the file size (50 MB max).", {error: err.message}));
    } finally {
      progress.classList.add("hidden");
    }
  }

  /* ---------------- actions ---------------- */

  const actions = {
    async joinRoom(roomId) {
      try {
        await Chat.api("/app/chat/api/rooms/" + encodeURIComponent(roomId) + "/join", { method: "POST" });
        const data = await Chat.api("/app/chat/api/sync-now", { method: "POST" });
        Chat.applyRooms(data.rooms || []);
        openRoom(roomId);
      } catch (err) {
        Chat.banner(window.LR.t("Could not join: {error}", {error: err.message}));
      }
    },
    async leaveRoom(roomId) {
      try {
        await Chat.api("/app/chat/api/rooms/" + encodeURIComponent(roomId) + "/leave", { method: "POST" });
        state.rooms = state.rooms.filter(function (r) {
          return r.room_id !== roomId;
        });
        if (state.activeRoomId === roomId) {
          showEmptyState();
        }
        Chat.renderRoomList();
      } catch (err) {
        Chat.banner(window.LR.t("Could not leave: {error}", {error: err.message}));
      }
    },
    async react(eventId, key) {
      try {
        await Chat.api("/app/chat/api/messages/" + encodeURIComponent(eventId) + "/react", {
          method: "POST",
          json: { key: key },
        });
        if (state.activeRoomId) pollRoom(state.activeRoomId);
      } catch (err) {
        Chat.banner(window.LR.t("Could not react: {error}", {error: err.message}));
      }
    },
    async edit(eventId, body) {
      try {
        await Chat.api("/app/chat/api/messages/" + encodeURIComponent(eventId) + "/edit", {
          method: "POST",
          json: { body: body },
        });
        if (state.activeRoomId) pollRoom(state.activeRoomId);
      } catch (err) {
        Chat.banner(window.LR.t("Could not edit: {error}", {error: err.message}));
      }
    },
    async redact(eventId) {
      try {
        await Chat.api("/app/chat/api/messages/" + encodeURIComponent(eventId) + "/redact", {
          method: "POST",
        });
        if (state.activeRoomId) pollRoom(state.activeRoomId);
      } catch (err) {
        Chat.banner(window.LR.t("Could not delete: {error}", {error: err.message}));
      }
    },
    async createRoom(name, topic, isPublic) {
      const data = await Chat.api("/app/chat/api/rooms", {
        method: "POST",
        json: { name: name, topic: topic, is_public: isPublic },
      });
      Chat.applyRooms([data.room]);
      openRoom(data.room.room_id);
    },
    async createDmByEmail(email) {
      const data = await Chat.api("/app/chat/api/dm", {
        method: "POST",
        json: { email: email },
      });
      if (data.peer) {
        state.dmPeers[data.room.room_id] = data.peer;
      }
      Chat.applyRooms([data.room]);
      openRoom(data.room.room_id);
      return data;
    },
    async inviteByEmail(roomId, email) {
      return Chat.api("/app/chat/api/rooms/" + encodeURIComponent(roomId) + "/invite", {
        method: "POST",
        json: { email: email },
      });
    },
  };

  /* ---------------- sync merge ---------------- */

  const sync = {
    handleChanges: function (changes) {
      if (changes.rooms_upserted && changes.rooms_upserted.length) {
        const roomsById = {};
        state.rooms.forEach(function (r) {
          roomsById[r.room_id] = Object.assign({}, r);
        });
        changes.rooms_upserted.forEach(function (rid) {
          if (roomsById[rid]) roomsById[rid].membership = "join";
        });
        Chat.api("/app/chat/api/state")
          .then(function (data) {
            Chat.applyRooms(data.rooms || []);
          })
          .catch(function () {
            /* SSE loop refreshes on next tick */
          });
      }
      if (changes.rooms_removed && changes.rooms_removed.length) {
        state.rooms = state.rooms.filter(function (r) {
          return changes.rooms_removed.indexOf(r.room_id) === -1;
        });
        Chat.renderRoomList();
      }
      if (changes.messages) {
        Object.keys(changes.messages).forEach(function (roomId) {
          mergeMessages(roomId, changes.messages[roomId]);
          if (roomId === state.activeRoomId) markRoomRead(roomId);
        });
      }
      if (changes.typing) {
        Object.keys(changes.typing).forEach(function (roomId) {
          showTyping(roomId, changes.typing[roomId]);
          if (roomId !== state.activeRoomId) return;
          window.setTimeout(function () {
            showTyping(roomId, []);
          }, 6000);
        });
      }
    },
    mergeMessages: mergeMessages,
  };

  /* ---------------- dialogs & wiring ---------------- */

  function wireDialogs() {
    const roomDialog = el("chat-new-room-dialog");
    el("chat-new-room").addEventListener("click", function () {
      roomDialog.showModal();
    });
    el("chat-new-room-cancel").addEventListener("click", function () {
      roomDialog.close();
    });
    el("chat-new-room-form").addEventListener("submit", function (ev) {
      ev.preventDefault();
      const name = this.name.value.trim();
      const topic = this.topic.value.trim();
      const isPublic = this.is_public.checked;
      if (!name) return;
      const btn = this.querySelector("button[value=create]");
      btn.disabled = true;
      actions
        .createRoom(name, topic, isPublic)
        .then(function () {
          roomDialog.close();
        })
        .catch(function (err) {
            Chat.banner(window.LR.t("Could not create room: {error}", {error: err.message}));
        })
        .finally(function () {
          btn.disabled = false;
        });
    });

    function dialogSubmit(formId, buttonId, actionFn) {
      const form = el(formId);
      const button = el(buttonId);
      const spinner = button ? button.querySelector("svg") : null;
      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        if (button) button.disabled = true;
        if (spinner) spinner.classList.remove("hidden");
        Promise.resolve()
          .then(actionFn)
          .catch(function (err) {
            Chat.banner(window.LR.t("Could not proceed: {error} Check the address and retry.", {error: err.message}));
          })
          .finally(function () {
            if (button) button.disabled = false;
            if (spinner) spinner.classList.add("hidden");
          });
      });
    }

    const dmDialog = el("chat-new-dm-dialog");
    el("chat-new-dm").addEventListener("click", function () {
      el("chat-dm-email").value = "";
      dmDialog.showModal();
      el("chat-dm-email").focus();
    });
    el("chat-new-dm-cancel").addEventListener("click", function () {
      dmDialog.close();
    });
    dialogSubmit("chat-dm-form", "chat-dm-start", function () {
      const email = el("chat-dm-email").value.trim();
      return actions.createDmByEmail(email).then(function (data) {
        dmDialog.close();
        const peer = data.peer || {};
        Chat.banner(
          window.LR.t("Conversation started with {peer}{matrixId}", {
            peer: peer.email || email,
            matrixId: peer.matrix_user_id ? " (" + peer.matrix_user_id + ")" : "",
          })
        );
      });
    });

    const inviteDialog = el("chat-invite-dialog");
    el("chat-room-invite").addEventListener("click", function () {
      if (!state.activeRoomId) return;
      el("chat-invite-email").value = "";
      inviteDialog.showModal();
      el("chat-invite-email").focus();
    });
    el("chat-invite-cancel").addEventListener("click", function () {
      inviteDialog.close();
    });
    dialogSubmit("chat-invite-form", "chat-invite-start", function () {
      const email = el("chat-invite-email").value.trim();
      return actions.inviteByEmail(state.activeRoomId, email).then(function (data) {
        el("chat-invite-email").value = "";
        Chat.banner(window.LR.t("Invited {email}{userId}", {email: email, userId: data && data.user_id ? " (" + data.user_id + ")" : ""}));
        pollRoom(state.activeRoomId);
      });
    });

    el("chat-room-leave").addEventListener("click", function () {
      if (state.activeRoomId && window.confirm(window.LR.t("Leave this conversation?"))) {
        actions.leaveRoom(state.activeRoomId);
      }
    });
  }

  function wireComposer() {
    const input = el("chat-input");
    el("chat-send").addEventListener("click", sendCurrent);
    input.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" && !ev.shiftKey) {
        ev.preventDefault();
        sendCurrent();
      }
    });
    input.addEventListener("input", onInputChange);

    const fileInput = el("chat-file-input");
    el("chat-attach").addEventListener("click", function () {
      fileInput.click();
    });
    fileInput.addEventListener("change", function () {
      uploadFiles(fileInput.files);
      fileInput.value = "";
    });

    const dropzone = el("chat-dropzone");
    dropzone.addEventListener("dragover", function (ev) {
      ev.preventDefault();
      dropzone.classList.add("border-slate-400");
    });
    dropzone.addEventListener("dragleave", function () {
      dropzone.classList.remove("border-slate-400");
    });
    dropzone.addEventListener("drop", function (ev) {
      ev.preventDefault();
      dropzone.classList.remove("border-slate-400");
      uploadFiles(ev.dataTransfer.files);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    wireDialogs();
    wireComposer();
    el("chat-back").addEventListener("click", showEmptyState);
    el("chat-empty-new-dm").addEventListener("click", function () {
      el("chat-new-dm").click();
    });
    el("chat-empty-new-room").addEventListener("click", function () {
      el("chat-new-room").click();
    });
  });

  Chat.ui.openRoom = openRoom;
  Chat.actions = actions;
  Chat.sync = sync;
  Chat.ui.pollRoom = pollRoom;
})();
