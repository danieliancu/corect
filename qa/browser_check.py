"""Opt-in Chromium checks. Run separately with the documented test settings."""
import json
import time
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from playwright.sync_api import sync_playwright, expect

from apps.assistant.tests.examples import correction_result, translation_result
from apps.assistant.services.openai_client import AssistantError
from apps.assistant.services.voice import AudioUsage

# Browser stand-ins for the microphone, MediaRecorder and audio playback, so no hardware or paid call is needed.
MEDIA_MOCKS = """(() => {
  const track = { stop() { window.__tracksStopped = (window.__tracksStopped || 0) + 1; } };
  if (!navigator.mediaDevices) Object.defineProperty(navigator, "mediaDevices", { value: {} });
  navigator.mediaDevices.getUserMedia = async () => ({ getTracks: () => [track] });
  class FakeRecorder extends EventTarget {
    static isTypeSupported(type) { return type.startsWith("audio/webm"); }
    constructor(stream, options) { super(); this.mimeType = options.mimeType; this.state = "inactive"; }
    start() { this.state = "recording"; }
    stop() {
      this.state = "inactive";
      const data = new Blob([new Uint8Array([0x1a, 0x45, 0xdf, 0xa3, 0, 0, 0, 0])], { type: this.mimeType });
      this.dispatchEvent(Object.assign(new Event("dataavailable"), { data }));
      this.dispatchEvent(new Event("stop"));
    }
  }
  window.MediaRecorder = FakeRecorder;
  // Keep the fake MP3 bytes from being decoded, which would fire a real media error.
  Object.defineProperty(HTMLMediaElement.prototype, "src", {
    configurable: true, get() { return this.__src || ""; }, set(value) { this.__src = value; },
  });
  HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };
  HTMLMediaElement.prototype.pause = function () { this.dispatchEvent(new Event("pause")); };
})();"""


@override_settings(RATE_LIMIT_MINUTE=100, RATE_LIMIT_DAY=1000)
class BrowserChecks(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.artifacts = Path(settings.BASE_DIR) / "artifacts" / "browser"
        cls.artifacts.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        self.runtime = sync_playwright().start()
        self.browser = self.runtime.chromium.launch()
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 1000})
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.correction = patch("apps.assistant.views.CorrectionService.correct", side_effect=self.mock_correct).start()
        self.translation = patch("apps.assistant.views.TranslationService.translate", return_value=translation_result()).start()

    def tearDown(self):
        self.context.close()
        self.browser.close()
        self.runtime.stop()
        patch.stopall()
        super().tearDown()

    @staticmethod
    def mock_correct(text):
        time.sleep(.25)  # Make loading-state assertions deterministic.
        if text == "Simulate failure":
            raise AssistantError("timeout", "A durat prea mult. Încearcă din nou.")
        result = correction_result()
        if text.startswith("Native example"):
            result.native_text = "I didn't make it to work yesterday."
        if text.startswith("Long example"):
            result.corrected_text = "I've been learning English for five years. " * 25
            result.corrections[0].explanation_ro = "După did/didn't folosim forma de bază a verbului. " * 10
        return result

    def test_responsive_workflow(self):
        measurements = []
        for width in (375, 390, 430, 768, 1024, 1440):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": 900})
                self.page.goto(self.live_server_url)
                expect(self.page.locator(".empty-result")).to_contain_text("Scrie ceva, apoi alege")
                self.page.screenshot(path=str(self.artifacts / f"home-{width}.png"), full_page=True)
                self.page.locator("#text").fill(correction_result().original_text)
                self.assertEqual(self.correction.call_count, len(measurements))
                self.page.get_by_role("button", name="Corectare", exact=True).click()
                expect(self.page.get_by_role("button", name="Traducem…", exact=True)).to_have_count(0)
                expect(self.page.locator(".action-buttons button").nth(1)).to_be_disabled()
                expect(self.page.locator(".result-text")).to_have_text(correction_result().corrected_text)
                expect(self.page.get_by_role("button", name="Corectare", exact=True)).to_be_enabled()
                self.page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
                self.page.screenshot(path=str(self.artifacts / f"correction-{width}.png"), full_page=True)
                layout = self.page.evaluate("""() => ({width: innerWidth, scrollWidth: document.documentElement.scrollWidth,
                    editor: document.querySelector('.editor-card').getBoundingClientRect().toJSON(),
                    result: document.querySelector('.result-card').getBoundingClientRect().toJSON()})""")
                self.assertLessEqual(layout["scrollWidth"], width)
                if width >= 1024:
                    self.assertGreater(layout["result"]["x"], layout["editor"]["x"])
                else:
                    self.assertGreater(layout["result"]["y"], layout["editor"]["y"])
                measurements.append(layout)
        (self.artifacts / "layout-checks.json").write_text(json.dumps(measurements, indent=2), encoding="utf-8")
        self.assertEqual(self.errors, [])

    def test_correct_scrolls_to_loading_and_keeps_result_in_view(self):
        self.page.emulate_media(reduced_motion="reduce")
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.goto(self.live_server_url)
        self.page.locator("#text").fill(correction_result().original_text)
        self.page.get_by_role("button", name="Corectare", exact=True).click()
        expect(self.page.locator("#result")).to_be_focused()
        # The result scrolls up to just below the sticky header, which stays in view.
        below_header = "document.querySelector('.site-header').getBoundingClientRect().bottom + 18"
        self.page.wait_for_function(f"document.querySelector('#result').getBoundingClientRect().top <= {below_header}")
        expect(self.page.locator(".correction-comparison")).to_be_visible()
        expect(self.page.locator(".sentence-before mark")).to_have_text("didn't went")
        expect(self.page.locator(".sentence-after mark")).to_have_text("didn't go")
        self.assertGreater(self.page.evaluate("window.scrollY"), 100)
        self.assertLessEqual(self.page.locator("#result").bounding_box()["y"], self.page.evaluate(below_header))
        self.assertEqual(self.page.locator(".site-header").bounding_box()["y"], 0)
        self.page.screenshot(path=str(self.artifacts / "correction-scrolled-390.png"))
        self.assertEqual(self.errors, [])

    def test_translation_error_recovery_menu_and_long_content(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.goto(self.live_server_url)
        self.page.locator("#text").fill(translation_result().original_text)
        self.page.get_by_role("button", name="Traducere", exact=True).click()
        expect(self.page.locator(".result-text")).to_have_text(translation_result().translated_text)
        self.page.locator("#text").fill("Simulate failure")
        self.page.get_by_role("button", name="Corectare", exact=True).click()
        expect(self.page.get_by_role("alert")).to_have_text("A durat prea mult. Încearcă din nou.")
        expect(self.page.locator("#text")).to_have_value("Simulate failure")
        expect(self.page.get_by_role("button", name="Corectare", exact=True)).to_be_enabled()
        self.page.locator("#text").fill("Long example")
        self.page.get_by_role("button", name="Corectare", exact=True).click()
        expect(self.page.locator(".result-text")).to_contain_text("I've been learning English")
        self.assertEqual(self.page.evaluate("document.documentElement.scrollWidth"), 390)
        self.page.screenshot(path=str(self.artifacts / "long-result-390.png"), full_page=True)
        self.page.locator("#navigation-menu summary").click()
        expect(self.page.get_by_role("navigation", name="Toate paginile")).to_be_visible()
        self.page.keyboard.press("Escape")
        expect(self.page.get_by_role("navigation", name="Toate paginile")).not_to_be_visible()
        expect(self.page.locator("#navigation-menu summary")).to_be_focused()
        self.assertEqual(self.errors, [])

    def test_authentication_history_practice_and_no_javascript(self):
        self.page.goto(self.live_server_url + "/accounts/signup/")
        self.page.get_by_label("Nume utilizator").fill("browser-learner")
        self.page.get_by_label("Email").fill("browser@example.com")
        self.page.locator("#id_password1").fill("Browser-test-password-815")
        self.page.locator("#id_password2").fill("Browser-test-password-815")
        self.page.get_by_role("button", name="Creează cont", exact=True).click()
        expect(self.page.locator("#text")).to_be_visible()
        self.page.locator("#text").fill(correction_result().original_text)
        self.page.get_by_role("button", name="Corectare", exact=True).click()
        expect(self.page.locator(".result-text")).to_be_visible()
        self.page.goto(self.live_server_url + "/history/")
        self.page.locator(".history-entry").click()
        expect(self.page.locator(".result-text")).to_have_text(correction_result().corrected_text)
        for path in ("mistakes", "progress", "practice", "settings", "accounts/profile"):
            self.page.goto(f"{self.live_server_url}/{path}/")
            self.assertEqual(self.page.locator("h1").count(), 1)
            self.page.screenshot(path=str(self.artifacts / (path.replace("/", "-") + ".png")), full_page=True)
        self.page.goto(self.live_server_url + "/practice/")
        self.page.get_by_label("see", exact=True).check()
        self.page.get_by_role("button", name="Verifică răspunsul").click()
        expect(self.page.get_by_role("status")).to_contain_text("Corect!")
        self.assertEqual(self.errors, [])
        no_js = self.browser.new_context(java_script_enabled=False, viewport={"width": 390, "height": 844})
        try:
            page = no_js.new_page()
            page.goto(self.live_server_url)
            page.locator("#text").fill(correction_result().original_text)
            page.get_by_role("button", name="Corectare", exact=True).click()
            expect(page.locator(".result-text")).to_have_text(correction_result().corrected_text)
            page.get_by_role("button", name="Traducere", exact=True).click()
            expect(page.locator(".result-text")).to_have_text(translation_result().translated_text)
        finally:
            no_js.close()

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
        self.page.locator("#text").fill("Hello")
        mic.click()
        expect(self.page.get_by_role("button", name="Oprește înregistrarea")).to_have_attribute("aria-pressed", "true")
        self.page.get_by_role("button", name="Oprește înregistrarea").click()
        expect(self.page.locator("#text")).to_have_value(f"Hello {transcript}")
        expect(self.page.locator(".mobile-count [data-count]")).to_have_text(str(len(f"Hello {transcript}")))
        expect(self.page.locator("#voice-status")).to_contain_text("Am adăugat înregistrarea")
        self.assertGreaterEqual(self.page.evaluate("window.__tracksStopped"), 1)
        transcribe.assert_called_once()

        self.page.locator("#text").fill(correction_result().original_text)
        self.page.get_by_role("button", name="Corectare", exact=True).click()
        correction_speaker = self.page.get_by_role("button", name="Ascultă corectura în engleză britanică")
        expect(correction_speaker).to_have_count(1)
        expect(self.page.get_by_role("button", name="Ascultă versiunea nativă în engleză britanică")).to_have_count(0)
        correction_speaker.click()
        expect(correction_speaker).to_have_attribute("aria-pressed", "true")
        correction_speaker.click()
        expect(correction_speaker).to_have_attribute("aria-pressed", "false")
        correction_speaker.click()
        expect(correction_speaker).to_have_attribute("aria-pressed", "true")
        self.assertEqual(speak.call_count, 1)  # The second play came from the page's audio cache.

        self.page.set_viewport_size({"width": 1440, "height": 900})
        self.page.locator("#text").fill("Native example: I didn't went to work yesterday.")
        self.page.get_by_role("button", name="Corectare", exact=True).click()
        native_speaker = self.page.get_by_role("button", name="Ascultă versiunea nativă în engleză britanică")
        expect(native_speaker).to_have_count(1)
        native_speaker.click()
        expect(native_speaker).to_have_attribute("aria-pressed", "true")
        new_correction_speaker = self.page.get_by_role("button", name="Ascultă corectura în engleză britanică")
        new_correction_speaker.click()
        expect(new_correction_speaker).to_have_attribute("aria-pressed", "true")
        expect(native_speaker).to_have_attribute("aria-pressed", "false")
        self.assertEqual(speak.call_count, 3)  # A new correction carries a new token, so it is fetched once.
        self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), 1440)
        self.page.screenshot(path=str(self.artifacts / "voice-controls-1440.png"), full_page=True)
        self.assertEqual(self.errors, [])
