"""Opt-in Chromium checks. Run separately with the documented test settings."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, unquote_plus
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.db import connections
from django.test import override_settings
from playwright.sync_api import sync_playwright, expect

from apps.analytics.models import AudioUsageEvent
from apps.assistant.tests.examples import correction_result, translation_result
from apps.assistant.services.openai_client import AssistantError
from apps.assistant.services.voice import AudioUsage
from apps.accounts.models import LegalAcceptance
from apps.analytics.services.visitors import VISITOR_COOKIE
from apps.core.consent import CONSENT_COOKIE, consent_cookie_value

# Browser stand-ins for the microphone and the WebRTC connection to OpenAI: tests play the provider's transcript events.
REALTIME_MOCKS = """(() => {
  const rt = (window.__rt = { gum: 0, tracksStopped: 0, sent: [], channel: null, pc: null, pcClosed: false });
  if (!navigator.mediaDevices) Object.defineProperty(navigator, "mediaDevices", { value: {} });
  navigator.mediaDevices.getUserMedia = async () => {
    rt.gum += 1;
    const track = { kind: "audio", enabled: true, stop() { rt.tracksStopped += 1; } };
    return { getTracks: () => [track], getAudioTracks: () => [track] };
  };
  class FakeChannel extends EventTarget {
    constructor() { super(); this.readyState = "connecting"; }
    send(data) { rt.sent.push(JSON.parse(data)); }
    close() { this.readyState = "closed"; }
  }
  class FakePeerConnection extends EventTarget {
    constructor() { super(); this.connectionState = "new"; rt.pc = this; }
    addTrack() {}
    createDataChannel() { rt.channel = new FakeChannel(); return rt.channel; }
    async createOffer() { return { type: "offer", sdp: "v=0 fake-offer" }; }
    async setLocalDescription() {}
    async setRemoteDescription() { if (!rt.holdOpen) setTimeout(rt.open, 50); }
    close() { this.connectionState = "closed"; rt.pcClosed = true; }
  }
  window.RTCPeerConnection = FakePeerConnection;
  rt.open = () => { rt.channel.readyState = "open"; rt.channel.dispatchEvent(new Event("open")); };
  rt.emit = (event) => rt.channel.dispatchEvent(new MessageEvent("message", { data: JSON.stringify(event) }));
  rt.drop = () => { rt.pc.connectionState = "failed"; rt.pc.dispatchEvent(new Event("connectionstatechange")); };
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
})();"""
DELTA, COMPLETED = "conversation.item.input_audio_transcription.delta", "conversation.item.input_audio_transcription.completed"
# Homepage examples: text before the mistake, the mistake, its correction, text after (as in static/js/example-prompts.js).
EXAMPLES = [("I'm running a bit late, but I should be ", "their", "there", " in ten minutes."),
            ("Do you fancy ", "grab", "grabbing", " a coffee after work?"),
            ("Could you give me a ", "hands", "hand", " with this?"),
            ("What ", "is", "are", " you up to this weekend?"),
            ("I'll give you a call when I ", "got", "get", " home.")]
TYPED = [before + wrong + after for before, wrong, _, after in EXAMPLES]
CORRECTED = [f"{before}{wrong} {right}{after}" for before, wrong, right, after in EXAMPLES]

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
        # Existing workflows start from a browser that already accepted the current Terms (analytics off); the consent
        # tests use fresh contexts without this cookie.
        self.context.add_cookies([self.consent_cookie()])
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
                # While the correction loads, both buttons keep their label and look.
                expect(self.page.locator(".result-loading")).to_be_visible()
                expect(self.page.get_by_role("button", name="Corectăm…", exact=True)).to_have_count(0)
                expect(self.page.get_by_role("button", name="Traducere", exact=True)).to_be_enabled()
                expect(self.page.get_by_role("button", name="Corectare", exact=True)).to_be_enabled()
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

    def test_desktop_landing_sections_and_footer(self):
        marketing, footer = self.page.locator(".desktop-marketing"), self.page.locator(".site-footer")
        for width in (1440, 1280, 1024):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": 900})
                self.page.goto(self.live_server_url)
                expect(self.page.locator("#hero-title")).to_have_text("Corectează-ți engleza. Vorbește natural.")
                expect(self.page.locator("#text")).to_be_visible()
                expect(self.page.get_by_role("button", name="Corectare", exact=True)).to_be_visible()
                expect(self.page.get_by_role("button", name="Traducere", exact=True)).to_be_visible()
                expect(marketing).to_be_visible()
                cards = self.page.locator(".feature-card")
                expect(cards).to_have_count(12)
                for index in range(12):
                    expect(cards.nth(index)).to_be_visible()
                expect(self.page.locator(".feature-card.is-pro .pro-ribbon")).to_have_count(5)
                for title in ("Versiune nativă", "Scrii sau dictezi", "Pronunție britanică", "Explicații în română"):
                    expect(self.page.get_by_role("heading", name=title, exact=True)).to_be_visible()
                # Promotion: the normal price struck through, the promotional monthly price prominent, Fair Use linked.
                pro = self.page.locator(".plan-pro")
                expect(pro.locator("s.plan-price-original")).to_have_text("Preț normal: £9.99")
                expect(pro.locator(".plan-price strong")).to_have_text("Preț actual: £4.99")
                prices = pro.evaluate("""card => ({strike: getComputedStyle(card.querySelector('s')).textDecorationLine,
                    old: parseFloat(getComputedStyle(card.querySelector('s')).fontSize),
                    now: parseFloat(getComputedStyle(card.querySelector('.plan-price strong')).fontSize)})""")
                self.assertIn("line-through", prices["strike"])
                self.assertGreater(prices["now"], prices["old"])
                expect(pro.locator(".plan-fair-use")).to_be_visible()
                expect(pro.get_by_role("link", name="Fair Use")).to_have_attribute("href", "/termeni/#fair-use")
                expect(self.page.get_by_text("Pentru administratori")).to_have_count(0)
                expect(self.page.get_by_role("heading", name="Cum funcționează")).to_be_visible()
                expect(self.page.locator(".step-card")).to_have_count(4)
                expect(self.page.get_by_role("heading", name="Free", exact=True)).to_be_visible()
                expect(self.page.get_by_role("heading", name="Pro", exact=True)).to_be_visible()
                expect(self.page.get_by_role("button", name="Alege Pro")).to_be_disabled()
                expect(self.page.locator(".plan-strip")).to_be_visible()
                cta = self.page.locator(".landing-cta")
                expect(cta).to_be_visible()
                expect(cta.locator("a, button")).to_have_count(1)
                expect(cta.get_by_role("link", name="Creează cont", exact=True)).to_have_attribute("href", "/accounts/signup/")
                expect(footer).to_be_visible()
                self.assertEqual(self.page.locator("h1").count(), 1)
                layout = self.page.evaluate("""() => ({scrollWidth: document.documentElement.scrollWidth,
                    free: document.querySelector('.plan-free').getBoundingClientRect().toJSON(),
                    pro: document.querySelector('.plan-pro').getBoundingClientRect().toJSON()})""")
                self.assertLessEqual(layout["scrollWidth"], width)
                self.assertEqual(round(layout["free"]["height"]), round(layout["pro"]["height"]))
                self.page.screenshot(path=str(self.artifacts / f"landing-{width}.png"), full_page=True)
                # The banner's skyline background stays fixed while the page scrolls.
                self.assertEqual(self.page.evaluate(
                    "getComputedStyle(document.querySelector('.landing-cta'), '::before').backgroundAttachment"), "fixed")
                self.page.evaluate("window.scrollTo({top: document.documentElement.scrollHeight, behavior: 'instant'})")
                self.page.screenshot(path=str(self.artifacts / f"landing-cta-{width}.png"))
                # "Beneficii" in the header scrolls to the plans, below the sticky header.
                self.page.get_by_role("navigation", name="Navigare principală").get_by_role("link", name="Beneficii").click()
                plans_title = self.page.get_by_role("heading", name="Alege planul potrivit")
                expect(plans_title).to_be_in_viewport()
                self.page.wait_for_function("""() => document.querySelector('#plans').getBoundingClientRect().top
                    >= document.querySelector('.site-header').getBoundingClientRect().bottom""")
        for width, height in ((390, 844), (375, 667), (360, 740), (360, 640), (768, 1024)):
            with self.subTest(width=width, height=height):
                self.page.set_viewport_size({"width": width, "height": height})
                self.page.goto(self.live_server_url)
                expect(self.page.locator("#text")).to_be_visible()
                # The editor and its buttons still fit on the first screen.
                self.assertLessEqual(self.page.locator(".action-buttons").bounding_box()["y"]
                                     + self.page.locator(".action-buttons").bounding_box()["height"], height)
                expect(marketing).to_be_hidden()
                for selector in (".feature-card", ".step-card", ".plan-card", ".plan-strip", ".landing-cta"):
                    expect(self.page.locator(selector).first).to_be_hidden()
                # Nothing written yet: the editor, the Corectură card and the footer fit on one screen, no scrolling.
                self.page.wait_for_function(f"document.documentElement.scrollHeight <= {height}")
                box = footer.bounding_box()
                self.assertLessEqual(box["y"] + box["height"], height)
                expect(footer).to_be_visible()
                expect(footer.get_by_role("link", name="Confidențialitate")).to_be_visible()
                self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), width)
                self.page.screenshot(path=str(self.artifacts / f"landing-{width}x{height}.png"), full_page=True)
        self.assertEqual(self.errors, [])

    def test_editor_link_focuses_text_box_and_pro_members_see_their_benefits(self):
        user = self.in_database_thread(lambda: User.objects.create_user(username="pro-learner",
                                                                        password="Browser-test-password-815"))
        # An existing account that already accepted the current Terms, so no consent dialog covers the page.
        self.in_database_thread(lambda: LegalAcceptance.objects.create(
            user=user, terms_version=settings.TERMS_VERSION, privacy_version=settings.PRIVACY_VERSION, source="visit"))
        self.page.goto(self.live_server_url + "/accounts/login/")
        self.page.locator("#id_username").fill("pro-learner")
        self.page.locator("#id_password").fill("Browser-test-password-815")
        self.page.locator("#id_password").press("Enter")
        expect(self.page.locator("#text")).to_be_visible()
        self.page.evaluate("window.scrollTo({top: document.documentElement.scrollHeight, behavior: 'instant'})")
        self.page.locator(".landing-cta").get_by_role("link", name="Începe o corectare").click()
        expect(self.page.locator("#text")).to_be_focused()
        self.page.wait_for_function("window.scrollY === 0")
        expect(self.page.locator("#hero-title")).to_be_in_viewport()
        expect(self.page.get_by_role("heading", name="Alege planul potrivit")).to_have_count(1)
        # The group is created by a migration, but TransactionTestCase flushes it between tests.
        self.in_database_thread(lambda: user.groups.add(Group.objects.get_or_create(name="Pro")[0]))
        self.page.reload()
        expect(self.page.get_by_role("heading", name="Ești în planul potrivit.")).to_be_visible()
        expect(self.page.get_by_role("heading", name="Alege planul potrivit")).to_have_count(0)
        expect(self.page.locator(".plan-card")).to_have_count(0)
        expect(self.page.locator(".pro-benefits li")).to_have_count(10)
        self.page.locator("#plans").screenshot(path=str(self.artifacts / "landing-pro-plans-1440.png"))
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
        self.page.locator("#id_accept_legal").check()
        self.page.get_by_role("button", name="Creează cont", exact=True).click()
        expect(self.page.locator("#text")).to_be_visible()
        self.page.locator("#text").fill(correction_result().original_text)
        self.page.get_by_role("button", name="Corectare", exact=True).click()
        expect(self.page.locator(".result-text")).to_be_visible()
        self.page.goto(self.live_server_url + "/history/")
        self.page.locator(".history-entry").click()
        expect(self.page.locator(".result-text")).to_have_text(correction_result().corrected_text)
        for path in ("mistakes", "progress", "practice", "confidentialitate", "termeni", "accounts/profile"):
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
        expect(self.page.locator(".mobile-count .count-hint")).to_have_text("Vorbește aici sau scrie")
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

    # ----- Example sentences in the empty text box -----
    def test_example_sentences_stay_out_of_the_text_box(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.goto(self.live_server_url)
        text, example = self.page.locator("#text"), self.page.locator(".example-prompt")
        expect(example).to_be_visible()
        samples = []
        for _ in range(10):
            samples.append((text.input_value(), example.text_content()))
            self.page.wait_for_timeout(120)
        self.assertEqual({value for value, _ in samples}, {""})  # Typed into the layer, never into the textarea.
        self.assertTrue(any(shown for _, shown in samples))
        self.assertTrue(all(TYPED[0].startswith(shown) for _, shown in samples))
        expect(self.page.locator(".mobile-count .count-hint")).to_have_text("Vorbește aici sau scrie")
        expect(self.page.locator(".mobile-count .count-hint")).to_be_visible()
        expect(self.page.locator(".mobile-count .count-value")).to_be_hidden()
        # Typed with the mistake, then the mistake is struck through in red and the correction typed beside it in green.
        self.page.wait_for_function("s => document.querySelector('.example-prompt').textContent === s", arg=TYPED[0])
        self.page.wait_for_function("s => document.querySelector('.example-prompt').textContent === s", arg=CORRECTED[0])
        marks = self.page.evaluate("""() => {
            const style = s => getComputedStyle(document.querySelector(s));
            return {wrong: document.querySelector('.example-wrong').textContent, right: document.querySelector('.example-right').textContent,
                    strike: style('.example-wrong').textDecorationLine, red: style('.example-wrong').color, green: style('.example-right').color};
        }""")
        self.assertEqual(marks, {"wrong": "their", "right": " there", "strike": "line-through",
                                 "red": "rgb(200, 32, 49)", "green": "rgb(15, 122, 66)"})
        self.assertEqual(text.input_value(), "")
        self.page.wait_for_function("document.querySelector('.example-prompt').textContent.startsWith('Do you fancy')")
        self.assertEqual(text.input_value(), "")
        for width, height in ((390, 844), (375, 667), (360, 740)):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": height})
                expect(example).to_be_visible()
                layout = self.page.evaluate("""() => {
                    const r = s => document.querySelector(s).getBoundingClientRect().toJSON();
                    return {example: r('.example-prompt'), mic: r('[data-voice-record]'), count: r('.mobile-count'),
                            box: r('#text'), scrollWidth: document.documentElement.scrollWidth};
                }""")
                self.assertLessEqual(layout["example"]["bottom"], layout["mic"]["top"])
                self.assertLessEqual(layout["example"]["bottom"], layout["count"]["top"])
                self.assertGreaterEqual(layout["example"]["left"], layout["box"]["left"])
                self.assertLessEqual(layout["example"]["right"], layout["box"]["right"])
                self.assertLessEqual(layout["scrollWidth"], width)
        self.page.set_viewport_size({"width": 390, "height": 844})
        text.click()
        expect(example).to_be_hidden()
        self.page.keyboard.type("Hi")
        expect(text).to_have_value("Hi")
        expect(example).to_be_hidden()
        text.fill("")
        expect(example).to_be_hidden()  # Still focused: no example while the learner is using the box.
        self.page.locator("#result").click()  # Focus leaves the text box.
        self.page.wait_for_timeout(500)
        expect(example).to_be_hidden()  # No instant flash after clearing.
        expect(example).to_be_visible(timeout=4000)
        self.assertEqual(text.input_value(), "")
        self.assertEqual(self.errors, [])

    def test_example_sentences_are_never_submitted(self):
        self.page.goto(self.live_server_url)
        example = self.page.locator(".example-prompt")
        expect(example).to_be_visible()
        self.page.wait_for_function("document.querySelector('.example-prompt').textContent.length > 10")
        for path, name in (("/assistant/correct/", "Corectare"), ("/assistant/translate/", "Traducere")):
            with self.subTest(button=name):
                with self.page.expect_request(lambda request, path=path: request.method == "POST" and request.url.endswith(path)) as sent:
                    self.page.get_by_role("button", name=name, exact=True).click()
                body = sent.value.post_data or ""
                self.assertEqual(parse_qs(body, keep_blank_values=True).get("text"), [""])
                for sentence in TYPED:
                    self.assertNotIn(sentence[:12], unquote_plus(body))
                expect(self.page.locator(".error-box")).to_contain_text("scrie ceva în casetă sau apasă microfonul")
        self.page.locator("#text").fill("I goed home.")
        with self.page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/assistant/correct/")) as sent:
            self.page.get_by_role("button", name="Corectare", exact=True).click()
        self.assertEqual(parse_qs(sent.value.post_data, keep_blank_values=True)["text"], ["I goed home."])
        expect(self.page.locator(".result-text")).to_have_text(correction_result().corrected_text)
        self.assertEqual(self.errors, [])

    def test_reduced_motion_shows_one_static_example(self):
        context = self.browser.new_context(reduced_motion="reduce", viewport={"width": 390, "height": 844})
        try:
            page = context.new_page()
            page.goto(self.live_server_url)
            example = page.locator(".example-prompt")
            page.wait_for_function("s => document.querySelector('.example-prompt').textContent === s", arg=CORRECTED[0])
            page.wait_for_timeout(3000)  # Still the same, already corrected sentence: nothing is typed or erased.
            self.assertEqual(example.text_content(), CORRECTED[0])
            expect(example.locator(".example-wrong")).to_have_text("their")
            expect(example.locator(".example-right")).to_have_text("there")
            expect(example).to_be_visible()
            self.assertEqual(page.locator("#text").input_value(), "")
        finally:
            context.close()

    # ----- Live transcription -----
    def start_live_mocks(self):
        patch("apps.assistant.voice_views.create_realtime_secret", return_value=("ek_browser_test", 1789336312)).start()
        self.file_transcribe = patch("apps.assistant.voice_views.transcribe", return_value=(
            "Recorded instead.", AudioUsage(model="gpt-transcribe", audio_seconds=Decimal("3.00")))).start()
        self.sdp_status, self.offers = 201, []

        def answer(route):
            cors = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "authorization, content-type",
                    "Access-Control-Allow-Methods": "POST"}
            if route.request.method == "OPTIONS":
                route.fulfill(status=204, headers=cors)
                return
            self.offers.append(route.request.headers.get("authorization"))
            route.fulfill(status=self.sdp_status, headers=cors, content_type="application/sdp", body="v=0 fake-answer")
        self.page.route("https://api.openai.com/v1/realtime/calls", answer)
        self.page.add_init_script(REALTIME_MOCKS)

    def emit(self, **event):
        self.page.evaluate("event => window.__rt.emit(event)", event)

    def wait_for_commit(self, count=1):
        self.page.wait_for_function(
            f"window.__rt.sent.filter(event => event.type === 'input_audio_buffer.commit').length >= {count}")

    def consent_cookie(self, analytics=False):
        return {"name": CONSENT_COOKIE, "value": consent_cookie_value(analytics), "url": self.live_server_url}

    def fresh_page(self, width, height):
        """A browser that has never visited Corect.uk: no consent cookie."""
        context = self.browser.new_context(viewport={"width": width, "height": height})  # Closed with the browser.
        page = context.new_page()
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        return context, page

    @staticmethod
    def cookie_values(context):
        return {cookie["name"]: cookie["value"] for cookie in context.cookies()}

    def test_notice_bar_and_cookie_settings_on_desktop(self):
        context, page = self.fresh_page(1440, 900)
        page.goto(self.live_server_url)
        bar = page.locator("[data-consent-bar]")
        expect(bar).to_be_visible()
        expect(bar.get_by_role("link", name="Termenii")).to_have_attribute("href", "/termeni/")
        expect(bar.get_by_role("link", name="Politica de confidențialitate")).to_have_attribute("href", "/confidentialitate/")
        expect(bar.locator("input[type=checkbox]")).to_have_count(0)
        expect(page.locator("#consent-dialog")).to_be_hidden()
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 1440)
        page.screenshot(path=str(self.artifacts / "consent-bar-1440.png"))

        def correct():
            page.locator("#text").fill(correction_result().original_text)
            page.get_by_role("button", name="Corectare", exact=True).click()
            expect(page.locator(".result-text")).to_have_text(correction_result().corrected_text)

        # Nothing is blocked: Correct works while the notice shows, and anonymous analytics is on by default.
        correct()
        self.assertIn(VISITOR_COOKIE, self.cookie_values(context))
        bar.get_by_role("button", name="Am înțeles").click()
        expect(page.locator("[data-consent-bar]")).to_have_count(0)
        expect(page.locator(".result-text")).to_have_text(correction_result().corrected_text)  # No reload.
        self.assertEqual(self.cookie_values(context)[CONSENT_COOKIE], consent_cookie_value(True))
        page.reload()
        expect(page.locator("[data-consent-bar]")).to_have_count(0)

        def save_cookie_settings(allow):
            page.locator(".site-footer").get_by_role("link", name="Setări cookie-uri").click()
            dialog = page.locator("#consent-dialog")
            expect(dialog).to_be_visible()
            expect(dialog.get_by_role("heading", name="Setări cookie-uri")).to_be_visible()
            dialog.locator("[name=allow_analytics]").set_checked(allow)
            with page.expect_navigation():
                dialog.get_by_role("button", name="Salvează").click()
            expect(dialog).to_be_hidden()

        save_cookie_settings(False)  # Switching off deletes the visitor cookie.
        cookies = self.cookie_values(context)
        self.assertNotIn(VISITOR_COOKIE, cookies)
        self.assertEqual(cookies[CONSENT_COOKIE], consent_cookie_value(False))
        correct()
        self.assertNotIn(VISITOR_COOKIE, self.cookie_values(context))
        save_cookie_settings(True)
        correct()
        self.assertIn(VISITOR_COOKIE, self.cookie_values(context))
        self.assertEqual(self.errors, [])

    def test_notice_bar_on_mobile(self):
        context, page = self.fresh_page(390, 844)
        page.goto(self.live_server_url)
        bar = page.locator("[data-consent-bar]")
        expect(bar).to_be_visible()
        box = bar.bounding_box()
        self.assertGreaterEqual(box["x"], 0)
        self.assertLessEqual(box["x"] + box["width"], 390)
        self.assertLessEqual(box["y"] + box["height"], 844)
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
        expect(page.locator(".desktop-marketing")).to_be_hidden()
        expect(page.get_by_role("button", name="Corectare", exact=True)).to_be_visible()
        page.screenshot(path=str(self.artifacts / "consent-bar-390.png"))
        bar.get_by_role("link", name="Termenii").click()
        page.wait_for_url("**/termeni/")
        expect(page.get_by_role("heading", name="Termeni de utilizare")).to_be_visible()
        expect(page.locator("main .consent-form")).to_have_count(0)
        page.locator("[data-consent-bar]").get_by_role("button", name="Am înțeles").click()
        expect(page.locator("[data-consent-bar]")).to_have_count(0)
        self.assertEqual(self.cookie_values(context)[CONSENT_COOKIE], consent_cookie_value(True))
        page.goto(self.live_server_url)
        expect(page.locator("[data-consent-bar]")).to_have_count(0)
        expect(page.locator("#text")).to_be_visible()
        page.locator(".site-footer").get_by_role("link", name="Setări cookie-uri").click()
        dialog = page.locator("#consent-dialog")
        expect(dialog).to_be_visible()
        box = dialog.bounding_box()
        self.assertGreaterEqual(box["x"], 0)
        self.assertLessEqual(box["x"] + box["width"], 390)
        page.screenshot(path=str(self.artifacts / "cookie-settings-390.png"))
        dialog.locator("[name=allow_analytics]").uncheck()
        with page.expect_navigation():
            dialog.get_by_role("button", name="Salvează").click()
        self.assertEqual(self.cookie_values(context)[CONSENT_COOKIE], consent_cookie_value(False))
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
        self.assertEqual(self.errors, [])

    @staticmethod
    def in_database_thread(query):
        """Runs an ORM query in a short-lived thread whose connection is closed at once. Playwright's sync API keeps an
        event loop in the test thread, where the ORM refuses to run and a leftover connection would block dropping
        the test database."""
        def run():
            try:
                return query()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(run).result()

    def ledger_row(self, timeout=5, **fields):
        def poll():
            deadline = time.time() + timeout
            while time.time() < deadline:
                event = AudioUsageEvent.objects.filter(operation="transcription", **fields).first()
                if event:
                    return event
                time.sleep(.05)
            return None
        event = self.in_database_thread(poll)
        if event is None:
            self.fail(f"No audio ledger row with {fields}")
        return event

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
        self.page.wait_for_function("window.__rt.channel !== null")
        self.page.evaluate("window.__rt.open()")
        stop = self.page.get_by_role("button", name="Oprește transcrierea live")
        expect(stop).to_have_attribute("aria-pressed", "true")
        expect(overlay).to_be_hidden()
        expect(stop.locator(".stop-icon")).to_be_visible()
        expect(stop.locator(".mic-icon")).to_be_hidden()
        expect(self.page.locator("#voice-status")).to_contain_text("Ascult… Vorbește normal. Textul apare pe măsură ce vorbești.")
        self.assertEqual(self.offers, ["Bearer ek_browser_test"])  # The browser only ever holds the short-lived secret.
        expect(text).not_to_be_editable()
        expect(self.page.get_by_role("button", name="Corectare", exact=True)).to_be_disabled()
        expect(self.page.get_by_role("button", name="Traducere", exact=True)).to_be_disabled()
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
        expect(self.page.locator("#voice-status")).to_contain_text("Gata. Poți modifica textul, apoi alege Corectare sau Traducere.")
        expect(text).to_have_value("I think we should meet tomorrow.")
        expect(text).to_be_editable()
        expect(self.page.get_by_role("button", name="Corectare", exact=True)).to_be_enabled()
        expect(self.page.get_by_role("button", name="Înregistrează-ți vocea")).to_have_attribute("aria-pressed", "false")
        self.assertGreaterEqual(self.page.evaluate("window.__rt.tracksStopped"), 1)
        self.assertTrue(self.page.evaluate("window.__rt.pcClosed"))
        event = self.ledger_row(stt_mode="realtime")
        self.assertEqual((event.status, event.model, event.metering_source, event.audio_seconds),
                         ("success", "gpt-live-transcribe", "provider", Decimal("1.00")))
        self.assertEqual(self.in_database_thread(AudioUsageEvent.objects.filter(operation="transcription").count), 1)
        self.file_transcribe.assert_not_called()  # One transcription service per recording: never both.

        text.fill(correction_result().original_text)
        self.page.get_by_role("button", name="Corectare", exact=True).click()
        expect(self.page.locator(".result-text")).to_have_text(correction_result().corrected_text)
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
        self.assertGreaterEqual(time.time() - started, 3.0)
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
