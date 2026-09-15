"""Live microphone latency benchmark against a running server (real, billed OpenAI calls; opt-in with --live).

Chromium plays a TTS-generated WAV as its microphone. An init script times getUserMedia, the session request, the SDP
exchange, the data channel, the provider's transcript events and the microphone states from outside the page, so runs
before and after a change are measured the same way; the page's own `window.CorectVoice.lastTimings` is saved too.

Needs the development requirements (`pip install -r requirements-dev.txt`, `python -m playwright install chromium`).
Raise VOICE_TRANSCRIBE_LIMIT_MINUTE on the server being measured, or runs will be rate limited.
"""
import json
import re
import time
import unicodedata
import wave
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.analytics.stats import percentiles
from apps.assistant.evals.live import require_live
from apps.assistant.services.provider import openai_client

SENTENCES = {
    "en": "I'd like to book an appointment with the doctor for next Tuesday morning, please.",
    "ro": "Aș vrea să fac o programare la medicul de familie marțea viitoare dimineață.",
}
SPEAKING = {"en": "Speak in natural British English at a normal conversational pace.",
            "ro": "Vorbește în limba română, natural, cu un ritm normal de conversație."}
RATE = 24000
METRICS = ("mic_ms", "session_ms", "sdp_ms", "startup_ms", "first_text_after_speech_ms", "commit_after_stop_ms",
           "completed_after_commit_ms", "finalise_ms")

INIT = r"""
(() => {
  const b = window.__bench = {marks: {}, states: []};
  const now = () => performance.now();
  const mark = (k) => { if (!(k in b.marks)) b.marks[k] = now(); };
  const md = navigator.mediaDevices;
  const gum = md.getUserMedia.bind(md);
  md.getUserMedia = async (c) => { mark("gum_start"); const s = await gum(c); mark("gum_done"); return s; };
  const f = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const url = typeof input === "string" ? input : input.url;
    const key = url.includes("realtime-transcription/session") ? "session" : url.includes("/realtime/calls") ? "sdp" : null;
    if (key) mark(key + "_start");
    const r = await f(input, init);
    if (key) mark(key + "_done");
    return r;
  };
  const PC = window.RTCPeerConnection;
  const Wrapped = function (...a) {
    const pc = new PC(...a);
    const cdc = pc.createDataChannel.bind(pc);
    pc.createDataChannel = (...x) => {
      const dc = cdc(...x);
      dc.addEventListener("open", () => mark("dc_open"));
      const send = dc.send.bind(dc);
      dc.send = (data) => {
        try { if (JSON.parse(data).type === "input_audio_buffer.commit") b.marks.commit = now(); } catch {}
        return send(data);
      };
      dc.addEventListener("message", (m) => {
        try {
          const e = JSON.parse(m.data), t = now();
          if (e.type.endsWith("input_audio_transcription.delta") && !("first_delta" in b.marks)) b.marks.first_delta = t;
          if (e.type.endsWith("input_audio_transcription.completed")) b.marks.completed = t;
        } catch {}
      });
      return dc;
    };
    return pc;
  };
  Wrapped.prototype = PC.prototype;
  window.RTCPeerConnection = Wrapped;
  document.addEventListener("voice-state", (e) => {
    b.states.push([e.detail.state, Math.round(now())]);
    if (e.detail.state === "listening") mark("listening");
    if (e.detail.state === "idle" && "stop" in b.marks) mark("idle_after_stop");
  });
})();
"""


def words(text):
    text = unicodedata.normalize("NFKD", text.lower())
    return re.findall(r"[a-z0-9']+", "".join(ch for ch in text if not unicodedata.combining(ch)).replace("’", "'"))


class Command(BaseCommand):
    help = "Measures microphone start-up, first words and stop-to-final-transcript latency in Chromium (use --live)."

    def add_arguments(self, parser):
        parser.add_argument("--live", action="store_true", help="Confirm real, billed provider calls.")
        parser.add_argument("--base-url", default="http://127.0.0.1:8000")
        parser.add_argument("--runs", type=int, default=10)
        parser.add_argument("--languages", default="en,ro")
        parser.add_argument("--trailing-ms", default="server",
                            help="Comma-separated trailing windows to try (debug override), or 'server' for the setting.")
        parser.add_argument("--stop-after-ms", type=int, default=300, help="Delay between the end of speech and Stop.")
        parser.add_argument("--lead-seconds", type=float, default=3.0, help="Silence before speech in the WAV.")
        parser.add_argument("--label", default="run")
        parser.add_argument("--headed", action="store_true")
        parser.add_argument("--json-out")

    def handle(self, *args, **options):
        require_live(options)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise CommandError("Install requirements-dev.txt and run `python -m playwright install chromium`.") from None
        languages = [code.strip() for code in options["languages"].split(",") if code.strip()]
        trailing_values = [value.strip() for value in options["trailing_ms"].split(",") if value.strip()]
        path = Path(options["json_out"] or settings.BASE_DIR / "artifacts" / "benchmarks" /
                    f"voice-{options['label']}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        with sync_playwright() as playwright:
            for language in languages:
                wav, speech_seconds = self.clip(language, options["lead_seconds"])
                browser = playwright.chromium.launch(headless=not options["headed"], args=[
                    "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                    f"--use-file-for-fake-audio-capture={wav.as_posix()}%noloop"])
                try:
                    for trailing in trailing_values:
                        for run in range(options["runs"]):
                            try:
                                row = self.run_once(browser, options, language, speech_seconds, trailing)
                            except Exception as exc:  # noqa: BLE001 - a failed run is reported, not fatal
                                row = {"language": language, "trailing": trailing, "error": type(exc).__name__}
                            row.update(label=options["label"], run=run)
                            rows.append(row)
                            with path.open("a", encoding="utf-8") as handle:
                                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                            time.sleep(1)
                finally:
                    browser.close()
        self.report(rows, languages, trailing_values)
        self.stdout.write(f"runs: {path}")

    def clip(self, language, lead_seconds):
        folder = settings.BASE_DIR / "artifacts" / "voice-bench"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{language}-lead{int(lead_seconds * 10)}.wav"
        if not path.exists():
            audio = openai_client().audio.speech.create(
                model=settings.OPENAI_TTS_MODEL, voice=settings.OPENAI_TTS_VOICE, input=SENTENCES[language],
                instructions=SPEAKING[language], response_format="pcm").content
            with wave.open(str(path), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(RATE)
                out.writeframes(b"\x00\x00" * int(RATE * lead_seconds) + audio + b"\x00\x00" * int(RATE * 2))
        with wave.open(str(path), "rb") as audio:
            total = audio.getnframes() / audio.getframerate()
        return path, total - lead_seconds - 2

    def run_once(self, browser, options, language, speech_seconds, trailing):
        context = browser.new_context(permissions=["microphone"], viewport={"width": 1280, "height": 900})
        context.add_init_script(INIT)
        if trailing != "server":
            context.add_init_script(f"localStorage.setItem('corectVoiceDebug', '1'); "
                                    f"localStorage.setItem('corectVoiceTrailingMs', '{int(trailing)}');")
        page = context.new_page()
        try:
            page.goto(options["base_url"] + "/")
            acknowledge = page.get_by_role("button", name="Am înțeles")
            if acknowledge.count() and acknowledge.first.is_visible():
                acknowledge.first.click()
            page.wait_for_timeout(300)
            page.evaluate("() => { window.__bench.marks.click = performance.now();"
                          " document.querySelector('[data-voice-record]').click(); }")
            page.wait_for_function("() => 'listening' in window.__bench.marks", timeout=20000)
            speech_end = int((options["lead_seconds"] + speech_seconds) * 1000) + options["stop_after_ms"]
            page.wait_for_function(f"() => performance.now() >= window.__bench.marks.gum_done + {speech_end}",
                                   timeout=30000, polling=20)
            page.evaluate("() => { window.__bench.marks.stop = performance.now();"
                          " document.querySelector('[data-voice-record]').click(); }")
            page.wait_for_function("() => 'idle_after_stop' in window.__bench.marks", timeout=20000)
            page.wait_for_timeout(200)
            data = page.evaluate("() => ({marks: window.__bench.marks, text: document.getElementById('text').value,"
                                 " page: window.CorectVoice ? window.CorectVoice.lastTimings : null})")
        finally:
            context.close()
        marks = data["marks"]
        span = lambda start, end: round(marks[end] - marks[start]) if start in marks and end in marks else None  # noqa: E731
        expected, heard = words(SENTENCES[language]), words(data["text"])
        speech_start = marks["gum_done"] + options["lead_seconds"] * 1000
        return {
            "language": language, "trailing": trailing, "mic_ms": span("click", "gum_done"),
            "session_ms": span("session_start", "session_done"), "sdp_ms": span("sdp_start", "sdp_done"),
            "startup_ms": span("click", "listening"),
            "first_text_after_speech_ms": round(marks["first_delta"] - speech_start) if "first_delta" in marks else None,
            "commit_after_stop_ms": span("stop", "commit"), "completed_after_commit_ms": span("commit", "completed"),
            "finalise_ms": span("stop", "idle_after_stop"),
            "final_received": "completed" in marks and "commit" in marks and marks["completed"] >= marks["commit"],
            "last_word_kept": bool(heard) and expected[-1] in heard[-3:],
            "word_recall": round(sum(word in heard for word in expected) / len(expected), 2),
            "page_timings": data["page"],
        }

    def report(self, rows, languages, trailing_values):
        for language in languages:
            for trailing in trailing_values:
                runs = [row for row in rows if row["language"] == language and row["trailing"] == trailing]
                ok = [row for row in runs if "error" not in row]
                self.stdout.write(f"\n{language} · trailing {trailing}: {len(ok)}/{len(runs)} runs")
                for metric in METRICS:
                    summary = percentiles([row.get(metric) for row in ok])
                    if summary["n"]:
                        self.stdout.write(f"  {metric:28} p50={summary['p50']:>6} p95={summary['p95']:>6} "
                                          f"min={summary['min']:>6} max={summary['max']:>6}")
                if ok:
                    self.stdout.write(f"  final_received {sum(row['final_received'] for row in ok)}/{len(ok)} · "
                                      f"last_word_kept {sum(row['last_word_kept'] for row in ok)}/{len(ok)} · "
                                      f"word_recall p50 {percentiles([row['word_recall'] for row in ok]).get('p50')}")
