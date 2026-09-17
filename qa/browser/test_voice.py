"""Voice input (live and recorded transcription) and British speech."""
import time
from decimal import Decimal
from unittest.mock import patch

from django.test import override_settings
from playwright.sync_api import expect

from apps.analytics.models import AudioUsageEvent
from apps.assistant.models import RealtimeTranscriptionSession
from apps.assistant.services.voice import AudioUsage
from apps.assistant.tests.examples import correction_result
from .base import BrowserTestCase
from .mocks import ACTION, BRITISH, COMPLETED, DELTA, MEDIA_MOCKS, ROMANIAN


class VoiceChecks(BrowserTestCase):
    @override_settings(VOICE_REALTIME_ENABLED=False)  # The kill switch: record, stop, then transcribe the recording.
    def test_voice_input_and_british_speech_controls(self):
        transcript = "Let's meet behind the house."
        transcribe = patch("apps.assistant.voice_views.transcribe", return_value=(
            transcript, AudioUsage(model="gpt-transcribe", audio_seconds=Decimal("2.00")))).start()
        speak = patch("apps.assistant.voice_views.synthesize_speech", return_value=(
            b"ID3fake-mp3", AudioUsage(model="gpt-4o-mini-tts", voice="cedar", input_tokens=8, output_tokens=90,
                                       total_tokens=98))).start()
        self.page.add_init_script(MEDIA_MOCKS)
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.goto(self.live_server_url)
        mic = self.page.get_by_role("button", name="Înregistrează-ți vocea")
        expect(mic).to_be_visible()
        self.assertTrue(self.page.evaluate("document.querySelector('.textarea-wrap').contains(document.querySelector('[data-voice-record]'))"))
        layout = self.page.evaluate("""() => ({mic: document.querySelector('[data-voice-record]').getBoundingClientRect().toJSON(),
            box: document.querySelector('#text').getBoundingClientRect().toJSON(),
            count: document.querySelector('.mobile-count').getBoundingClientRect().toJSON(),
            scrollWidth: document.documentElement.scrollWidth})""")
        self.assertGreaterEqual(layout["mic"]["width"], 44)
        self.assertLessEqual(layout["mic"]["right"], layout["box"]["right"])
        self.assertLessEqual(layout["mic"]["bottom"], layout["box"]["bottom"])
        self.assertLessEqual(layout["count"]["right"], layout["mic"]["left"])
        self.assertLessEqual(layout["scrollWidth"], 390)
        # An empty box invites the learner to speak or type instead of showing "0/2000".
        expect(self.page.locator(".mobile-count .count-hint")).to_have_text("Scrie în acest ecran sau vorbește aici")
        expect(self.page.locator(".mobile-count .count-hint")).to_be_visible()
        expect(self.page.locator(".mobile-count .count-value")).to_be_hidden()
        self.page.locator("#text").fill("Hello")
        expect(self.page.locator(".mobile-count .count-value")).to_have_text("5/2000")
        expect(self.page.locator(".mobile-count .count-hint")).to_be_hidden()
        mic.click()
        expect(self.page.get_by_role("button", name="Oprește înregistrarea")).to_have_attribute("aria-pressed", "true")
        self.page.get_by_role("button", name="Oprește înregistrarea").click()
        expect(self.page.locator("#text")).to_have_value(f"Hello {transcript}")
        expect(self.page.locator(".mobile-count [data-count]")).to_have_text(str(len(f"Hello {transcript}")))
        expect(self.page.locator("#voice-status")).to_contain_text(f"Am adăugat înregistrarea. Verific-o, apoi apasă „{ACTION}”.")
        self.assertGreaterEqual(self.page.evaluate("window.__tracksStopped"), 1)
        transcribe.assert_called_once()

        self.page.locator("#text").fill(correction_result().original_text)
        self.submit()
        correction_speaker = self.page.get_by_role("button", name="Ascultă varianta corectă în engleză britanică")
        expect(correction_speaker).to_have_count(1)
        expect(self.page.get_by_role("button", name="Ascultă varianta naturală în engleză britanică")).to_have_count(0)
        correction_speaker.click()
        expect(correction_speaker).to_have_attribute("aria-pressed", "true")
        correction_speaker.click()
        expect(correction_speaker).to_have_attribute("aria-pressed", "false")
        correction_speaker.click()
        expect(correction_speaker).to_have_attribute("aria-pressed", "true")
        self.assertEqual(speak.call_count, 1)  # The second play came from the page's audio cache.

        self.page.set_viewport_size({"width": 1440, "height": 900})
        self.page.locator("#text").fill("Native example: I didn't went to work yesterday.")
        self.submit()
        native_speaker = self.page.get_by_role("button", name="Ascultă varianta naturală în engleză britanică")
        expect(native_speaker).to_have_count(1)
        native_speaker.click()
        expect(native_speaker).to_have_attribute("aria-pressed", "true")
        new_correction_speaker = self.page.get_by_role("button", name="Ascultă varianta corectă în engleză britanică")
        new_correction_speaker.click()
        expect(new_correction_speaker).to_have_attribute("aria-pressed", "true")
        expect(native_speaker).to_have_attribute("aria-pressed", "false")
        self.assertEqual(speak.call_count, 3)  # A new result carries a new token, so it is fetched once.

        self.page.locator("#text").fill(ROMANIAN)
        self.submit()
        british_speaker = self.page.get_by_role("button", name="Ascultă în engleză britanică")
        british_speaker.click()
        expect(british_speaker).to_have_attribute("aria-pressed", "true")
        self.assertEqual(speak.call_args.args[0], BRITISH)  # The useful English is spoken, never the Romanian.
        self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), 1440)
        self.page.screenshot(path=str(self.artifacts / "voice-controls-1440.png"), full_page=True)
        self.assertEqual(self.errors, [])

    def test_transcript_state_replaces_revisions_and_never_cuts_words(self):
        self.page.goto(self.live_server_url)
        out = self.page.evaluate("""() => {
          const T = window.CorectTranscript, out = {};
          let s = T.create();
          s.applyDelta("a", " I"); s.applyDelta("a", " would"); out.partial = s.snapshot().text;
          s.applyDelta("a", " like"); out.grown = s.snapshot().text;
          s.complete("a", "I would like."); out.revised = s.snapshot().text;
          s.applyDelta("b", " to meet"); out.next = s.snapshot().text;
          s.complete("b", "To meet you."); out.final = s.snapshot().text;
          s = T.create({ before: "I think", after: " tomorrow." });
          s.applyDelta("a", " we should"); s.applyDelta("a", " meet"); out.caret = s.snapshot();
          s = T.create({ before: "Hello", after: "!" }); s.applyDelta("a", " there"); out.punctuation = s.snapshot().text;
          s = T.create({ before: "Hi", maxLength: 12 });
          out.fits = s.applyDelta("a", " there"); out.limit = s.applyDelta("a", " friend"); out.limited = s.snapshot().text;
          s = T.create({ maxLength: 5 }); s.applyDelta("a", " pia"); out.fragment = s.applyDelta("a", "ță mare");
          out.fragmentText = s.snapshot().text;
          s = T.create(); s.applyDelta("late", " world"); s.applyDelta("early", " Hello"); s.place("late", "early");
          out.ordered = s.snapshot().text;
          return out;
        }""")
        self.assertEqual((out["partial"], out["grown"], out["revised"], out["next"], out["final"]),
                         ("I would", "I would like", "I would like.", "I would like. to meet", "I would like. To meet you."))
        self.assertEqual(out["caret"], {"text": "I think we should meet tomorrow.", "caret": len("I think we should meet")})
        self.assertEqual(out["punctuation"], "Hello there!")
        self.assertEqual((out["fits"], out["limit"], out["limited"]), ("ok", "limit", "Hi there"))
        self.assertEqual((out["fragment"], out["fragmentText"]), ("limit", ""))
        self.assertEqual(out["ordered"], "Hello world")
        self.assertEqual(self.errors, [])

    @override_settings(VOICE_TRANSCRIBE_LIMIT_MINUTE=100)
    def test_live_transcription_writes_words_while_the_learner_speaks(self):
        self.start_live_mocks()
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.goto(self.live_server_url)
        text = self.page.locator("#text")
        text.fill("I think tomorrow.")
        text.evaluate("box => box.setSelectionRange(7, 7)")
        self.page.evaluate("window.__rt.holdOpen = true")
        self.page.get_by_role("button", name="Înregistrează-ți vocea").click()
        overlay = self.page.locator("#voice-overlay")  # Until the connection listens, a waiting screen covers the page.
        expect(overlay).to_be_visible()
        expect(overlay).to_contain_text("Pornim microfonul…")
        self.page.wait_for_function("window.__rt.channel !== null && window.__rt.channel.readyState === 'connecting' && window.__rt.pc !== null")
        self.page.wait_for_function("window.__rt.sessionRequests.length === 1")
        self.page.wait_for_timeout(100)
        self.page.evaluate("window.__rt.open()")
        stop = self.page.get_by_role("button", name="Oprește transcrierea live")
        expect(stop).to_have_attribute("aria-pressed", "true")
        expect(overlay).to_be_hidden()
        expect(stop.locator(".stop-icon")).to_be_visible()
        expect(stop.locator(".mic-icon")).to_be_hidden()
        expect(self.page.locator("#voice-status")).to_contain_text("Ascult… Vorbește normal. Textul apare pe măsură ce vorbești.")
        self.assertEqual(self.offers, ["Bearer ek_browser_test"])  # The browser only ever holds the short-lived secret.
        expect(text).not_to_be_editable()
        expect(self.page.locator("[data-clear-text]")).to_be_hidden()  # Nothing can be cleared while words arrive.
        expect(self.page.get_by_role("button", name=ACTION, exact=True)).to_be_disabled()
        seen = []
        for delta in (" we", " should", " meet"):
            self.emit(type=DELTA, item_id="item_1", delta=delta)
            seen.append(text.input_value())
        self.assertEqual(seen, ["I think we tomorrow.", "I think we should tomorrow.", "I think we should meet tomorrow."])
        expect(self.page.locator(".mobile-count [data-count]")).to_have_text(str(len(seen[-1])))
        layout = self.page.evaluate("""() => ({mic: document.querySelector('[data-voice-record]').getBoundingClientRect().toJSON(),
            box: document.querySelector('#text').getBoundingClientRect().toJSON(),
            count: document.querySelector('.mobile-count').getBoundingClientRect().toJSON(),
            scrollWidth: document.documentElement.scrollWidth})""")
        self.assertGreaterEqual(layout["mic"]["width"], 44)
        self.assertLessEqual(layout["mic"]["right"], layout["box"]["right"])
        self.assertLessEqual(layout["count"]["right"], layout["mic"]["left"])
        self.assertLessEqual(layout["scrollWidth"], 390)
        self.page.screenshot(path=str(self.artifacts / "live-transcription-390.png"))

        stop.click()
        self.wait_for_commit()
        self.emit(type="input_audio_buffer.committed", item_id="item_1", previous_item_id=None)
        self.emit(type=DELTA, item_id="item_2", delta=" Late")  # Speech after the stop is not added.
        self.emit(type=COMPLETED, item_id="item_1", transcript="we should meet", usage={"type": "duration", "seconds": 1})
        expect(self.page.locator("#voice-status")).to_contain_text(f"Gata. Poți modifica textul, apoi apasă „{ACTION}”.")
        expect(text).to_have_value("I think we should meet tomorrow.")
        expect(text).to_be_editable()
        expect(self.page.get_by_role("button", name=ACTION, exact=True)).to_be_enabled()
        expect(self.page.get_by_role("button", name="Înregistrează-ți vocea")).to_have_attribute("aria-pressed", "false")
        self.assertGreaterEqual(self.page.evaluate("window.__rt.tracksStopped"), 1)
        self.assertTrue(self.page.evaluate("window.__rt.pcClosed"))
        event = self.ledger_row(stt_mode="realtime")
        self.assertEqual((event.status, event.model, event.metering_source, event.audio_seconds),
                         ("success", "gpt-live-transcribe", "provider", Decimal("1.00")))
        self.assertEqual(self.in_database_thread(AudioUsageEvent.objects.filter(operation="transcription").count), 1)
        self.file_transcribe.assert_not_called()  # One transcription service per recording: never both.
        self.assertIsNone(self.usage(self.ANONYMOUS_ACTOR))  # Speaking into the box used no naturalisation.

        text.fill(correction_result().original_text)
        self.submit()
        expect(self.page.locator(".result-text")).to_have_text(correction_result().corrected_text)
        self.assertEqual(self.usage(self.ANONYMOUS_ACTOR), 1)  # Submitting the text is the one use.
        self.assertEqual(self.errors, [])

    @override_settings(VOICE_TRANSCRIBE_LIMIT_MINUTE=100, VOICE_TRAILING_AUDIO_MS=300, VOICE_FINAL_TRANSCRIPT_MS=1000)
    def test_stop_keeps_the_microphone_open_briefly_then_ends_on_the_final_transcript(self):
        self.start_live_mocks()
        finishes = []
        self.page.on("request", lambda request: finishes.append(request.post_data or "")
                     if request.url.endswith("/realtime-transcription/finish/") else None)
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.goto(self.live_server_url)
        text = self.page.locator("#text")
        mic = self.page.get_by_role("button", name="Înregistrează-ți vocea")
        stop = self.page.get_by_role("button", name="Oprește transcrierea live")
        mic.click()
        expect(stop).to_be_visible()
        self.emit(type=DELTA, item_id="item_1", delta=" Nu cred că ajung la muncă înainte de nouă.")
        self.page.evaluate("window.__rt.stopAt = performance.now()")
        stop.click()
        self.wait_for_commit()
        moments = self.page.evaluate("({stop: window.__rt.stopAt, muted: window.__rt.mutedAt, commit: window.__rt.commitAt})")
        self.assertGreaterEqual(moments["muted"] - moments["stop"], 250)  # The microphone stayed on for the trailing window.
        self.assertGreaterEqual(moments["commit"], moments["muted"])  # Muted first, then committed.
        self.emit(type=COMPLETED, item_id="item_1", transcript="Nu cred că ajung la muncă înainte de nouă.",
                  usage={"type": "duration", "seconds": 3})
        expect(mic).to_have_attribute("aria-pressed", "false")
        timings = self.page.evaluate("window.CorectVoice.lastTimings")
        for name in ("mic_ms", "session_ms", "connect_ms", "startup_ms", "first_word_ms", "finalise_ms"):
            self.assertIsNotNone(timings[name], name)
            self.assertEqual(timings[name], int(timings[name]), name)
        self.assertTrue(timings["final_received"])
        self.assertLess(timings["finalise_ms"], 1300)  # Ended by the final transcript, not by the safety timeout.
        event = self.ledger_row(stt_mode="realtime")
        self.assertEqual((event.final_received, event.finalise_ms), (True, timings["finalise_ms"]))
        self.assertIsNotNone(event.startup_ms)
        self.assertTrue(any('name="finalise_ms"' in body for body in finishes))
        self.assertFalse(any("Nu cred" in body for body in finishes))  # Timings only: never the transcript.

        mic.click()  # No final transcript this time: the short safety timeout ends the wait and the text stays.
        expect(stop).to_be_visible()
        self.emit(type=DELTA, item_id="item_2", delta=" Mersi mult!")
        stop.click()
        self.wait_for_commit(count=2)
        committed = time.time()
        expect(mic).to_have_attribute("aria-pressed", "false", timeout=4000)
        self.assertGreaterEqual(time.time() - committed, 0.8)
        self.assertFalse(self.page.evaluate("window.CorectVoice.lastTimings.final_received"))
        expect(text).to_have_value("Nu cred că ajung la muncă înainte de nouă. Mersi mult!")
        self.assertFalse(self.ledger_row(stt_mode="realtime", final_received=False).final_received)

        self.submit()  # Spoken Romanian is sent like typed Romanian.
        expect(self.page.locator("#result").get_by_role("heading", level=2)).to_have_text("În engleză britanică")
        self.assertEqual(self.errors, [])

    @override_settings(VOICE_TRANSCRIBE_LIMIT_MINUTE=100, VOICE_TRAILING_AUDIO_MS=0, VOICE_FINAL_TRANSCRIPT_MS=500)
    def test_microphone_permission_decides_whether_the_session_request_overlaps(self):
        self.start_live_mocks()
        mic_label, stop_label = "Înregistrează-ți vocea", "Oprește transcrierea live"
        status = self.page.locator("#voice-status")

        # Nothing connects to the provider before the learner reaches for the microphone.
        self.page.goto(self.live_server_url)
        preconnect = self.page.locator('link[rel="preconnect"][href="https://api.openai.com"]')
        expect(preconnect).to_have_count(0)
        mic = self.page.get_by_role("button", name=mic_label)
        mic.dispatch_event("pointerdown")
        mic.dispatch_event("pointerdown")
        expect(preconnect).to_have_count(1)

        def attempt(permission, delay=0, error=None):
            self.page.goto(self.live_server_url)
            self.page.evaluate("([permission, delay, error]) => Object.assign(window.__rt, "
                               "{permission, gumDelay: delay, gumError: error})", [permission, delay, error])
            self.page.get_by_role("button", name=mic_label).click()

        def moments():
            return self.page.evaluate("({session: window.__rt.sessionRequests[0], mic: window.__rt.gumAt})")

        def stop_listening():
            self.page.get_by_role("button", name=stop_label).click()
            expect(self.page.get_by_role("button", name=mic_label)).to_have_attribute("aria-pressed", "false")

        attempt("granted", delay=400)
        expect(self.page.get_by_role("button", name=stop_label)).to_be_visible()
        order = moments()
        self.assertLess(order["session"], order["mic"])  # Requested while the microphone was still starting.
        stop_listening()

        attempt("prompt", delay=200)
        expect(self.page.get_by_role("button", name=stop_label)).to_be_visible()
        order = moments()
        self.assertGreater(order["session"], order["mic"])  # Only once the learner has allowed the microphone.
        stop_listening()

        sessions = self.in_database_thread(RealtimeTranscriptionSession.objects.count)
        attempt("denied", error="NotAllowedError")
        expect(status).to_contain_text("Accesul la microfon a fost refuzat.")
        self.assertEqual(self.page.evaluate("window.__rt.sessionRequests.length"), 0)
        self.assertEqual(self.in_database_thread(RealtimeTranscriptionSession.objects.count), sessions)  # No quota used.

        attempt("granted", error="NotReadableError")  # Allowed, but the microphone is busy.
        expect(status).to_contain_text("Nu am putut folosi microfonul.")
        self.assertEqual(self.page.evaluate("window.__rt.sessionRequests.length"), 1)
        closed = self.ledger_row(error_code="realtime_connect_failed")
        self.assertEqual((closed.audio_seconds, closed.estimated_cost), (Decimal("0.00"), Decimal("0")))  # No audio, $0.
        self.assertEqual(self.errors, [])

    @override_settings(VOICE_TRANSCRIBE_LIMIT_MINUTE=100)
    def test_live_transcription_stops_by_itself_after_three_seconds_without_new_words(self):
        self.start_live_mocks()
        self.page.goto(self.live_server_url)
        text = self.page.locator("#text")
        mic = self.page.get_by_role("button", name="Înregistrează-ți vocea")
        stop = self.page.get_by_role("button", name="Oprește transcrierea live")
        example = self.page.locator(".example-prompt")
        expect(example).to_be_visible()  # The empty box shows example sentences until the microphone starts.

        mic.click()  # Nothing is said at all: the microphone still stops after three seconds.
        expect(stop).to_be_visible()
        expect(example).to_be_hidden()
        started = time.time()
        self.wait_for_commit()
        self.assertGreaterEqual(time.time() - started, 2.5)
        self.emit(type="error", error={"type": "invalid_request_error", "code": "input_audio_buffer_commit_empty"})
        expect(self.page.locator("#voice-status")).to_contain_text("Nu am auzit nimic")
        expect(text).to_have_value("")
        expect(text).to_be_editable()
        expect(mic).to_have_attribute("aria-pressed", "false")
        self.assertEqual(self.ledger_row(stt_mode="realtime").status, "success")

        mic.click()  # Words, then silence: every new word restarts the three seconds.
        expect(stop).to_be_visible()
        self.page.wait_for_timeout(2000)
        self.emit(type=DELTA, item_id="item_1", delta=" Hello there")
        expect(text).to_have_value("Hello there")
        expect(example).to_be_hidden()  # Live transcript text and example sentences never appear together.
        started = time.time()
        self.wait_for_commit(count=2)
        self.assertGreaterEqual(time.time() - started, 2.8)  # Three seconds after the last word, less the round trip.
        self.emit(type=COMPLETED, item_id="item_1", transcript="Hello there.", usage={"type": "duration", "seconds": 6})
        expect(self.page.locator("#voice-status")).to_contain_text("Nu te-am mai auzit, așa că am oprit microfonul.")
        expect(text).to_have_value("Hello there.")
        expect(text).to_be_editable()
        expect(self.page.get_by_role("button", name="Înregistrează-ți vocea")).to_have_attribute("aria-pressed", "false")
        self.assertGreaterEqual(self.page.evaluate("window.__rt.tracksStopped"), 1)
        self.assertTrue(self.page.evaluate("window.__rt.pcClosed"))
        event = self.ledger_row(stt_mode="realtime", metering_source="provider")
        self.assertEqual(event.status, "success")
        self.assertEqual(self.errors, [])

    @override_settings(VOICE_TRANSCRIBE_LIMIT_MINUTE=100)
    def test_live_transcription_interruption_keeps_text_and_connect_failure_falls_back(self):
        self.start_live_mocks()
        self.page.goto(self.live_server_url)
        text = self.page.locator("#text")
        self.page.get_by_role("button", name="Înregistrează-ți vocea").click()
        expect(self.page.get_by_role("button", name="Oprește transcrierea live")).to_be_visible()
        self.emit(type=DELTA, item_id="item_1", delta=" Keep this")
        self.page.evaluate("window.__rt.drop()")
        expect(self.page.locator("#voice-status")).to_contain_text(
            "Conexiunea pentru transcriere live s-a întrerupt. Am păstrat textul primit până acum.")
        expect(text).to_have_value("Keep this")
        expect(text).to_be_editable()
        self.assertEqual(self.ledger_row(error_code="realtime_interrupted").stt_mode, "realtime")
        self.file_transcribe.assert_not_called()  # Audio already sent live is never sent again to file transcription.

        self.sdp_status = 500  # Live transcription cannot connect before any speech is sent.
        self.page.goto(self.live_server_url)
        self.page.get_by_role("button", name="Înregistrează-ți vocea").click()
        expect(self.page.locator("#voice-status")).to_contain_text("Transcrierea live nu e disponibilă acum")
        stop = self.page.get_by_role("button", name="Oprește înregistrarea")
        expect(stop).to_have_attribute("aria-pressed", "true")
        self.assertEqual(self.page.evaluate("window.__rt.gum"), 1)  # The same microphone stream is reused.
        stop.click()
        expect(text).to_have_value("Recorded instead.")
        expect(self.page.locator(".example-prompt")).to_be_hidden()
        self.file_transcribe.assert_called_once()
        self.ledger_row(error_code="realtime_connect_failed")
        self.ledger_row(stt_mode="file")
        self.assertEqual(self.errors, [])

    @override_settings(VOICE_TRANSCRIBE_LIMIT_MINUTE=100)
    def test_live_transcription_stops_at_the_character_limit_and_when_the_page_closes(self):
        self.start_live_mocks()
        self.page.goto(self.live_server_url)
        text, status = self.page.locator("#text"), self.page.locator("#voice-status")
        start = "x" * 1990
        text.fill(start)
        self.page.get_by_role("button", name="Înregistrează-ți vocea").click()
        expect(self.page.get_by_role("button", name="Oprește transcrierea live")).to_be_visible()
        self.emit(type=DELTA, item_id="item_1", delta=" hello")
        expect(text).to_have_value(start + " hello")
        self.emit(type=DELTA, item_id="item_1", delta=" wonderful")
        self.wait_for_commit()
        self.emit(type=COMPLETED, item_id="item_1", transcript="hello wonderful", usage={"type": "duration", "seconds": 1})
        expect(status).to_contain_text("Ai ajuns la limita de 2.000 de caractere. Am oprit microfonul.")
        expect(text).to_have_value(start + " hello")
        expect(text).to_be_editable()
        self.assertEqual(self.ledger_row(stt_mode="realtime").status, "success")

        self.page.get_by_role("button", name="Înregistrează-ți vocea").click()
        expect(self.page.get_by_role("button", name="Oprește transcrierea live")).to_be_visible()
        self.emit(type=DELTA, item_id="item_9", delta=" bye")
        self.page.goto(self.live_server_url + "/confidentialitate/")
        # The beacon leaves with the unloading page; under a busy full run the server can take a while to record it.
        closed = self.ledger_row(timeout=20, error_code="realtime_page_closed")
        self.assertEqual(closed.metering_source, "stream_duration")
        self.assertEqual(self.errors, [])

    @override_settings(VOICE_REALTIME_ENABLED=False)
    def test_unsupported_browser_is_told_and_nothing_is_sent(self):
        self.page.add_init_script("delete window.MediaRecorder; delete window.RTCPeerConnection;")
        self.page.goto(self.live_server_url)
        self.page.get_by_role("button", name="Înregistrează-ți vocea").click()
        expect(self.page.locator("#voice-status")).to_contain_text("Înregistrarea nu este acceptată în acest browser.")
        self.assertEqual(self.in_database_thread(AudioUsageEvent.objects.count), 0)
        self.assertEqual(self.errors, [])
