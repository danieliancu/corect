"use strict";
// Voice input: record, stop, transcribe on the server, insert into the text box.
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

  // ---------- Voice input ----------
  const mic = document.querySelector("[data-voice-record]");
  const textarea = document.getElementById("text");
  if (mic && textarea) {
    const maxSeconds = Number(mic.dataset.maxSeconds) || 60;
    let state = "idle";
    let recorder = null;
    let stream = null;
    let chunks = [];
    let stopTimer = null;
    mic.hidden = false;

    const setState = (next) => {
      state = next;
      mic.classList.toggle("is-recording", next === "recording");
      mic.classList.toggle("is-busy", next === "transcribing");
      mic.disabled = next === "transcribing";
      mic.setAttribute("aria-pressed", String(next === "recording"));
      mic.setAttribute(
        "aria-label",
        next === "recording"
          ? "Oprește înregistrarea"
          : next === "transcribing"
            ? "Transcriem înregistrarea"
            : "Înregistrează-ți vocea",
      );
    };

    const releaseMicrophone = () => {
      clearTimeout(stopTimer);
      stream?.getTracks().forEach((track) => track.stop());
      stream = null;
    };

    function insertTranscript(transcript) {
      const max = textarea.maxLength > 0 ? textarea.maxLength : Infinity;
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
      announce("Am adăugat înregistrarea. Verific-o, apoi alege Corectare sau Traducere.");
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

    async function startRecording() {
      if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
        announce("Înregistrarea nu este acceptată în acest browser.", true);
        return;
      }
      const mimeType = RECORDING_FORMATS.find((type) => MediaRecorder.isTypeSupported(type));
      if (!mimeType) {
        announce("Înregistrarea nu este acceptată în acest browser.", true);
        return;
      }
      setState("starting");
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (error) {
        setState("idle");
        announce(
          ["NotAllowedError", "SecurityError"].includes(error?.name)
            ? "Accesul la microfon a fost refuzat."
            : "Nu am putut folosi microfonul.",
          true,
        );
        return;
      }
      chunks = [];
      recorder = new MediaRecorder(stream, { mimeType, audioBitsPerSecond: 32000 });
      recorder.addEventListener("dataavailable", (event) => {
        if (event.data && event.data.size) chunks.push(event.data);
      });
      recorder.addEventListener("stop", () => {
        releaseMicrophone();
        const blob = new Blob(chunks, { type: recorder.mimeType || mimeType });
        chunks = [];
        if (blob.size) upload(blob);
        else {
          setState("idle");
          announce("Nu am înțeles înregistrarea. Încearcă din nou.", true);
        }
      });
      recorder.start();
      setState("recording");
      announce(`Înregistrăm… apasă din nou microfonul ca să oprești (cel mult ${maxSeconds} de secunde).`);
      stopTimer = setTimeout(stopRecording, maxSeconds * 1000);
    }

    function stopRecording() {
      clearTimeout(stopTimer);
      if (recorder && recorder.state !== "inactive") recorder.stop();
      else releaseMicrophone();
    }

    mic.addEventListener("click", () => {
      if (state === "recording") stopRecording();
      else if (state === "idle") startRecording();
    });
    window.addEventListener("pagehide", stopRecording);
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
