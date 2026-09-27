/* Chat device test: single-window camera/mic/speaker check + loopback test
   call (HLD U25.59). No server signaling: two local RTCPeerConnections
   negotiate directly in the page (plus a relay-only pair when TURN is
   configured, to verify coturn accepts allocations). No m.call.* events are
   sent, so no timeline entries or cache rows are created (U25.23). */
(function () {
  "use strict";

  const Chat = window.Chat;

  /* Literal class sets (Tailwind JIT only emits literal classes). */
  const STATUS_OK = "text-emerald-600";
  const STATUS_FAIL = "text-red-500";

  const RELAY_TIMEOUT_MS = 12000;

  const test = {
    stream: null,
    audioCtx: null,
    analyser: null,
    meterRaf: null,
    direct: null, /* {pc1, pc2, timer, startedAt} */
    relay: null, /* {pc1, pc2, timeout} */
  };

  function el(id) {
    return document.getElementById(id);
  }

  function showStatus(id, text, ok) {
    const node = el(id);
    node.classList.remove("hidden", STATUS_OK, STATUS_FAIL);
    node.classList.add(ok === true ? STATUS_OK : ok === false ? STATUS_FAIL : "text-slate-500");
    node.textContent = text;
  }

  function hideStatuses() {
    ["chat-test-status-direct", "chat-test-status-relay", "chat-test-note"].forEach(function (id) {
      const node = el(id);
      node.classList.add("hidden");
      node.textContent = "";
    });
  }

  function showError(text) {
    const node = el("chat-test-error");
    node.textContent = text;
    node.classList.remove("hidden");
  }

  function clearError() {
    const node = el("chat-test-error");
    node.textContent = "";
    node.classList.add("hidden");
  }

  /* ---------------- device check ---------------- */

  async function startPreview() {
    try {
      test.stream = await navigator.mediaDevices.getUserMedia({
        audio: true,
        video: { width: { ideal: 640 }, height: { ideal: 480 } },
      });
    } catch (err) {
      showError(Chat.calls.mediaErrorMessage(err));
      return;
    }
    clearError();
    el("chat-test-preview").srcObject = test.stream;
    startMicMeter();
  }

  function startMicMeter() {
    if (!test.stream || test.stream.getAudioTracks().length === 0) return;
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      test.audioCtx = test.audioCtx || new Ctx();
      const source = test.audioCtx.createMediaStreamSource(test.stream);
      test.analyser = test.audioCtx.createAnalyser();
      test.analyser.fftSize = 2048;
      source.connect(test.analyser);
      const buf = new Float32Array(test.analyser.fftSize);
      const bar = el("chat-test-mic-bar");
      const tick = function () {
        test.analyser.getFloatTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
        const rms = Math.sqrt(sum / buf.length);
        /* Bar width is runtime-computed: inline style, never a Tailwind class. */
        bar.style.width = Math.min(100, Math.round(rms * 300)) + "%";
        test.meterRaf = window.requestAnimationFrame(tick);
      };
      tick();
    } catch (err) {
      /* Meter is a convenience; the call test still verifies audio end to end. */
      if (window.console && console.warn) console.warn("mic meter unavailable", err);
    }
  }

  function playTestSound() {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      test.audioCtx = test.audioCtx || new Ctx();
      test.audioCtx.resume();
      const osc = test.audioCtx.createOscillator();
      const gain = test.audioCtx.createGain();
      osc.frequency.value = 440;
      const now = test.audioCtx.currentTime;
      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.exponentialRampToValueAtTime(0.3, now + 0.05);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 1.2);
      osc.connect(gain);
      gain.connect(test.audioCtx.destination);
      osc.start(now);
      osc.stop(now + 1.3);
    } catch (err) {
      showError(window.LR.t("Could not play the test sound ({error}).", { error: (err && err.name) || "audio" }));
    }
  }

  /* ---------------- loopback test call ---------------- */

  function makePair(iceServers, policy) {
    const opts = policy ? { iceServers: iceServers, iceTransportPolicy: policy } : { iceServers: iceServers };
    const pc1 = new RTCPeerConnection(opts);
    const pc2 = new RTCPeerConnection(opts);
    pc1.onicecandidate = function (ev) {
      if (ev.candidate) pc2.addIceCandidate(ev.candidate).catch(function () {});
    };
    pc2.onicecandidate = function (ev) {
      if (ev.candidate) pc1.addIceCandidate(ev.candidate).catch(function () {});
    };
    return { pc1: pc1, pc2: pc2 };
  }

  async function negotiate(pair) {
    const offer = await pair.pc1.createOffer();
    await pair.pc1.setLocalDescription(offer);
    await pair.pc2.setRemoteDescription(pair.pc1.localDescription);
    const answer = await pair.pc2.createAnswer();
    await pair.pc2.setLocalDescription(answer);
    await pair.pc1.setRemoteDescription(pair.pc2.localDescription);
  }

  function startDuration() {
    if (test.direct.timer) window.clearInterval(test.direct.timer);
    const startedAt = Date.now();
    const render = function () {
      el("chat-test-timer").textContent = window.LR.t("Connected · {duration}", {
        duration: Chat.calls.fmtDuration((Date.now() - startedAt) / 1000),
      });
    };
    render();
    test.direct.timer = window.setInterval(render, 1000);
    const unmute = el("chat-test-unmute");
    unmute.classList.remove("hidden");
    const remote = el("chat-test-remote");
    remote.muted = true; /* muted by default: avoids speaker-to-mic feedback */
    unmute.textContent = window.LR.t("Unmute");
    unmute.onclick = function () {
      remote.muted = !remote.muted;
      unmute.textContent = remote.muted ? window.LR.t("Unmute") : window.LR.t("Mute");
    };
  }

  function setLoopButton(busy) {
    const btn = el("chat-test-loop");
    btn.disabled = busy;
    const spinner = btn.querySelector("svg");
    if (spinner) spinner.classList.toggle("hidden", !busy);
  }

  async function runLoopback() {
    hideStatuses();
    el("chat-test-timer").textContent = window.LR.t("Connecting…");
    if (!test.stream) {
      await startPreview();
      if (!test.stream) {
        el("chat-test-timer").textContent = "";
        return;
      }
    }
    teardownLoop();
    setLoopButton(true);

    const cfg = Chat.calls.config();
    const remoteVideo = el("chat-test-remote");
    remoteVideo.srcObject = null;
    try {
      /* Direct pair: default ICE policy — verifies the local WebRTC pipeline. */
      test.direct = makePair(cfg.iceServers, null);
      test.direct.pc2.ontrack = function (ev) {
        if (ev.streams && ev.streams[0]) remoteVideo.srcObject = ev.streams[0];
      };
      test.direct.pc1.onconnectionstatechange = function () {
        if (!test.direct || test.direct.pc1 !== this) return;
        if (this.connectionState === "connected") {
          showStatus("chat-test-status-direct", window.LR.t("Direct connection: working"), true);
          startDuration();
        } else if (this.connectionState === "failed") {
          showStatus("chat-test-status-direct", window.LR.t("Direct connection: failed"), false);
          el("chat-test-timer").textContent = "";
        }
      };
      test.stream.getTracks().forEach(function (t) {
        test.direct.pc1.addTrack(t, test.stream);
      });
      await negotiate(test.direct);
    } catch (err) {
      setLoopButton(false);
      el("chat-test-timer").textContent = "";
      showError(window.LR.t("Could not start the test call: {error}", { error: err.message }));
      return;
    }

    if (!cfg.iceServers || cfg.iceServers.length === 0) {
      /* Fail-early (U25.21): without TURN, real calls are off for the domain. */
      const note = el("chat-test-note");
      note.classList.remove("hidden");
      note.classList.add("text-slate-500");
      // Long literal kept on one line so the i18n JS extractor and static
      // guards match it (scripts/i18n_extract_js.py tolerates wraps; tests don't).
      note.textContent = window.LR.t("Voice/video calls are disabled for your domain (no TURN server configured).");
      setLoopButton(false);
      return;
    }

    /* Relay-only pair: verifies the TURN server accepts allocations with the
       app-minted REST credentials (U25.21) before a real call needs it. */
    try {
      test.relay = makePair(cfg.iceServers, "relay");
      test.relay.settled = false;
      test.relay.timeout = window.setTimeout(function () {
        if (test.relay && !test.relay.settled) {
          test.relay.settled = true;
          showStatus("chat-test-status-relay", window.LR.t("TURN relay: unreachable"), false);
        }
      }, RELAY_TIMEOUT_MS);
      test.relay.pc1.onconnectionstatechange = function () {
        if (!test.relay || test.relay.pc1 !== this || test.relay.settled) return;
        if (this.connectionState === "connected") {
          test.relay.settled = true;
          window.clearTimeout(test.relay.timeout);
          showStatus("chat-test-status-relay", window.LR.t("TURN relay: working"), true);
        } else if (this.connectionState === "failed") {
          test.relay.settled = true;
          window.clearTimeout(test.relay.timeout);
          showStatus("chat-test-status-relay", window.LR.t("TURN relay: unreachable"), false);
        }
      };
      test.stream.getTracks().forEach(function (t) {
        test.relay.pc1.addTrack(t, test.stream);
      });
      await negotiate(test.relay);
    } catch (err) {
      /* The relay probe is informational: the direct result already tells the
         user whether their devices work. */
      if (test.relay && !test.relay.settled) {
        test.relay.settled = true;
        window.clearTimeout(test.relay.timeout);
        showStatus("chat-test-status-relay", window.LR.t("TURN relay: unreachable"), false);
      }
    } finally {
      setLoopButton(false);
    }
  }

  function teardownLoop() {
    if (test.direct) {
      if (test.direct.timer) window.clearInterval(test.direct.timer);
      try {
        test.direct.pc1.close();
      } catch (err) {
        /* already closed */
      }
      try {
        test.direct.pc2.close();
      } catch (err) {
        /* already closed */
      }
      test.direct = null;
    }
    if (test.relay) {
      if (test.relay.timeout) window.clearTimeout(test.relay.timeout);
      try {
        test.relay.pc1.close();
      } catch (err) {
        /* already closed */
      }
      try {
        test.relay.pc2.close();
      } catch (err) {
        /* already closed */
      }
      test.relay = null;
    }
    el("chat-test-unmute").classList.add("hidden");
    el("chat-test-remote").muted = true;
  }

  /* ---------------- dialog lifecycle ---------------- */

  function resetUi() {
    hideStatuses();
    clearError();
    el("chat-test-mic-bar").style.width = "0%";
    el("chat-test-timer").textContent = "";
    el("chat-test-unmute").classList.add("hidden");
    const remote = el("chat-test-remote");
    remote.muted = true;
    remote.srcObject = null;
    setLoopButton(false);
  }

  function cleanup() {
    teardownLoop();
    if (test.meterRaf) window.cancelAnimationFrame(test.meterRaf);
    test.meterRaf = null;
    test.analyser = null;
    if (test.stream) {
      test.stream.getTracks().forEach(function (t) {
        t.stop();
      });
      test.stream = null;
    }
    el("chat-test-preview").srcObject = null;
    if (test.audioCtx) {
      test.audioCtx.close().catch(function () {});
      test.audioCtx = null;
    }
    resetUi();
  }

  function open() {
    const dialog = el("chat-test-call-dialog");
    if (dialog.open) return;
    resetUi();
    dialog.showModal();
    startPreview();
  }

  document.addEventListener("DOMContentLoaded", function () {
    el("chat-test-call").addEventListener("click", open);
    el("chat-test-close").addEventListener("click", function () {
      el("chat-test-call-dialog").close();
    });
    el("chat-test-call-dialog").addEventListener("close", cleanup);
    el("chat-test-sound").addEventListener("click", playTestSound);
    el("chat-test-loop").addEventListener("click", runLoopback);
  });

  Chat.deviceTest = {
    open: open,
  };
})();
