/* Chat calls: 1:1 WebRTC voice/video over Matrix m.call.* signaling
   (HLD U25.19-U25.23). Signaling is proxied through session-authenticated
   module endpoints; media flows peer-to-peer and never touches the server. */
(function () {
  "use strict";

  const Chat = window.Chat;
  const state = Chat.state;

  const calls = {
    enabled: false,
    iceServers: [],
    /* roomId -> { callId -> summary } (HLD U25.20 timeline entries). */
    summaries: {},
  };

  let active = null; /* {roomId, callId, role, pc, localStream, video, connectedAt, timer, remoteCandidates} */
  let incoming = null; /* {roomId, callId, sender, offer, video, timer, candidates} */
  let ownCandidates = []; /* own ICE candidates queued until the invite POST returns */

  const INVITE_TIMEOUT_MS = 60000;

  function el(id) {
    return document.getElementById(id);
  }

  function roomById(roomId) {
    return state.rooms.find(function (r) {
      return r.room_id === roomId;
    });
  }

  function peerName(roomId) {
    const room = roomById(roomId);
    return (room && (room.display_name || room.name)) || window.LR.t("Direct message");
  }

  function callUrl(roomId, callId, suffix) {
    let url = "/app/chat/api/rooms/" + encodeURIComponent(roomId) + "/call";
    if (callId) url += "/" + encodeURIComponent(callId);
    if (suffix) url += "/" + suffix;
    return url;
  }

  /* Inline banner action opening the device-test dialog (HLD U25.59). */
  function testDevicesAction() {
    return {
      label: window.LR.t("Test your devices"),
      fn: function () {
        if (window.Chat.deviceTest) window.Chat.deviceTest.open();
      },
    };
  }

  function fmtDuration(totalSeconds) {
    const s = Math.max(0, Math.floor(totalSeconds));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    const mm = String(m).padStart(2, "0");
    const ss = String(sec).padStart(2, "0");
    return h > 0 ? h + ":" + mm + ":" + ss : m + ":" + ss;
  }

  /* ---------------- config & header buttons ---------------- */

  async function init() {
    try {
      const data = await Chat.api("/app/chat/api/turn");
      calls.enabled = !!data.enabled;
      calls.iceServers = data.iceServers || [];
    } catch (err) {
      /* TURN unconfigured or unreachable: calls stay off (fail-early, U25.21). */
      calls.enabled = false;
      calls.iceServers = [];
    }
    updateHeader();
  }

  function updateHeader() {
    const room = state.activeRoomId ? roomById(state.activeRoomId) : null;
    const show = !!(calls.enabled && room && room.is_direct && room.membership === "join");
    const busy = !!(active || incoming);
    ["chat-call-audio", "chat-call-video"].forEach(function (id) {
      const btn = el(id);
      if (!btn) return;
      btn.classList.toggle("hidden", !show);
      btn.disabled = busy;
      btn.classList.toggle("opacity-40", busy && show);
    });
  }

  /* ---------------- summaries (timeline entries) ---------------- */

  function ensureSummaryStore(roomId) {
    if (!calls.summaries[roomId]) calls.summaries[roomId] = {};
    return calls.summaries[roomId];
  }

  function mergeSummaries(roomId, list) {
    const store = ensureSummaryStore(roomId);
    (list || []).forEach(function (s) {
      if (s && s.call_id) store[s.call_id] = s;
    });
    if (roomId === state.activeRoomId && window.Chat.ui.renderTimeline) {
      window.Chat.ui.renderTimeline(roomId);
    }
  }

  function touchSummary(roomId, ev) {
    const store = ensureSummaryStore(roomId);
    const entry = (store[ev.call_id] = store[ev.call_id] || {
      call_id: ev.call_id,
      sender: ev.sender,
      invite_ts: ev.origin_server_ts,
      answer_ts: null,
      hangup_ts: null,
      hangup_sender: null,
      hangup_reason: null,
      video: false,
      status: "ringing",
      duration_s: null,
    });
    const content = ev.content || {};
    if (ev.type === "m.call.invite") {
      entry.sender = ev.sender;
      entry.invite_ts = ev.origin_server_ts;
      entry.video = !!content["lr.video"] || String((content.offer || {}).sdp || "").indexOf("m=video") !== -1;
      /* Never downgrade a terminal status (busy auto-decline may already
         have recorded a hangup before this invite echo is processed). */
      if (entry.answer_ts == null && entry.hangup_ts == null) entry.status = "ringing";
    } else if (ev.type === "m.call.answer") {
      entry.answer_ts = ev.origin_server_ts;
      if (entry.hangup_ts == null) entry.status = "active";
    } else if (ev.type === "m.call.hangup") {
      entry.hangup_ts = ev.origin_server_ts;
      entry.hangup_sender = ev.sender;
      entry.hangup_reason = content.reason || null;
      if (entry.answer_ts != null) {
        entry.status = "ended";
        entry.duration_s = Math.max(0, Math.floor((entry.hangup_ts - entry.answer_ts) / 1000));
      } else {
        entry.status = entry.hangup_sender !== entry.sender ? "declined" : "missed";
      }
    }
  }

  function timelineEntries(roomId) {
    const store = calls.summaries[roomId] || {};
    return Object.keys(store)
      .map(function (k) {
        return store[k];
      })
      .filter(function (s) {
        return s.invite_ts != null;
      })
      .sort(function (a, b) {
        return a.invite_ts - b.invite_ts;
      });
  }

  /* Literal class set (Tailwind JIT only emits classes seen as literals). */
  var CALL_ENTRY = "flex items-center justify-center gap-1.5 py-1.5 text-[12px] md:text-[11px] text-slate-500";
  var CALL_ICON =
    '<svg class="h-3.5 w-3.5 shrink-0" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M2 3.5A1.5 1.5 0 013.5 2h1.148a1.5 1.5 0 011.465 1.175l.716 3.223a1.5 1.5 0 01-1.052 1.767l-.933.267c-.41.117-.643.555-.48.95a11.542 11.542 0 006.254 6.254c.395.163.833-.07.95-.48l.267-.933a1.5 1.5 0 011.767-1.052l3.223.716A1.5 1.5 0 0118 15.352V16.5a1.5 1.5 0 01-1.5 1.5H15c-7.18 0-13-5.82-13-13V3.5z" clip-rule="evenodd"/></svg>';

  function renderEntry(summary) {
    const row = document.createElement("div");
    row.className = CALL_ENTRY;
    let label;
    if (summary.status === "ended") {
      label = window.LR.t("Call · {duration}", { duration: fmtDuration(summary.duration_s || 0) });
    } else if (summary.status === "missed") {
      label = window.LR.t("Missed call");
    } else if (summary.status === "declined") {
      label = window.LR.t("Declined call");
    } else if (summary.status === "active") {
      label = window.LR.t("Ongoing call");
    } else {
      label = window.LR.t("Calling…");
    }
    if (summary.video) {
      const icon = document.createElement("span");
      icon.className = "h-3.5 w-3.5 shrink-0";
      icon.innerHTML =
        '<svg class="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor"><path d="M3.25 4A2.25 2.25 0 001 6.25v7.5A2.25 2.25 0 003.25 16h7.5A2.25 2.25 0 0013 13.75v-7.5A2.25 2.25 0 0010.75 4h-7.5z"/><path d="M14 8.123v3.754a.25.25 0 00.382.214l3.202-1.877a.25.25 0 000-.428l-3.202-1.877a.25.25 0 00-.382.214z"/></svg>';
      row.appendChild(icon);
    } else {
      const icon = document.createElement("span");
      icon.className = "h-3.5 w-3.5 shrink-0";
      icon.innerHTML = CALL_ICON;
      row.appendChild(icon);
    }
    const text = document.createElement("span");
    text.textContent = label;
    if (summary.status === "missed" || summary.status === "declined") {
      row.className = CALL_ENTRY + " text-red-400";
    }
    row.appendChild(text);
    return row;
  }

  /* ---------------- overlay ---------------- */

  function overlay() {
    return el("chat-call-overlay");
  }

  function setStatus(text) {
    el("chat-call-status").textContent = text || "";
  }

  function showOverlay(mode, roomId) {
    overlay().classList.remove("hidden");
    el("chat-call-peer").textContent = peerName(roomId);
    el("chat-call-incoming").classList.toggle("hidden", mode !== "incoming");
    el("chat-call-active").classList.toggle("hidden", mode !== "active");
  }

  function hideOverlay() {
    overlay().classList.add("hidden");
    el("chat-call-remote-video").srcObject = null;
    el("chat-call-local-video").srcObject = null;
  }

  function attachLocalStream(stream, video) {
    const localVideo = el("chat-call-local-video");
    localVideo.classList.toggle("hidden", !video);
    localVideo.srcObject = stream;
  }

  function attachRemoteStream(stream) {
    if (!active) return;
    el("chat-call-remote-video").srcObject = stream;
    const hasVideo = stream.getVideoTracks().length > 0 && active.video;
    el("chat-call-no-video").classList.toggle("hidden", hasVideo);
    el("chat-call-remote-avatar").textContent = Chat.initialsFor(peerName(active.roomId));
  }

  /* ---------------- peer connection ---------------- */

  async function getUserMediaSafe(video) {
    try {
      return await navigator.mediaDevices.getUserMedia({
        audio: true,
        video: video ? { width: { ideal: 640 }, height: { ideal: 480 } } : false,
      });
    } catch (err) {
      throw new Error(mediaErrorMessage(err));
    }
  }

  function mediaErrorMessage(err) {
    const name = (err && err.name) || "";
    if (name === "NotAllowedError" || name === "SecurityError") {
      return window.LR.t(
        "Microphone/camera access was denied. Allow access in your browser's site settings, then try again."
      );
    }
    if (name === "NotFoundError" || name === "DevicesNotFoundError") {
      return window.LR.t("No microphone was found. Connect one and try again.");
    }
    return window.LR.t("Could not access your microphone or camera ({error}).", { error: name || "media error" });
  }

  function createPeer() {
    const pc = new RTCPeerConnection({ iceServers: calls.iceServers });
    pc.onicecandidate = function (ev) {
      if (ev.candidate && active) queueOwnCandidate(ev.candidate.toJSON());
    };
    pc.ontrack = function (ev) {
      if (ev.streams && ev.streams[0]) attachRemoteStream(ev.streams[0]);
    };
    pc.onconnectionstatechange = function () {
      if (!active || active.pc !== pc) return;
      if (pc.connectionState === "connected") {
        onConnected();
      } else if (pc.connectionState === "failed") {
        hangUp("ice_failed", true);
      }
    };
    return pc;
  }

  function queueOwnCandidate(candidate) {
    ownCandidates.push(candidate);
    flushOwnCandidates();
  }

  async function flushOwnCandidates() {
    if (!active || !active.callId || !ownCandidates.length) return;
    const batch = ownCandidates.splice(0, ownCandidates.length);
    try {
      await Chat.api(callUrl(active.roomId, active.callId, "candidates"), {
        method: "POST",
        json: { candidates: batch },
      });
    } catch (err) {
      /* non-fatal: ICE may still succeed over the remaining candidates */
    }
  }

  async function applyRemoteCandidates(pc, candidates) {
    for (let i = 0; i < (candidates || []).length; i++) {
      try {
        await pc.addIceCandidate(candidates[i]);
      } catch (err) {
        /* malformed or pre-offer candidate: skip, ICE proceeds with the rest */
      }
    }
  }

  /* ---------------- outgoing call ---------------- */

  async function startCall(video) {
    if (active || incoming) return;
    if (!calls.enabled) {
      Chat.banner(window.LR.t("Calls are not configured for your domain."));
      return;
    }
    const roomId = state.activeRoomId;
    if (!roomId) return;
    let stream;
    try {
      stream = await getUserMediaSafe(video);
    } catch (err) {
      Chat.banner(err.message, true, undefined, testDevicesAction());
      return;
    }
    const pc = createPeer();
    active = {
      roomId: roomId,
      callId: null,
      role: "caller",
      pc: pc,
      localStream: stream,
      video: video,
      connectedAt: null,
      timer: null,
      remoteCandidates: [],
    };
    ownCandidates = [];
    attachLocalStream(stream, video);
    attachRemoteStream(new MediaStream());
    showOverlay("active", roomId);
    setStatus(window.LR.t("Connecting…"));
    updateHeader();
    try {
      stream.getTracks().forEach(function (t) {
        pc.addTrack(t, stream);
      });
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      const data = await Chat.api(callUrl(roomId, null, null), {
        method: "POST",
        json: { video: video, offer: { type: offer.type, sdp: offer.sdp } },
      });
      active.callId = data.call_id;
      touchSummary(roomId, {
        call_id: data.call_id,
        type: "m.call.invite",
        sender: state.identity,
        content: { "lr.video": video },
        origin_server_ts: Date.now(),
      });
      rerenderTimeline(roomId);
      setStatus(window.LR.t("Ringing…"));
      await flushOwnCandidates();
    } catch (err) {
      teardown(false);
      Chat.banner(window.LR.t("Could not start the call: {error}", { error: err.message }));
      updateHeader();
    }
  }

  /* ---------------- incoming call ---------------- */

  function onInvite(roomId, ev) {
    if (active || incoming) {
      /* Glare/busy (HLD U25.19 first-invite-wins): auto-decline the new one. */
      Chat.api(callUrl(roomId, ev.call_id, "hangup"), {
        method: "POST",
        json: { reason: "user_hangup" },
      }).catch(function () {
        /* non-fatal */
      });
      touchSummary(roomId, {
        call_id: ev.call_id,
        type: "m.call.hangup",
        sender: state.identity,
        content: { reason: "user_hangup" },
        origin_server_ts: Date.now(),
      });
      return;
    }
    const content = ev.content || {};
    const offer = content.offer;
    if (!offer || !offer.sdp) return;
    const video = !!content["lr.video"] || String(offer.sdp).indexOf("m=video") !== -1;
    incoming = {
      roomId: roomId,
      callId: ev.call_id,
      sender: ev.sender,
      offer: offer,
      video: video,
      candidates: [],
      timer: window.setTimeout(function () {
        decline("invite_timeout");
      }, Math.min(content.lifetime || INVITE_TIMEOUT_MS, INVITE_TIMEOUT_MS)),
    };
    el("chat-call-incoming-label").textContent = video
      ? window.LR.t("Incoming video call")
      : window.LR.t("Incoming call");
    el("chat-call-incoming-avatar").textContent = Chat.initialsFor(peerName(roomId));
    el("chat-call-accept").disabled = false;
    showOverlay("incoming", roomId);
    setStatus("");
    updateHeader();
  }

  async function accept() {
    if (!incoming) return;
    const btn = el("chat-call-accept");
    btn.disabled = true;
    const spinner = btn.querySelector("svg");
    if (spinner) spinner.classList.remove("hidden");
    const pending = incoming;
    let stream;
    try {
      stream = await getUserMediaSafe(pending.video);
    } catch (err) {
      btn.disabled = false;
      if (spinner) spinner.classList.add("hidden");
      Chat.banner(err.message, true, undefined, testDevicesAction());
      decline("user_media_error");
      return;
    }
    const pc = createPeer();
    active = {
      roomId: pending.roomId,
      callId: pending.callId,
      role: "callee",
      pc: pc,
      localStream: stream,
      video: pending.video,
      connectedAt: null,
      timer: null,
      remoteCandidates: pending.candidates,
    };
    incoming = null;
    window.clearTimeout(pending.timer);
    ownCandidates = [];
    attachLocalStream(stream, pending.video);
    attachRemoteStream(new MediaStream());
    showOverlay("active", pending.roomId);
    setStatus(window.LR.t("Connecting…"));
    updateHeader();
    try {
      await pc.setRemoteDescription(pending.offer);
      stream.getTracks().forEach(function (t) {
        pc.addTrack(t, stream);
      });
      const answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);
      await applyRemoteCandidates(pc, active.remoteCandidates);
      active.remoteCandidates = [];
      await Chat.api(callUrl(pending.roomId, pending.callId, "answer"), {
        method: "POST",
        json: { answer: { type: answer.type, sdp: answer.sdp } },
      });
      await flushOwnCandidates();
    } catch (err) {
      teardown(false);
      Chat.banner(window.LR.t("Could not answer the call: {error}", { error: err.message }));
      updateHeader();
    }
  }

  function decline(reason) {
    if (!incoming) return;
    const pending = incoming;
    incoming = null;
    window.clearTimeout(pending.timer);
    hideOverlay();
    updateHeader();
    Chat.api(callUrl(pending.roomId, pending.callId, "hangup"), {
      method: "POST",
      json: { reason: reason || "user_hangup" },
    }).catch(function () {
      /* non-fatal: the invite expires server-side via lifetime */
    });
  }

  /* ---------------- signaling events ---------------- */

  async function onAnswer(roomId, ev) {
    if (!active || active.callId !== ev.call_id || active.role !== "caller") return;
    const answer = (ev.content || {}).answer;
    if (!answer || !answer.sdp) return;
    try {
      await active.pc.setRemoteDescription(answer);
      await applyRemoteCandidates(active.pc, active.remoteCandidates);
      active.remoteCandidates = [];
      await flushOwnCandidates();
    } catch (err) {
      hangUp("unknown_error", false);
      Chat.banner(window.LR.t("The call failed: {error}", { error: err.message }));
    }
  }

  function onRemoteCandidates(roomId, ev) {
    const candidates = (ev.content || {}).candidates || [];
    if (incoming && incoming.callId === ev.call_id) {
      incoming.candidates = incoming.candidates.concat(candidates);
      return;
    }
    if (active && active.callId === ev.call_id) {
      if (active.pc.remoteDescription) {
        applyRemoteCandidates(active.pc, candidates);
      } else {
        active.remoteCandidates = active.remoteCandidates.concat(candidates);
      }
    }
  }

  function onHangup(roomId, ev) {
    if (incoming && incoming.callId === ev.call_id) {
      incoming = null;
      hideOverlay();
      updateHeader();
      return;
    }
    if (active && active.callId === ev.call_id) {
      teardown(false);
      updateHeader();
    }
  }

  function handleEvents(roomId, events) {
    (events || []).forEach(function (ev) {
      if (!ev || !ev.call_id || !ev.type) return;
      if (ev.sender === state.identity) return; /* own events echo back via sync */
      if (ev.type === "m.call.invite") onInvite(roomId, ev);
      else if (ev.type === "m.call.answer") onAnswer(roomId, ev);
      else if (ev.type === "m.call.candidates") onRemoteCandidates(roomId, ev);
      else if (ev.type === "m.call.hangup") onHangup(roomId, ev);
      touchSummary(roomId, ev);
    });
    rerenderTimeline(roomId);
  }

  function rerenderTimeline(roomId) {
    if (roomId === state.activeRoomId && window.Chat.ui.renderTimeline) {
      window.Chat.ui.renderTimeline(roomId);
    }
  }

  /* ---------------- in-call controls & teardown ---------------- */

  function onConnected() {
    if (!active) return;
    active.connectedAt = Date.now();
    if (active.timer) window.clearInterval(active.timer);
    active.timer = window.setInterval(function () {
      if (!active || !active.connectedAt) return;
      setStatus(window.LR.t("Connected · {duration}", { duration: fmtDuration((Date.now() - active.connectedAt) / 1000) }));
    }, 1000);
    setStatus(window.LR.t("Connected · {duration}", { duration: "0:00" }));
  }

  function hangUp(reason, silent) {
    if (!active) return;
    const pending = active;
    teardown(silent !== true);
    Chat.api(callUrl(pending.roomId, pending.callId, "hangup"), {
      method: "POST",
      json: { reason: reason || "user_hangup" },
    }).catch(function () {
      /* non-fatal: the peer also stops on ICE timeout */
    });
    updateHeader();
  }

  function teardown(announce) {
    if (!active) return;
    if (active.timer) window.clearInterval(active.timer);
    if (active.localStream) {
      active.localStream.getTracks().forEach(function (t) {
        t.stop();
      });
    }
    try {
      active.pc.close();
    } catch (err) {
      /* already closed */
    }
    const roomId = active.roomId;
    active = null;
    ownCandidates = [];
    hideOverlay();
    if (announce) {
      Chat.banner(window.LR.t("The call ended."), false, "success");
    }
    rerenderTimeline(roomId);
  }

  function toggleMute() {
    if (!active || !active.localStream) return;
    const track = active.localStream.getAudioTracks()[0];
    if (!track) return;
    track.enabled = !track.enabled;
    const btn = el("chat-call-mute");
    btn.classList.toggle("bg-white/10", track.enabled);
    btn.classList.toggle("bg-amber-500", !track.enabled);
    btn.title = track.enabled
      ? window.LR.t("Mute microphone")
      : window.LR.t("Unmute microphone");
  }

  function toggleCamera() {
    if (!active || !active.localStream) return;
    const track = active.localStream.getVideoTracks()[0];
    if (!track) return;
    track.enabled = !track.enabled;
    const btn = el("chat-call-camera");
    btn.classList.toggle("bg-white/10", track.enabled);
    btn.classList.toggle("bg-amber-500", !track.enabled);
    btn.title = track.enabled
      ? window.LR.t("Turn camera off")
      : window.LR.t("Turn camera on");
  }

  /* ---------------- wiring ---------------- */

  document.addEventListener("DOMContentLoaded", function () {
    el("chat-call-audio").addEventListener("click", function () {
      startCall(false);
    });
    el("chat-call-video").addEventListener("click", function () {
      startCall(true);
    });
    el("chat-call-accept").addEventListener("click", accept);
    el("chat-call-decline").addEventListener("click", function () {
      decline("user_hangup");
    });
    el("chat-call-hangup").addEventListener("click", function () {
      hangUp("user_hangup");
    });
    el("chat-call-mute").addEventListener("click", toggleMute);
    el("chat-call-camera").addEventListener("click", toggleCamera);
    /* Leaving the page mid-call: best-effort hangup via sendBeacon so the
       peer stops ringing instead of waiting for the ICE timeout. */
    window.addEventListener("pagehide", function () {
      if (!active || !active.callId) return;
      const payload = new Blob([JSON.stringify({ reason: "user_hangup" })], {
        type: "application/json",
      });
      navigator.sendBeacon(callUrl(active.roomId, active.callId, "hangup"), payload);
    });
    init();
  });

  Chat.calls = {
    handleEvents: handleEvents,
    mergeSummaries: mergeSummaries,
    timelineEntries: timelineEntries,
    renderEntry: renderEntry,
    updateHeader: updateHeader,
    mediaErrorMessage: mediaErrorMessage,
    fmtDuration: fmtDuration,
    config: function () {
      return { enabled: calls.enabled, iceServers: calls.iceServers };
    },
  };
})();
