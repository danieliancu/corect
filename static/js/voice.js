"use strict";
// Voice input: live transcription, so words appear in the text box while the learner speaks. When live transcription is
// switched off, unsupported or cannot connect, the fallback records, stops and transcribes the finished recording.
// British voice output: speak a signed Corect.uk sentence only when its speaker button is pressed.
(() => {
  const UNAVAILABLE = "Vocea este momentan indisponibilă. Încearcă din nou mai târziu.";
  const RECORDING_FORMATS = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/wav"];
  const status = document.getElementById("voice-status");

  const csrfToken = () =>
    document.querySelector("input[name=csrfmiddlewaretoken]")?.value ||
    (document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/) || [])[1] ||
    "";

  function announce(message, isError = false) {
    let region = status || document.getElementById("speech-status");
    if (!region) {
      region = document.createElement("p");
      region.id = "speech-status";
      region.className = "sr-only";
      region.setAttribute("role", "status");
      document.body.append(region);
    }
    region.textContent = message;
    region.classList.toggle("is-error", isError);
  }

  async function errorMessage(response) {
    try {
      return (await response.json()).error || UNAVAILABLE;
    } catch {
      return UNAVAILABLE;
    }
  }

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      body,
      credentials: "same-origin",
      headers: { "X-CSRFToken": csrfToken() },
    });
  }

  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const now = () => performance.now();
  const debugging = () => {
    try {
      return window.localStorage.getItem("corectVoiceDebug") === "1";
    } catch {
      return false;
    }
  };

  // ---------- Voice input ----------
  const mic = document.querySelector("[data-voice-record]");
  const textarea = document.getElementById("text");
  if (mic && textarea) {
    const CONNECT_TIMEOUT_MS = 10000;
    const SILENCE_STOP_MS = 3000;
    const bounded = (value, fallback, low, high) => {
      const number = Number.parseInt(value, 10);
      return Number.isFinite(number) ? Math.min(high, Math.max(low, number)) : fallback;
    };
    // Set on the server (VOICE_TRAILING_AUDIO_MS, VOICE_FINAL_TRANSCRIPT_MS). A debug override exists only for benchmarks.
    const trailingAudioMs = () => {
      let override = null;
      try {
        if (debugging()) override = window.localStorage.getItem("corectVoiceTrailingMs");
      } catch {}
      return bounded(override ?? mic.dataset.trailingMs, 200, 0, 1500);
    };
    const finalTranscriptMs = bounded(mic.dataset.finalMs, 2500, 500, 4000);
    const ACTION = mic.dataset.actionLabel || "Vreau să sune natural!";
    const LABELS = {
      idle: "Înregistrează-ți vocea",
      starting: "Înregistrează-ți vocea",
      recording: "Oprește înregistrarea",
      transcribing: "Transcriem înregistrarea",
      connecting: "Pornim transcrierea live",
      listening: "Oprește transcrierea live",
      finalising: "Finalizăm transcrierea live",
    };
    const maxSeconds = Number(mic.dataset.maxSeconds) || 60;
    const maxLength = textarea.maxLength > 0 ? textarea.maxLength : Infinity;
    const submitButtons = [...(textarea.form?.querySelectorAll("button[type=submit]") || [])];
    const liveAvailable = () =>
      Boolean(mic.dataset.realtimeUrl && mic.dataset.finishUrl && window.RTCPeerConnection && window.CorectTranscript &&
        navigator.mediaDevices?.getUserMedia);
    const overlay = document.getElementById("voice-overlay");
    let state = "idle";
    let stream = null;
    let stopTimer = null;
    let formBusy = false;
    let lockedButtons = [];
    let preconnected = false;
    // File fallback
    let recorder = null;
    let chunks = [];
    let discardRecording = false;
    // Live transcription
    let live = null;
    mic.hidden = false;
    // Timings of the last live session, in milliseconds (numbers only), for developers and benchmarks.
    window.CorectVoice = { lastTimings: null };

    const setState = (next) => {
      state = next;
      const active = next === "recording" || next === "listening";
      const busy = ["transcribing", "connecting", "finalising"].includes(next);
      mic.classList.toggle("is-recording", active);
      mic.classList.toggle("is-busy", busy);
      mic.disabled = busy || (next === "idle" && formBusy);
      mic.setAttribute("aria-pressed", String(active));
      mic.setAttribute("aria-label", LABELS[next] || LABELS.idle);
      if (overlay) overlay.hidden = next !== "connecting";
      // Other homepage features (the example sentences) follow the microphone through this one signal.
      mic.dataset.voiceState = next;
      mic.dispatchEvent(new CustomEvent("voice-state", { bubbles: true, detail: { state: next } }));
    };

    const releaseMicrophone = () => {
      clearTimeout(stopTimer);
      stream?.getTracks().forEach((track) => track.stop());
      stream = null;
    };

    const microphoneError = (error) =>
      ["NotAllowedError", "SecurityError"].includes(error?.name)
        ? "Accesul la microfon a fost refuzat."
        : "Nu am putut folosi microfonul.";

    // While words are being written in, the learner can read and scroll but not type, and cannot submit yet.
    function lockEditor() {
      textarea.readOnly = true;
      textarea.setAttribute("aria-busy", "true");
      if (lockedButtons.length) return;
      lockedButtons = submitButtons.filter((button) => !button.disabled);
      lockedButtons.forEach((button) => {
        button.disabled = true;
        button.setAttribute("aria-disabled", "true");
      });
    }

    function unlockEditor() {
      textarea.readOnly = false;
      textarea.removeAttribute("aria-busy");
      lockedButtons.forEach((button) => {
        button.disabled = false;
        button.removeAttribute("aria-disabled");
      });
      lockedButtons = [];
    }

    // The connection to the transcription provider is warmed only once the learner reaches for the microphone
    // (pointer down or Enter/Space), never on page load.
    function preconnect() {
      if (preconnected || state !== "idle" || !liveAvailable() || !mic.dataset.preconnect) return;
      preconnected = true;
      const link = document.createElement("link");
      link.rel = "preconnect";
      link.href = mic.dataset.preconnect;
      link.crossOrigin = "anonymous";
      document.head.append(link);
    }

    // ----- Live transcription timings -----
    function timingsOf(session) {
      const t = session.t;
      const span = (from, to) => (t[from] != null && t[to] != null ? Math.max(0, Math.round(t[to] - t[from])) : null);
      return {
        mic_ms: span("click", "mic"),
        session_ms: span("sessionSent", "sessionDone"),
        connect_ms: span("sdpSent", "connected"),
        startup_ms: span("click", "listening"),
        first_word_ms: span("listening", "firstDelta"),
        finalise_ms: span("stop", "final"),
        final_received: session.commitSent ? t.finalReceived === true : null,
      };
    }

    // ----- Live transcription -----
    function render(session) {
      const { text, caret } = session.transcript.snapshot();
      if (textarea.value !== text) {
        textarea.value = text;
        // The same handling as typing: updates the character counter.
        textarea.dispatchEvent(new Event("input", { bubbles: true }));
      }
      textarea.setSelectionRange(caret, caret);
      if (caret >= text.length) textarea.scrollTop = textarea.scrollHeight;
    }

    function closeConnection(session) {
      session.closing = true;
      try {
        session.dc?.close();
      } catch {}
      try {
        session.pc?.close();
      } catch {}
    }

    function sendFinish(session, outcome, pageClosing = false) {
      if (!session.token || session.finishSent) return;
      session.finishSent = true;
      const body = new FormData();
      body.append("session", session.token);
      body.append("outcome", outcome);
      if (session.providerSeconds !== null) body.append("provider_seconds", String(session.providerSeconds));
      if (session.t) {
        Object.entries(timingsOf(session)).forEach(([name, value]) => {
          if (typeof value === "boolean") body.append(name, value ? "1" : "0");
          else if (value !== null) body.append(name, String(value));
        });
      }
      body.append("csrfmiddlewaretoken", csrfToken());
      if (pageClosing && navigator.sendBeacon && navigator.sendBeacon(mic.dataset.finishUrl, body)) return;
      post(mic.dataset.finishUrl, body).catch(() => {});
    }

    function endLive(session, pageClosing = false) {
      if (live !== session) return;
      live = null;
      clearTimeout(stopTimer);
      clearTimeout(session.silenceTimer);
      closeConnection(session);
      releaseMicrophone();
      sendFinish(session, pageClosing ? "page_closed" : session.outcome, pageClosing);
      window.CorectVoice.lastTimings = timingsOf(session);
      if (debugging()) console.debug("corect voice timings", window.CorectVoice.lastTimings);
      if (pageClosing) return;
      render(session);
      unlockEditor();
      setState("idle");
      if (session.outcome === "interrupted") {
        announce("Conexiunea pentru transcriere live s-a întrerupt. Am păstrat textul primit până acum.", true);
      } else if (session.outcome === "limit") {
        const limit = Number.isFinite(maxLength) ? maxLength.toLocaleString("ro-RO") : "";
        announce(`Ai ajuns la limita de ${limit} de caractere. Am oprit microfonul.`, true);
      } else if (!session.transcript.spoken()) {
        announce("Nu am auzit nimic. Apasă microfonul și încearcă din nou.", true);
      } else if (session.stoppedBySilence) {
        announce(`Nu te-am mai auzit, așa că am oprit microfonul. Poți modifica textul, apoi apasă „${ACTION}”.`);
      } else {
        announce(`Gata. Poți modifica textul, apoi apasă „${ACTION}”.`);
      }
      // On touch screens, focusing would pop up the keyboard over the text the learner wants to read first.
      if (!window.matchMedia?.("(pointer: coarse)")?.matches) textarea.focus({ preventScroll: true });
    }

    async function stopLive(outcome) {
      const session = live;
      if (!session || session.stopping) return;
      session.stopping = true;
      session.outcome = outcome;
      session.t.stop = now();
      clearTimeout(stopTimer);
      clearTimeout(session.silenceTimer);
      setState("finalising");
      const mute = () => stream?.getAudioTracks().forEach((track) => (track.enabled = false));
      if (outcome !== "interrupted" && session.dc?.readyState === "open") {
        // Words just spoken are still on their way, so the microphone stays on for a short trailing window before the
        // audio is committed. After three silent seconds, or at the character limit, there is nothing left to wait for.
        const trailing = session.stoppedBySilence || outcome === "limit" ? 0 : trailingAudioMs();
        if (trailing) await wait(trailing);
        mute();
        if (live === session && session.dc.readyState === "open") {
          let safety;
          // The provider's final transcript ends the wait at once; the timeout only guards against it never arriving.
          const finalTranscript = new Promise((resolve) => {
            session.finishWait = resolve;
            safety = setTimeout(resolve, finalTranscriptMs);
          });
          session.commitSent = true;
          try {
            session.dc.send(JSON.stringify({ type: "input_audio_buffer.commit" }));
          } catch {
            session.finishWait();
          }
          await finalTranscript;
          clearTimeout(safety);
        }
      } else {
        mute();
      }
      session.t.final = now();
      endLive(session);
    }

    // gpt-live-transcribe accepts no turn detection (the API rejects server VAD for this model), so silence is noticed
    // here: SILENCE_STOP_MS without a new word, counted from the moment it listens and restarted by every word, ends the
    // session exactly like pressing Stop, whether or not anything was said.
    function stopAfterSilence(session) {
      clearTimeout(session.silenceTimer);
      session.silenceTimer = setTimeout(() => {
        if (live !== session || state !== "listening") return;
        session.stoppedBySilence = true;
        stopLive("completed");
      }, SILENCE_STOP_MS);
    }

    function connectionLost(session) {
      if (live !== session || session.closing) return;
      if (session.stopping) session.finishWait?.();
      else if (state === "listening") stopLive("interrupted");
      else session.failConnect?.(new Error("closed"));
    }

    function onProviderEvent(session, raw) {
      if (live !== session) return;
      let event;
      try {
        event = JSON.parse(raw);
      } catch {
        return;
      }
      const id = event.item_id;
      // After the commit, speech that reached the provider later belongs to a new turn nobody is waiting for.
      const ours = id && (!session.commitSent || session.transcript.has(id) || id === session.committedId);
      switch (event.type) {
        case "conversation.item.input_audio_transcription.delta":
          if (!ours || session.stopping && session.outcome === "limit") return;
          session.t.firstDelta ??= now();
          if (session.transcript.applyDelta(id, event.delta) === "limit") {
            render(session);
            stopLive("limit");
            return;
          }
          render(session);
          if (!session.stopping) stopAfterSilence(session);
          return;
        case "input_audio_buffer.committed":
          if (!id) return;
          session.committedId = id;
          session.transcript.place(id, event.previous_item_id);
          return;
        case "conversation.item.input_audio_transcription.completed":
          if (!ours) return;
          if (event.usage?.type === "duration" && Number.isFinite(event.usage.seconds)) {
            session.providerSeconds = (session.providerSeconds || 0) + event.usage.seconds;
          }
          if (session.transcript.complete(id, event.transcript) === "limit" && !session.stopping) {
            render(session);
            stopLive("limit");
            return;
          }
          render(session);
          if (session.commitSent) {
            session.t.finalReceived = true;
            session.finishWait?.();
          }
          return;
        case "conversation.item.input_audio_transcription.failed":
        case "error":
          if (session.stopping) session.finishWait?.();
          else if (state === "listening") stopLive("interrupted");
          return;
        default:
      }
    }

    // Peer connection, data channel and offer need nothing from the server, so they are prepared while the session
    // request is still on its way.
    async function prepare(session) {
      const pc = (session.pc = new RTCPeerConnection());
      stream.getAudioTracks().forEach((track) => pc.addTrack(track, stream));
      const dc = (session.dc = pc.createDataChannel("oai-events"));
      session.opened = new Promise((resolve, reject) => {
        session.failConnect = reject;
        dc.addEventListener("open", resolve, { once: true });
      });
      session.opened.catch(() => {});
      dc.addEventListener("message", (event) => onProviderEvent(session, event.data));
      dc.addEventListener("close", () => connectionLost(session));
      pc.addEventListener("connectionstatechange", () => {
        if (["failed", "disconnected"].includes(pc.connectionState)) connectionLost(session);
      });
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      session.offer = offer;
    }

    async function connect(session, data) {
      const abort = new AbortController();
      const timeout = setTimeout(() => {
        abort.abort();
        session.failConnect?.(new Error("timeout"));
      }, CONNECT_TIMEOUT_MS);
      try {
        session.t.sdpSent = now();
        // The short-lived client secret authorises this one connection; the server's API key never reaches the browser.
        const answer = await fetch(data.calls_url, {
          method: "POST",
          body: session.offer.sdp,
          headers: { Authorization: `Bearer ${data.client_secret}`, "Content-Type": "application/sdp" },
          signal: abort.signal,
        });
        if (!answer.ok) throw new Error("offer rejected");
        await session.pc.setRemoteDescription({ type: "answer", sdp: await answer.text() });
        await session.opened;
        session.t.connected = now();
        session.failConnect = null;
      } finally {
        clearTimeout(timeout);
      }
    }

    async function microphoneAlreadyAllowed() {
      try {
        return (await navigator.permissions?.query({ name: "microphone" }))?.state === "granted";
      } catch {
        return false;
      }
    }

    function requestSession(t) {
      t.sessionSent = now();
      const request = post(mic.dataset.realtimeUrl, new FormData()).then((response) => {
        t.sessionDone = now();
        return response;
      });
      request.catch(() => {});
      return request;
    }

    // The microphone failed after a session was already requested (only possible when permission was granted): close
    // that session as a connection failure, which costs nothing, instead of leaving it open.
    function closeUnusedSession(request, t) {
      request
        .then(async (response) => {
          if (!response.ok) return;
          const data = await response.json();
          sendFinish({ token: data.session, providerSeconds: null, t, commitSent: false }, "connect_failed");
        })
        .catch(() => {});
    }

    async function startLive() {
      const t = { click: now() };
      setState("connecting");
      announce("Pornim transcrierea live…");
      // With the microphone already allowed the browser cannot ask or refuse, so the session request overlaps
      // microphone start-up. Otherwise nothing is claimed until the learner has allowed the microphone.
      let sessionRequest = (await microphoneAlreadyAllowed()) ? requestSession(t) : null;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        });
        t.mic = now();
      } catch (error) {
        if (sessionRequest) closeUnusedSession(sessionRequest, t);
        setState("idle");
        announce(microphoneError(error), true);
        return;
      }
      const start = textarea.selectionStart ?? textarea.value.length;
      const end = textarea.selectionEnd ?? start;
      const session = {
        transcript: window.CorectTranscript.create({
          before: textarea.value.slice(0, start),
          after: textarea.value.slice(end),
          maxLength,
        }),
        providerSeconds: null,
        outcome: "completed",
        t,
      };
      live = session;
      lockEditor();
      try {
        const preparing = prepare(session);
        preparing.catch(() => {});
        sessionRequest ??= requestSession(t);
        const response = await sessionRequest;
        if (live !== session) return;
        if (response.status === 429) {
          live = null;
          closeConnection(session);
          releaseMicrophone();
          unlockEditor();
          setState("idle");
          announce(await errorMessage(response), true);
          return;
        }
        if (!response.ok) throw new Error("session unavailable");
        const data = await response.json();
        session.token = data.session;
        await preparing;
        await connect(session, data);
        if (live !== session) return;
        t.listening = now();
        setState("listening");
        stopAfterSilence(session);
        announce("Ascult… Vorbește normal. Textul apare pe măsură ce vorbești.");
        stopTimer = setTimeout(() => stopLive("completed"), (Number(data.max_seconds) || maxSeconds) * 1000);
      } catch {
        if (live !== session) return;
        // No words were transcribed yet, so recording for the fallback cannot bill the same speech twice.
        live = null;
        closeConnection(session);
        sendFinish(session, "connect_failed");
        window.CorectVoice.lastTimings = timingsOf(session);
        unlockEditor();
        recordForFallback();
      }
    }

    // ----- Finished-recording transcription (fallback) -----
    function insertTranscript(transcript) {
      const max = maxLength;
      const start = textarea.selectionStart ?? textarea.value.length;
      const end = textarea.selectionEnd ?? start;
      const before = textarea.value.slice(0, start);
      const after = textarea.value.slice(end);
      const insert =
        (before && !/\s$/.test(before) ? " " : "") +
        transcript +
        (after && !/^\s/.test(after) ? " " : "");
      if (Array.from(before + insert + after).length > max) {
        announce(
          `Textul înregistrat nu mai încape. Păstrează textul sub ${max} de caractere.`,
          true,
        );
        return;
      }
      textarea.focus();
      textarea.setRangeText(insert, start, end, "end");
      // The same handling as typing: updates the character counter.
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      announce(`Am adăugat înregistrarea. Verific-o, apoi apasă „${ACTION}”.`);
    }

    async function upload(blob) {
      setState("transcribing");
      announce("Transcriem înregistrarea…");
      const body = new FormData();
      body.append("audio", blob, "recording");
      try {
        const response = await post(mic.dataset.transcribeUrl, body);
        if (response.ok) insertTranscript((await response.json()).text);
        else announce(await errorMessage(response), true);
      } catch {
        announce("Conexiunea s-a întrerupt. Verifică internetul și încearcă din nou.", true);
      } finally {
        setState("idle");
      }
    }

    const recordingFormat = () =>
      window.MediaRecorder && RECORDING_FORMATS.find((type) => MediaRecorder.isTypeSupported(type));

    function beginRecording(mimeType, message) {
      chunks = [];
      discardRecording = false;
      recorder = new MediaRecorder(stream, { mimeType, audioBitsPerSecond: 32000 });
      recorder.addEventListener("dataavailable", (event) => {
        if (event.data && event.data.size) chunks.push(event.data);
      });
      recorder.addEventListener("stop", () => {
        releaseMicrophone();
        const blob = new Blob(chunks, { type: recorder.mimeType || mimeType });
        chunks = [];
        if (discardRecording) setState("idle"); // The page is closing: never upload on the way out.
        else if (blob.size) upload(blob);
        else {
          setState("idle");
          announce("Nu am înțeles înregistrarea. Încearcă din nou.", true);
        }
      });
      recorder.start();
      setState("recording");
      announce(message);
      stopTimer = setTimeout(stopRecording, maxSeconds * 1000);
    }

    // Live transcription could not start: record on the same microphone stream and transcribe after the stop.
    function recordForFallback() {
      const mimeType = recordingFormat();
      if (!mimeType) {
        releaseMicrophone();
        setState("idle");
        announce(UNAVAILABLE, true);
        return;
      }
      beginRecording(
        mimeType,
        `Transcrierea live nu e disponibilă acum. Înregistrăm și transcriem după ce te oprești (cel mult ${maxSeconds} de secunde).`,
      );
    }

    async function startRecording() {
      if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
        announce("Înregistrarea nu este acceptată în acest browser.", true);
        return;
      }
      const mimeType = recordingFormat();
      if (!mimeType) {
        announce("Înregistrarea nu este acceptată în acest browser.", true);
        return;
      }
      setState("starting");
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (error) {
        setState("idle");
        announce(microphoneError(error), true);
        return;
      }
      beginRecording(mimeType, `Înregistrăm… apasă din nou microfonul ca să oprești (cel mult ${maxSeconds} de secunde).`);
    }

    function stopRecording() {
      clearTimeout(stopTimer);
      if (recorder && recorder.state !== "inactive") recorder.stop();
      else releaseMicrophone();
    }

    mic.addEventListener("pointerdown", preconnect);
    mic.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") preconnect();
    });
    mic.addEventListener("click", () => {
      if (state === "listening") stopLive("completed");
      else if (state === "recording") stopRecording();
      else if (state === "idle") (liveAvailable() ? startLive() : startRecording());
    });

    // No microphone while "Vreau să sune natural!" is being processed.
    document.body.addEventListener("htmx:beforeRequest", (event) => {
      if (event.defaultPrevented || !textarea.form?.contains(event.detail.elt)) return;
      formBusy = true;
      if (state === "idle") mic.disabled = true;
    });
    document.body.addEventListener("htmx:afterRequest", (event) => {
      if (!textarea.form?.contains(event.detail.elt)) return;
      formBusy = false;
      if (state === "idle") mic.disabled = false;
    });

    window.addEventListener("pagehide", () => {
      if (live) endLive(live, true);
      else {
        discardRecording = true;
        stopRecording();
      }
    });
  }

  // ---------- British voice output ----------
  const player = new Audio();
  const cache = new Map(); // speech token -> object URL, for this page only
  let requested = null;
  let playing = null;

  const reset = (button) => {
    if (!button) return;
    button.classList.remove("is-busy", "is-playing");
    button.removeAttribute("aria-busy");
    button.setAttribute("aria-pressed", "false");
  };
  ["ended", "pause", "error"].forEach((name) =>
    player.addEventListener(name, () => {
      reset(playing);
      playing = null;
    }),
  );

  // Delegated, so speaker buttons added by HTMX swaps work without new listeners.
  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-speech-token]");
    if (!button || button.classList.contains("is-busy")) return;
    if (playing === button) {
      player.pause();
      return;
    }
    player.pause();
    requested = button;
    const token = button.dataset.speechToken;
    let url = cache.get(token);
    if (!url) {
      button.classList.add("is-busy");
      button.setAttribute("aria-busy", "true");
      try {
        const body = new FormData();
        body.append("token", token);
        const response = await post(button.dataset.speechUrl, body);
        if (!response.ok) throw new Error(await errorMessage(response));
        url = URL.createObjectURL(await response.blob());
        cache.set(token, url);
      } catch (error) {
        reset(button);
        announce(error instanceof TypeError ? UNAVAILABLE : error.message || UNAVAILABLE, true);
        return;
      }
      reset(button);
      if (requested !== button) return; // Another speaker was pressed while this one loaded.
    }
    player.src = url;
    try {
      await player.play();
      playing = button;
      button.classList.add("is-playing");
      button.setAttribute("aria-pressed", "true");
    } catch {
      reset(button);
      announce("Acest audio nu a putut fi redat.", true);
    }
  });

  window.addEventListener("pagehide", () => {
    player.pause();
    cache.forEach((url) => URL.revokeObjectURL(url));
    cache.clear();
  });
})();
