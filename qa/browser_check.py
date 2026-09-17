"""Opt-in Chromium checks. Run separately with the documented test settings."""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from urllib.parse import parse_qs, unquote_plus
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.db import connections
from django.test import override_settings
from django.utils.crypto import salted_hmac
from playwright.sync_api import sync_playwright, expect

from apps.analytics.models import AudioUsageEvent
from apps.assistant.models import NaturalizeUsage, RealtimeTranscriptionSession
from apps.assistant.services.localday import local_day
from apps.assistant.schemas import TranslationResult
from apps.assistant.services.naturalize import Naturalized
from apps.assistant.tests.examples import CORRECTION_CASES, correction_result
from apps.assistant.services.openai_client import AssistantError
from apps.assistant.services.voice import AudioUsage
from apps.accounts.models import LegalAcceptance
from apps.analytics.services.visitors import VISITOR_COOKIE
from apps.core.consent import CONSENT_COOKIE, consent_cookie_value
from apps.learning.models import ExerciseAttempt
from apps.learning.services.profile import record_correction_occurrences, refresh_patterns
from apps.learning.tests.helpers import batch, make_correction, make_exercise, patch_ai

ACTION = "Vreau să sune natural!"
ROMANIAN = "Nu cred că ajung la muncă înainte de nouă."
BRITISH = "I don't think I'll get to work before nine."
UNNATURAL = "I want to ask you if you can help me with a thing."

# Browser stand-ins for the microphone, the permission state and the WebRTC connection to OpenAI: tests play the
# provider's transcript events and read the moments the page muted the microphone and committed the audio.
REALTIME_MOCKS = """(() => {
  const rt = (window.__rt = { gum: 0, tracksStopped: 0, sent: [], channel: null, pc: null, pcClosed: false,
                              permission: "prompt", gumDelay: 0, gumError: null, sessionRequests: [] });
  if (!navigator.mediaDevices) Object.defineProperty(navigator, "mediaDevices", { value: {} });
  navigator.mediaDevices.getUserMedia = async () => {
    rt.gum += 1;
    if (rt.gumDelay) await new Promise((resolve) => setTimeout(resolve, rt.gumDelay));
    rt.gumAt = performance.now();
    if (rt.gumError) throw new DOMException("mock", rt.gumError);
    const track = { kind: "audio", live: true, stop() { rt.tracksStopped += 1; },
      get enabled() { return this.live; },
      set enabled(value) { if (!value && this.live) rt.mutedAt = performance.now(); this.live = value; } };
    return { getTracks: () => [track], getAudioTracks: () => [track] };
  };
  const permissions = navigator.permissions;
  Object.defineProperty(navigator, "permissions", { configurable: true, value: {
    query: async (descriptor) => descriptor && descriptor.name === "microphone"
      ? { state: rt.permission } : permissions.query(descriptor) } });
  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const url = typeof input === "string" ? input : input.url;
    if (url.includes("realtime-transcription/session")) rt.sessionRequests.push(performance.now());
    return nativeFetch(input, init);
  };
  class FakeChannel extends EventTarget {
    constructor() { super(); this.readyState = "connecting"; }
    send(data) {
      const event = JSON.parse(data);
      if (event.type === "input_audio_buffer.commit") rt.commitAt = performance.now();
      rt.sent.push(event);
    }
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
# Homepage examples: text before the literal phrase, the literal phrase, its natural British English, text after
# (as in static/js/example-prompts.js).
EXAMPLES = [("", "I hurt my head","I've got a headache", ", so I'm staying in tonight."),
            ("Sorry, ", "I have delayed with ten minutes", "I'm ten minutes late", "."),
            ("Can you ", "make us a photo", "take a photo of us", "?"),
            ("", "It depends of you what we make", "It's up to you what we do", " this weekend."),
            ("", "I finally took the driving exam", "I finally passed my driving test", "!")]
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


@override_settings(NATURALIZE_RATE_LIMIT_MINUTE=100, NATURALIZE_DAILY_LIMITS={"anonymous": 1000, "free": 1000, "pro": 1000})
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
        self.naturalize = patch("apps.assistant.views.NaturalizeService.naturalize",
                                side_effect=self.mock_naturalize).start()

    def tearDown(self):
        self.context.close()
        self.browser.close()
        self.runtime.stop()
        patch.stopall()
        super().tearDown()

    @staticmethod
    def mock_naturalize(text, polite=False):
        """English with errors by default; Romanian, natural and correct-but-unnatural English by their first words."""
        time.sleep(.25)  # Make loading-state assertions deterministic.
        if text == "Simulate failure":
            raise AssistantError("timeout", "A durat prea mult. Încearcă din nou.")
        if text.startswith("Nu cred"):
            return Naturalized("translation", "ro", TranslationResult(source_language="ro", target_language="en",
                                                                      original_text=text, translated_text=BRITISH))
        if text.startswith(("I want to ask", "I've lived")):
            result = correction_result(CORRECTION_CASES[4])
            result.original_text = result.corrected_text = text
            if text.startswith("I want to ask"):
                result.native_text, result.native_explanation = "Could you help me with something?", "Sună mai direct."
            return Naturalized("correction", "en", result)
        result = correction_result()
        result.original_text = text  # As the real service does: the result carries the text that was sent.
        if text.startswith("Native example"):
            result.native_text = "I didn't make it to work yesterday."
        if text.startswith("Long example"):
            result.corrected_text = "I've been learning English for five years. " * 25
            result.corrections[0].explanation_ro = "După did/didn't folosim forma de bază a verbului. " * 10
        return Naturalized("correction", "en", result)

    def submit(self, page=None):
        (page or self.page).get_by_role("button", name=ACTION, exact=True).click()

    def assert_no_overflow(self, page, width):
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
        clipped = page.evaluate("[...document.querySelectorAll('.button')].filter(b => b.offsetParent && "
                                "b.scrollWidth > b.clientWidth + 1).map(b => b.textContent.trim())")
        self.assertEqual(clipped, [])

    def test_responsive_workflow(self):
        measurements = []
        for width in (360, 375, 390, 430, 768, 1024, 1440):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": 900})
                self.page.goto(self.live_server_url)
                expect(self.page.locator(".empty-result")).to_contain_text("greșelile reparate și explicate")
                expect(self.page.locator("#assistant-form button[type=submit]")).to_have_count(1)
                self.assert_no_overflow(self.page, width)
                self.page.screenshot(path=str(self.artifacts / f"home-{width}.png"), full_page=True)
                self.page.locator("#text").fill(correction_result().original_text)
                self.assertEqual(self.naturalize.call_count, len(measurements))
                self.submit()
                # While the request loads, the button keeps its label and look.
                expect(self.page.locator(".result-loading")).to_be_visible()
                expect(self.page.get_by_role("button", name=ACTION, exact=True)).to_be_enabled()
                expect(self.page.locator(".result-text")).to_have_text(correction_result().corrected_text)
                expect(self.page.get_by_role("button", name=ACTION, exact=True)).to_be_enabled()
                heading = self.page.locator(".corrected-heading h2")
                expect(heading).to_have_text("Engleza ta, corectată")
                self.assertLess(heading.bounding_box()["height"], 40)  # One line, with no badge beside it.
                expect(self.page.locator(".improvement-badge")).to_have_count(0)
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

    def test_one_action_for_english_and_romanian_and_every_result_state(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.goto(self.live_server_url)
        body = self.page.evaluate("document.body.innerText")
        for stale in ("Corectare", "Traducere", "Corectură", "alege"):
            self.assertNotIn(stale, body)
        text, result = self.page.locator("#text"), self.page.locator("#result")
        cases = [
            (correction_result().original_text, "Engleza ta, corectată", "Ascultă varianta corectă în engleză britanică"),
            (UNNATURAL, "Sună mai natural:", "Ascultă varianta naturală în engleză britanică"),
            (CORRECTION_CASES[4][0], "✓ Sună deja natural.", "Ascultă varianta corectă în engleză britanică"),
            (ROMANIAN, "În engleză britanică", "Ascultă în engleză britanică"),
        ]
        for value, heading, speaker in cases:
            with self.subTest(heading=heading):
                text.fill(value)
                self.submit()
                expect(result.get_by_role("heading", level=2).first).to_have_text(heading)
                expect(result.get_by_role("button", name=speaker)).to_have_count(1)
                self.assert_no_overflow(self.page, 390)
                self.page.screenshot(path=str(self.artifacts / f"result-{heading[:12].strip('✓ :').lower()}-390.png"),
                                     full_page=True)
        expect(result).to_contain_text(BRITISH)
        expect(result.locator(".correction-comparison")).to_have_count(0)
        self.assertEqual([call.args[0] for call in self.naturalize.call_args_list], [case[0] for case in cases])
        self.assertEqual(self.errors, [])

    def test_about_page_from_the_mobile_header_and_menu(self):
        # Phones and tablets: a touch screen, where the "?" shows. One context per size, as a real device.
        for width, height in ((390, 844), (360, 740), (768, 1024)):
            with self.subTest(width=width):
                phone = self.browser.new_context(has_touch=True, is_mobile=True, viewport={"width": width, "height": height})
                phone.add_cookies([self.consent_cookie()])
                try:
                    page = phone.new_page()
                    page.on("pageerror", lambda error: self.errors.append(str(error)))
                    self.check_about_page_on_touch_screen(page, width)
                finally:
                    phone.close()
        # Every width below the desktop menu shows the "?" (also a narrow window with a mouse); desktop never does.
        for width in (1440, 900):
            with self.subTest(width=width, device="desktop"):
                self.page.set_viewport_size({"width": width, "height": 900})
                self.page.goto(self.live_server_url + "/about/")
                expect(self.page.get_by_role("heading", name="Cum funcționează")).to_be_visible()
                if width < 1024:
                    expect(self.page.locator(".help-link")).to_be_visible()
                    expect(self.page.locator(".nav-menu")).to_be_visible()
                else:
                    expect(self.page.locator(".help-link")).to_be_hidden()
                    expect(self.page.locator(".nav-menu")).to_be_hidden()
                self.page.screenshot(path=str(self.artifacts / f"about-desktop-{width}.png"))
        self.assertEqual(self.errors, [])

    def check_about_page_on_touch_screen(self, page, width):
        """The "?" sits left of the user icon and opens "Despre"; "Despre" in the hamburger menu opens it too."""
        page.goto(self.live_server_url)
        help_link = page.get_by_role("link", name="Despre Corect.uk")
        expect(help_link).to_be_visible()
        header = page.evaluate("""() => {
            const r = s => document.querySelector(s).getBoundingClientRect().toJSON();
            return {help: r('.help-link'), user: r('.user-link:not(.help-link)'), menu: r('.nav-menu summary'),
                    scrollWidth: document.documentElement.scrollWidth};
        }""")
        self.assertLessEqual(header["help"]["right"], header["user"]["left"])  # Left of the user icon.
        self.assertAlmostEqual(header["help"]["top"], header["user"]["top"], delta=1)
        self.assertLessEqual(header["user"]["right"], header["menu"]["left"])
        self.assertLessEqual(header["scrollWidth"], width)
        help_link.click()
        expect(page).to_have_url(self.live_server_url + "/about/")
        expect(page.get_by_role("heading", name="De ce să alegi Corect.uk")).to_be_visible()
        expect(page.locator(".feature-card")).to_have_count(12)
        expect(page.locator(".landing-cta")).to_be_visible()
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
        page.screenshot(path=str(self.artifacts / f"about-{width}.png"), full_page=True)
        page.goto(self.live_server_url + "/termeni/")
        page.locator(".nav-menu summary").click()
        menu = page.get_by_role("navigation", name="Toate paginile")
        links = menu.get_by_role("link").all_inner_texts()
        self.assertEqual([link.strip() for link in links[:5]], ["Acasă", "Panou", "Progres", "Greșeli", "Istoric"])
        menu.get_by_role("link", name="Despre", exact=True).click()
        expect(page).to_have_url(self.live_server_url + "/about/")

    def test_desktop_landing_sections_and_footer(self):
        marketing, footer = self.page.locator(".landing-wide-only"), self.page.locator(".site-footer")
        for width in (1440, 1280, 1024):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": 900})
                self.page.goto(self.live_server_url)
                expect(self.page.locator("#hero-title")).to_have_text("Vorbește natural limba engleză")
                self.assertLess(self.page.locator("#hero-title").bounding_box()["height"], 70)  # One line.
                expect(self.page.locator("#text")).to_be_visible()
                expect(self.page.get_by_role("button", name=ACTION, exact=True)).to_be_visible()
                desktop_links = self.page.get_by_role("navigation", name="Navigare principală").get_by_role("link")
                self.assertEqual([link.strip() for link in desktop_links.all_inner_texts()[:4]],
                                 ["Panou", "Progres", "Greșeli", "Istoric"])
                expect(self.page.locator(".site-header").get_by_role("link", name="Începe acum")).to_have_attribute(
                    "href", "/accounts/signup/")
                # Hero: the picture, the three value points on one row, the editor beside the benefit panel.
                expect(self.page.locator(".hero-art img")).to_be_visible()
                points = self.page.locator(".hero-points.is-wide li")
                expect(points).to_have_count(3)
                self.assertEqual(len({round(points.nth(index).bounding_box()["y"]) for index in range(3)}), 1)
                expect(self.page.locator(".hero-points.is-compact")).to_be_hidden()
                expect(self.page.locator(".features-teaser")).to_be_hidden()
                editor, panel = self.page.locator(".editor-panel").bounding_box(), self.page.locator("#result").bounding_box()
                self.assertGreater(panel["x"], editor["x"] + editor["width"])
                self.assertLess(abs(panel["y"] - editor["y"]), 2)
                self.assertLess(abs(panel["height"] - editor["height"]), 2)  # The two boxes are the same height.
                expect(self.page.locator(".empty-benefits li")).to_have_count(4)
                expect(marketing.first).to_be_visible()
                cards = self.page.locator(".feature-card")
                expect(cards).to_have_count(12)
                for index in range(12):
                    expect(cards.nth(index)).to_be_visible()
                expect(self.page.locator(".feature-card.is-pro .pro-ribbon")).to_have_count(5)
                for title in ("Sună natural", "Scrii sau dictezi", "Pronunție britanică", "Explicații în română"):
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
        for width, height in ((430, 932), (390, 844), (375, 667), (360, 740), (320, 640), (768, 1024)):
            with self.subTest(width=width, height=height):
                self.page.set_viewport_size({"width": width, "height": height})
                self.page.goto(self.live_server_url)
                # Phones and tablets open straight on the editor: no picture, title text or value points on screen,
                # while the page keeps its one heading for screen readers.
                for selector in (".hero-art", ".hero-lead", ".hero-points.is-wide", ".hero-points.is-compact", ".hero-hand"):
                    expect(self.page.locator(selector)).to_be_hidden()
                self.assertEqual(self.page.locator("h1").count(), 1)
                self.assertLessEqual(self.page.locator(".hero").bounding_box()["width"], 1)  # Clipped to 1px.
                expect(self.page.locator("#hero-title")).to_have_text("Vorbește natural limba engleză")
                header = self.page.locator(".site-header").bounding_box()
                editor = self.page.locator(".editor-panel").bounding_box()
                self.assertLess(editor["y"], header["y"] + header["height"] + 24)
                self.assertLess(self.page.locator(".naturalize-button").bounding_box()["height"], 70)  # One line.
                expect(marketing.first).to_be_hidden()
                for selector in (".feature-card", ".step-card", ".plan-card", ".plan-strip", ".landing-cta"):
                    expect(self.page.locator(selector).first).to_be_hidden()
                # The benefits as compact 2 × 2 cards, then the one-line way into everything, then only the footer links.
                cards = [self.page.locator(".empty-benefits li").nth(index).bounding_box() for index in range(4)]
                self.assertLess(abs(cards[0]["y"] - cards[1]["y"]), 2)
                self.assertGreater(cards[2]["y"], cards[0]["y"] + cards[0]["height"] - 1)
                self.assertGreater(cards[0]["y"], editor["y"] + editor["height"])
                teaser = self.page.locator(".features-teaser")
                expect(teaser).to_be_visible()
                expect(teaser).to_have_attribute("href", "/about/")
                self.assertGreater(teaser.bounding_box()["y"], cards[3]["y"] + cards[3]["height"] - 1)
                self.assertLess(teaser.locator(".teaser-title").bounding_box()["height"], 30)  # One line.
                expect(footer.locator(".footer-logo")).to_be_hidden()
                expect(footer.get_by_role("link", name="Confidențialitate")).to_be_visible()
                # Everything on one screen: nothing to scroll, the footer links on the first screen.
                self.assertLessEqual(self.page.evaluate("document.documentElement.scrollHeight"), height)
                links = footer.locator(".footer-links").bounding_box()
                self.assertLessEqual(links["y"] + links["height"], height)
                if width >= 360:  # The footer links on one row.
                    tops = {round(link.bounding_box()["y"]) for link in footer.locator(".footer-links a").all()}
                    self.assertEqual(len(tops), 1)
                self.assert_no_overflow(self.page, width)
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
        self.page.locator(".landing-cta").get_by_role("link", name="Scrie primul text").click()
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
        expect(self.page.locator(".pro-benefits li")).to_have_count(9)
        self.page.locator("#plans").screenshot(path=str(self.artifacts / "landing-pro-plans-1440.png"))
        self.assertEqual(self.errors, [])

    def test_submitting_scrolls_to_loading_and_keeps_result_in_view(self):
        self.page.emulate_media(reduced_motion="reduce")
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.goto(self.live_server_url)
        self.page.locator("#text").fill(correction_result().original_text)
        self.submit()
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

    def test_romanian_error_recovery_menu_and_long_content(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.goto(self.live_server_url)
        self.page.locator("#text").fill(ROMANIAN)
        self.submit()
        expect(self.page.locator(".result-text")).to_have_text(BRITISH)
        self.page.locator("#text").fill("Simulate failure")
        self.submit()
        expect(self.page.get_by_role("alert")).to_have_text("A durat prea mult. Încearcă din nou.")
        expect(self.page.locator("#text")).to_have_value("Simulate failure")
        expect(self.page.get_by_role("button", name=ACTION, exact=True)).to_be_enabled()
        self.page.locator("#text").fill("Long example")
        self.submit()
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
        self.submit()
        expect(self.page.locator(".result-text")).to_be_visible()
        self.page.locator("#text").fill(ROMANIAN)
        self.submit()
        expect(self.page.locator(".result-text")).to_have_text(BRITISH)
        self.page.goto(self.live_server_url + "/history/")
        expect(self.page.locator(".history-day-count").first).to_contain_text("1 text în engleză")
        expect(self.page.locator(".history-day-count").first).to_contain_text("1 text din română")
        self.page.locator(".history-entry").filter(has_text="Română → engleză").click()
        expect(self.page.get_by_role("heading", level=1)).to_have_text("Română → engleză")
        expect(self.page.locator(".result-text")).to_have_text(BRITISH)
        self.page.goto(self.live_server_url + "/history/")
        self.page.locator(".history-entry").filter(has_not_text="Română").first.click()
        expect(self.page.locator(".result-text").first).to_have_text(correction_result().corrected_text)
        self.page.goto(self.live_server_url + "/mistakes/")
        # Greșeli: the status sits beside the pattern name, the details stay on one line, and the arrow on the
        # right is the only way into the exercises (no long button).
        listing = self.page.locator(".learn-list.is-listing")
        expect(listing.locator(".learn-row-title .learn-status")).to_have_count(1)
        expect(listing.locator(".button")).to_have_count(0)
        expect(self.page.get_by_role("button", name="Exersează", exact=True)).to_have_count(0)
        for width in (1440, 390, 360):
            self.page.set_viewport_size({"width": width, "height": 844})
            row = self.page.evaluate("""() => { const r = s => document.querySelector(s).getBoundingClientRect().toJSON();
                const meta = document.querySelector('.learn-row-meta');
                return {title: r('.learn-row-title strong'), status: r('.learn-row-title .learn-status'), meta: r('.learn-row-meta'),
                        lineHeight: parseFloat(getComputedStyle(meta).lineHeight) || 20, arrow: r('.learn-row-arrow'),
                        progress: r('.learn-row-progress'), clipped: meta.scrollWidth > meta.clientWidth + 1,
                        row: r('.learn-row'), scrollWidth: document.documentElement.scrollWidth}; }""")
            self.assertLess(abs(row["title"]["top"] + row["title"]["height"] / 2 - row["status"]["top"] - row["status"]["height"] / 2), 8)
            self.assertLess(row["meta"]["height"], row["lineHeight"] * 1.5)
            self.assertFalse(row["clipped"])  # "Categorie · N apariții · recapitulare 15 Sep" fits on its one line.
            self.assertLess(row["title"]["height"], row["lineHeight"] * 2.6)  # The name is not squeezed into a narrow column.
            self.assertGreaterEqual(row["arrow"]["left"], row["progress"]["right"])
            self.assertGreater(row["arrow"]["right"], row["row"]["right"] - 50)  # On the right edge of the card.
            self.assertGreaterEqual(row["arrow"]["width"], 44)
            self.assertLessEqual(row["scrollWidth"], width)
            self.page.screenshot(path=str(self.artifacts / f"mistakes-listing-{width}.png"), full_page=True)
        self.page.set_viewport_size({"width": 1440, "height": 1000})
        arrow_form = listing.locator(".learn-row-go").first  # Starts the exercises (covered without AI in the learning tests).
        expect(arrow_form).to_have_attribute("action", "/learn/practice/start/")
        expect(arrow_form.locator("input[name=kind]")).to_have_value("pattern")
        self.page.locator(".category-row").first.click()
        expect(self.page.locator(".mistake-item")).to_have_count(1)
        expect(self.page.get_by_role("link", name="Exersează", exact=True)).to_have_count(0)
        for path in ("mistakes", "progress", "practice", "confidentialitate", "termeni", "accounts/profile"):
            self.page.goto(f"{self.live_server_url}/{path}/")
            self.assertEqual(self.page.locator("h1").count(), 1)
            self.page.screenshot(path=str(self.artifacts / (path.replace("/", "-") + ".png")), full_page=True)
        # Profile: two columns on desktop (details and deletion left, password right), the original order on phones.
        profile_forms = "() => [...document.querySelectorAll('.profile-page > form')].map(f => f.getBoundingClientRect().toJSON())"
        self.page.goto(self.live_server_url + "/accounts/profile/")
        details, password, delete = self.page.evaluate(profile_forms)
        self.assertEqual(round(details["x"]), round(delete["x"]))
        self.assertGreaterEqual(password["x"], details["x"] + details["width"])
        self.assertLess(abs(password["y"] - details["y"]), 2)
        self.assertGreater(delete["y"], details["y"] + details["height"])
        content = self.page.locator("main").bounding_box()["width"]  # 1090px: every page has the homepage's width.
        self.assertGreaterEqual(password["x"] + password["width"] - details["x"], content - 1)  # The page width, not a narrow column.
        self.page.screenshot(path=str(self.artifacts / "accounts-profile-1440.png"), full_page=True)
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.reload()
        details, password, delete = self.page.evaluate(profile_forms)
        self.assertLess(details["y"], password["y"])
        self.assertLess(password["y"], delete["y"])
        self.assertEqual(round(details["x"]), round(password["x"]))
        self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), 390)
        self.page.screenshot(path=str(self.artifacts / "accounts-profile-390.png"), full_page=True)
        self.page.set_viewport_size({"width": 1440, "height": 1000})
        self.page.goto(self.live_server_url + "/practice/")
        expect(self.page.get_by_text("Întrebare rapidă")).to_have_count(0)
        self.assertEqual(self.errors, [])
        no_js = self.browser.new_context(java_script_enabled=False, viewport={"width": 390, "height": 844})
        try:
            page = no_js.new_page()
            page.goto(self.live_server_url)
            page.locator("#text").fill(correction_result().original_text)
            self.submit(page)
            expect(page.locator(".result-text")).to_have_text(correction_result().corrected_text)
            page.locator("#text").fill(ROMANIAN)
            self.submit(page)
            expect(page.locator(".result-text")).to_have_text(BRITISH)
        finally:
            no_js.close()

    # ----- Learning dashboard and personalised practice -----
    def sign_in_learner(self, username, mistakes=0, exercises=0):
        """A learner who accepted the current Terms, with repeated since/for mistakes and stored exercises for them."""
        def seed():
            user = User.objects.create_user(username=username, password="Browser-test-password-815")
            LegalAcceptance.objects.create(user=user, terms_version=settings.TERMS_VERSION,
                                           privacy_version=settings.PRIVACY_VERSION, source="visit")
            for days_ago in range(mistakes):
                record_correction_occurrences(user, make_correction(user, days_ago=days_ago + 1))
            make_exercise(user, count=exercises)
        self.in_database_thread(seed)
        self.page.goto(self.live_server_url + "/accounts/login/")
        self.page.locator("#id_username").fill(username)
        self.page.locator("#id_password").fill("Browser-test-password-815")
        self.page.locator("#id_password").press("Enter")
        expect(self.page.locator("#text")).to_be_visible()

    def test_learning_dashboard_layout_and_personalised_session(self):
        ai = ExitStack()
        self.addCleanup(ai.close)
        model = ai.enter_context(patch_ai(batch()))
        self.sign_in_learner("dashboard-learner", mistakes=3, exercises=5)
        for width in (1440, 1280, 1024):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": 900})
                self.page.goto(self.live_server_url + "/learn/")
                expect(self.page.get_by_role("heading", level=1)).to_have_text("Bun venit, dashboard-learner")
                expect(self.page.get_by_role("navigation", name="Învățare")).to_be_visible()
                expect(self.page.locator(".learn-hero")).to_contain_text("5 exerciții alese din greșelile tale recente")
                expect(self.page.locator(".learn-stat")).to_have_count(4)
                expect(self.page.get_by_role("heading", name="Ce trebuie exersat")).to_be_visible()
                expect(self.page.locator(".learn-card").filter(has_text="Ce trebuie exersat")).to_contain_text("Since / for")
                expect(self.page.get_by_role("link", name="Vezi toate tipologiile de exersat")).to_have_attribute("href", "/mistakes/")
                expect(self.page.get_by_role("link", name="Exersează")).to_have_count(0)
                expect(self.page.locator(".learn-hero").get_by_role("button", name="Începe cele 5 exerciții")).to_be_visible()
                expect(self.page.locator(".learn-welcome .learn-plan-pill")).to_have_text("Free")
                expect(self.page.locator(".learn-welcome")).to_contain_text("1 tipologie urmărită")
                expect(self.page.locator(".learn-sidebar-editor")).to_contain_text("Adaugă un text nou")
                expect(self.page.locator(".learn-rail")).to_have_count(0)
                expect(self.page.get_by_role("heading", name="Următoarele")).to_have_count(0)
                activity = self.page.locator(".learn-hero .learn-hero-activity")
                expect(activity.get_by_role("heading", name="Progres", exact=True)).to_be_visible()
                layout = self.page.evaluate("""() => {
                    const r = s => document.querySelector(s).getBoundingClientRect().toJSON();
                    return {sidebar: r('.learn-sidebar'), main: r('.learn-main'), shell: r('.learn-shell'),
                            hero: r('.learn-hero'), copy: r('.learn-hero-copy'), activity: r('.learn-hero-activity'),
                            stats: r('.learn-stats'), scrollWidth: document.documentElement.scrollWidth};
                }""")
                self.assertLessEqual(layout["scrollWidth"], width)
                self.assertGreater(layout["main"]["x"], layout["sidebar"]["x"])
                self.assertGreater(layout["stats"]["y"], layout["hero"]["y"])
                # Without the rail the main column reaches the shell's right padding, and progress sits beside the text.
                self.assertGreaterEqual(layout["main"]["x"] + layout["main"]["width"], layout["shell"]["x"] + layout["shell"]["width"] - 30)
                self.assertGreater(layout["activity"]["x"], layout["copy"]["x"] + layout["copy"]["width"] - 1)
                self.page.screenshot(path=str(self.artifacts / f"learn-{width}.png"), full_page=True)
        for width, height in ((390, 844), (375, 667), (360, 740)):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": height})
                self.page.goto(self.live_server_url + "/learn/")
                expect(self.page.locator(".learn-sidebar")).to_be_hidden()
                expect(self.page.locator(".learn-hero")).to_be_in_viewport()
                layout = self.page.evaluate("""() => {
                    const r = s => document.querySelector(s).getBoundingClientRect().toJSON();
                    return {hero: r('.learn-hero'), stats: r('.learn-stats'), copy: r('.learn-hero-copy'),
                            activity: r('.learn-hero-activity'), scrollWidth: document.documentElement.scrollWidth};
                }""")
                self.assertLessEqual(layout["scrollWidth"], width)
                self.assertGreater(layout["stats"]["y"], layout["hero"]["y"])  # "Pentru tine azi" comes first.
                self.assertGreaterEqual(layout["activity"]["y"], layout["copy"]["y"] + layout["copy"]["height"] - 1)  # Stacked.
                self.assertEqual(self.page.locator("h1").count(), 1)
                self.page.screenshot(path=str(self.artifacts / f"learn-{width}.png"), full_page=True)

        # On a phone each pattern takes two lines: name beside its status, progress beside "Exersează".
        for width in (390, 360):
            with self.subTest(width=width, page="practice"):
                self.page.set_viewport_size({"width": width, "height": 844})
                self.page.goto(self.live_server_url + "/practice/")
                row = self.page.evaluate("""() => {
                    const li = document.querySelector('.learn-row'), r = s => li.querySelector(s).getBoundingClientRect().toJSON();
                    return {main: r('.learn-row-main'), status: r('.learn-status'), progress: r('.learn-row-progress'),
                            form: r('form'), scrollWidth: document.documentElement.scrollWidth};
                }""")
                self.assertLessEqual(row["scrollWidth"], width)
                self.assertLess(row["status"]["top"], row["main"]["bottom"])
                self.assertGreaterEqual(row["status"]["left"], row["main"]["right"])
                self.assertLess(row["form"]["top"], row["progress"]["bottom"])
                self.assertGreaterEqual(row["form"]["left"], row["progress"]["right"])
                self.page.screenshot(path=str(self.artifacts / f"practice-{width}.png"), full_page=True)

        # Five stored exercises for a pattern, answered on a phone, graded on the server without AI.
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.goto(self.live_server_url + "/practice/")
        self.page.get_by_role("button", name="Exersează: Since / for").click()
        for number in range(1, 6):
            expect(self.page.get_by_role("heading", level=1)).to_have_text(f"Exercițiul {number} din 5")
            if number == 1:
                self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), 390)
                self.page.screenshot(path=str(self.artifacts / "learn-session-390.png"), full_page=True)
            self.page.get_by_label("since", exact=True).check()
            self.page.get_by_role("button", name="Verifică").click()
            expect(self.page.get_by_role("status")).to_contain_text("Corect!")
            self.page.get_by_role("button", name="Termină" if number == 5 else "Continuă →").click()
        expect(self.page.get_by_role("heading", name="Gata, ai terminat!")).to_be_visible()
        expect(self.page.locator(".learn-session-done")).to_contain_text("Ai răspuns corect la 5 din 5 exerciții.")
        model.assert_not_called()
        self.assertEqual(self.errors, [])

    def test_repeated_correction_leads_to_a_practice_session(self):
        ai = ExitStack()
        self.addCleanup(ai.close)
        model = ai.enter_context(patch_ai(batch("base_form_after_did")))
        self.sign_in_learner("loop-learner")
        self.page.set_viewport_size({"width": 390, "height": 844})
        # The same mistake in two different texts: sending one text again would be answered from the learner's own
        # saved result, which is deliberately not counted as making the mistake a second time.
        for day in ("Monday", "Tuesday"):
            self.page.goto(self.live_server_url)
            self.page.locator("#text").fill(f"I didn't went to work on {day}.")
            self.submit()
            expect(self.page.locator(".result-text")).to_have_text(correction_result().corrected_text)
        hint = self.page.locator(".learning-hint")
        expect(hint).to_contain_text("Ai mai făcut această greșeală o dată.")
        self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), 390)
        hint.scroll_into_view_if_needed()
        self.page.screenshot(path=str(self.artifacts / "correction-practice-hint-390.png"))
        hint.get_by_role("button", name="Exersează acum").click()
        expect(self.page.get_by_role("heading", level=1)).to_have_text("Exercițiul 1 din 5")
        model.assert_called_once()  # Nothing was stored for this pattern yet: one batch, reused afterwards.
        self.assertEqual(self.errors, [])

    def test_what_changed_cards_on_progress(self):
        self.sign_in_learner("changes-learner", mistakes=3)

        def seed():
            user = User.objects.get(username="changes-learner")
            for days_ago in (6, 4, 2):
                record_correction_occurrences(user, make_correction(
                    user, category="word_order", original="I like very much tea", replacement="I like tea very much",
                    pattern="adverb_position", days_ago=days_ago))
            for days_ago in (10, 8):  # Recent enough that 4 of 5 correct is "almost solved", not yet solved.
                record_correction_occurrences(user, make_correction(
                    user, category="spelling", original="definately", replacement="definitely", pattern="spelling_other",
                    days_ago=days_ago))
            for exercise, correct in zip(make_exercise(user, pattern_key="spelling_other", count=5), (True, True, True, True, False)):
                ExerciseAttempt.objects.create(user=user, exercise=exercise, pattern_key="spelling_other", answer="0",
                                               is_correct=correct, graded_by="deterministic")
            refresh_patterns(user)
        self.in_database_thread(seed)
        section = self.page.locator(".learn-changes")
        measure = """() => {
            const r = el => el.getBoundingClientRect().toJSON(), cards = [...document.querySelectorAll('.learn-change')];
            return {grid: r(document.querySelector('.learn-changes-grid')), scrollWidth: document.documentElement.scrollWidth,
                    cards: cards.map(card => ({box: r(card), copy: r(card.querySelector('.learn-change-copy')),
                        chart: r(card.querySelector('.learn-trend, .learn-change-score')), wide: card.classList.contains('is-wide')}))};
        }"""
        for width, height in ((1440, 1000), (768, 1024), (390, 844)):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": height})
                self.page.goto(self.live_server_url + "/progress/")
                expect(section.get_by_role("heading", name="Ce s-a schimbat")).to_be_visible()
                expect(section.get_by_text("O privire rapidă asupra progresului tău din ultima perioadă.")).to_be_visible()
                expect(section.locator(".learn-change.is-persistent")).to_have_count(2)
                almost = section.locator(".learn-change.is-almost")
                expect(almost.locator(".learn-change-badge")).to_have_text("Aproape rezolvat")
                expect(almost.locator(".learn-ring")).to_have_text("80%")
                expect(almost.locator(".learn-dot.is-on")).to_have_count(4)
                expect(almost).to_contain_text("4 din 5 corecte")
                expect(section.locator(".learn-change.is-persistent .learn-trend")).to_have_count(2)
                # The end dot sits at the right end of its line, and the ring fills 80 % (no locale commas in SVG numbers).
                dot = self.page.evaluate("""() => { const svg = document.querySelector('.learn-trend'), c = svg.querySelector('circle');
                    return {svg: svg.getBoundingClientRect().toJSON(), dot: c.getBoundingClientRect().toJSON(),
                            dash: document.querySelector('.learn-ring-fill').getAttribute('stroke-dasharray')}; }""")
                self.assertGreater(dot["dot"]["x"], dot["svg"]["x"] + dot["svg"]["width"] * 0.8)
                self.assertRegex(dot["dash"], r"^\d+\.\d+ \d+\.\d+$")
                expect(section.get_by_role("link", name="Vezi greșelile din categoria Ordinea cuvintelor")).to_have_attribute(
                    "href", "/mistakes/word_order/")
                self.assertEqual(self.page.evaluate("getComputedStyle(document.querySelector('.learn-change-badge')).textTransform"),
                                 "uppercase")
                layout = self.page.evaluate(measure)
                first, second, wide = layout["cards"]
                self.assertLessEqual(layout["scrollWidth"], width)
                self.assertTrue(wide["wide"] and not first["wide"] and not second["wide"])
                self.assertGreater(wide["box"]["y"], first["box"]["y"] + first["box"]["height"] - 1)
                for card in layout["cards"]:
                    self.assertLessEqual(card["box"]["x"] + card["box"]["width"], layout["grid"]["x"] + layout["grid"]["width"] + 1)
                if width >= 768:  # Two equal cards side by side, the almost solved card across both.
                    self.assertLess(abs(first["box"]["y"] - second["box"]["y"]), 2)
                    self.assertGreater(second["box"]["x"], first["box"]["x"] + first["box"]["width"])
                    self.assertLess(abs(first["box"]["width"] - second["box"]["width"]), 2)
                    self.assertGreater(wide["box"]["width"], first["box"]["width"] * 1.9)
                    self.assertGreaterEqual(wide["chart"]["x"], wide["copy"]["x"] + wide["copy"]["width"])  # Ring on the right.
                else:  # One column.
                    self.assertGreater(second["box"]["y"], first["box"]["y"] + first["box"]["height"] - 1)
                    self.assertLess(abs(first["box"]["width"] - wide["box"]["width"]), 2)
                if width >= 1280:
                    self.assertGreaterEqual(first["chart"]["x"], first["copy"]["x"] + first["copy"]["width"])  # Chart beside text.
                section.screenshot(path=str(self.artifacts / f"progress-changes-{width}.png"))
        self.assertEqual(self.errors, [])

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

    def test_correction_and_natural_version_are_separate_collapsible_boxes(self):
        for width in (390, 360, 1440):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": 900})
                self.page.goto(self.live_server_url)
                self.page.locator("#text").fill("Native example: I didn't went to work yesterday.")
                self.submit()
                boxes = self.page.locator("#result .result-box")
                expect(boxes).to_have_count(2)
                corrected, natural = boxes.nth(0), boxes.nth(1)
                expect(corrected.get_by_role("heading", level=2)).to_have_text("Engleza ta, corectată")
                expect(natural.get_by_role("heading", level=2)).to_have_text("Sună mai natural:")
                expect(natural).not_to_contain_text("Engleză britanică")  # No pill.
                for box, speaker in ((corrected, "Ascultă varianta corectă în engleză britanică"),
                                     (natural, "Ascultă varianta naturală în engleză britanică")):
                    heading = box.locator(".result-heading")
                    title = heading.get_by_role("heading", level=2).bounding_box()
                    button = heading.get_by_role("button", name=speaker).bounding_box()
                    self.assertLess(button["x"] + button["width"], title["x"] + 1)  # Speaker left of the title.
                    self.assertLess(title["height"], 32)  # The title stays on one line.
                title = corrected.locator(".result-heading h2").bounding_box()
                arrow = corrected.locator("[data-collapse-toggle]").bounding_box()
                self.assertGreaterEqual(arrow["x"], title["x"] + title["width"])  # Arrow on the right of the title.
                edge = corrected.bounding_box()
                self.assertLessEqual(arrow["x"] + arrow["width"], edge["x"] + edge["width"])
                expect(natural.locator("[data-collapse-toggle]")).to_have_count(0)  # The natural version never closes.
                first = corrected.bounding_box()
                self.assertGreater(natural.bounding_box()["y"], first["y"] + first["height"])  # Separate boxes.
                self.assert_no_overflow(self.page, width)
                # A discreet copy icon at the bottom right of each English sentence copies just that sentence.
                self.page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=self.live_server_url)
                for sentence in (corrected.locator(".corrected-sentence"), natural.locator(".native-sentence")):
                    box, copy = sentence.bounding_box(), sentence.get_by_role("button", name="Copiază textul")
                    icon = copy.bounding_box()
                    self.assertGreater(icon["x"], box["x"] + box["width"] * 0.7)
                    self.assertGreater(icon["y"] + icon["height"], box["y"] + box["height"] - 12)
                    self.assertLessEqual(icon["x"] + icon["width"], box["x"] + box["width"] + 1)
                copy = natural.locator(".native-sentence").get_by_role("button", name="Copiază textul")
                copy.click()
                expect(natural.get_by_role("button", name="Copiat")).to_be_visible()
                self.assertEqual(self.page.evaluate("navigator.clipboard.readText()"),
                                 natural.locator(".native-sentence").text_content().strip())
                self.page.screenshot(path=str(self.artifacts / f"natural-boxes-{width}.png"), full_page=True)
                # The details start closed, so the corrected sentence and the natural version are what is read first.
                toggle = corrected.locator("[data-collapse-toggle]")
                expect(toggle).to_have_attribute("aria-expanded", "false")
                expect(toggle.locator(".collapse-label")).to_have_text("Detalii")
                expect(corrected.locator(".corrected-sentence")).to_be_visible()  # The sentence stays.
                for hidden in (".correction-caption", ".correction-comparison", ".sentence-comparison"):
                    expect(corrected.locator(hidden)).to_be_hidden()  # Everything under it is collapsed.
                expect(natural.locator(".native-sentence")).to_be_visible()
                self.page.screenshot(path=str(self.artifacts / f"natural-boxes-collapsed-{width}.png"), full_page=True)
                toggle.click()
                expect(toggle).to_have_attribute("aria-expanded", "true")
                expect(corrected.locator(".correction-comparison")).to_be_visible()
                toggle.click()
                expect(corrected.locator(".correction-comparison")).to_be_hidden()
        self.assertEqual(self.errors, [])

    # ----- Daily plan quota -----
    ANONYMOUS_ACTOR = "anon:" + salted_hmac("assistant-rate", "127.0.0.1").hexdigest()  # The live server's REMOTE_ADDR.

    def set_usage(self, actor, used):
        self.in_database_thread(lambda: NaturalizeUsage.objects.update_or_create(
            actor=actor, day=local_day(), defaults={"used": used, "reserved": 0}))

    def usage(self, actor):
        return self.in_database_thread(lambda: NaturalizeUsage.objects.filter(actor=actor).values_list("used", flat=True)
                                       .first())

    def naturalize_text(self, text=None):
        self.page.locator("#text").fill(text or correction_result().original_text)
        self.submit()
        expect(self.page.locator(".result-loading")).to_have_count(0)

    @override_settings(NATURALIZE_DAILY_LIMITS={"anonymous": 5, "free": 20, "pro": 200}, VOICE_REALTIME_ENABLED=False)
    def test_anonymous_quota_invites_to_create_an_account_and_loaded_speech_still_plays(self):
        speak = patch("apps.assistant.voice_views.synthesize_speech", return_value=(
            b"ID3fake-mp3", AudioUsage(model="gpt-4o-mini-tts", voice="cedar", input_tokens=8, output_tokens=90,
                                       total_tokens=98))).start()
        self.page.add_init_script(MEDIA_MOCKS)
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.goto(self.live_server_url)
        self.set_usage(self.ANONYMOUS_ACTOR, 3)
        self.naturalize_text()
        expect(self.page.locator(".quota-note")).to_have_text("1 din 5 utilizări rămase astăzi")
        self.naturalize_text()
        expect(self.page.locator(".quota-note")).to_have_text("0 din 5 utilizări rămase astăzi")
        speaker = self.page.get_by_role("button", name="Ascultă varianta corectă în engleză britanică")
        speaker.click()
        expect(speaker).to_have_attribute("aria-pressed", "true")
        self.assertEqual(self.usage(self.ANONYMOUS_ACTOR), 5)  # Listening used nothing.
        self.naturalize_text()
        alert = self.page.locator(".quota-box[role=alert]")
        expect(alert).to_contain_text("Ai folosit cele 5 utilizări gratuite de azi. Creează un cont gratuit și primești 20 pe zi.")
        expect(alert.get_by_role("link", name="Creează cont gratuit")).to_have_attribute("href", "/accounts/signup/")
        expect(self.page.locator("#text")).to_have_value(correction_result().original_text)  # The text is kept.
        expect(self.page.locator(".quota-note")).to_have_count(0)
        self.assertNotIn("voce", alert.inner_text().lower())  # There is no separate voice allowance.
        self.assertEqual(self.usage(self.ANONYMOUS_ACTOR), 5)
        self.assert_no_overflow(self.page, 390)
        self.page.screenshot(path=str(self.artifacts / "quota-anonymous-390.png"), full_page=True)
        self.assertEqual(speak.call_count, 1)
        self.assertEqual(self.errors, [])

    @override_settings(NATURALIZE_DAILY_LIMITS={"anonymous": 5, "free": 20, "pro": 200})
    def test_free_quota_points_to_pro_and_pro_gets_the_fair_use_message(self):
        self.sign_in_learner("quota-learner")
        user = self.in_database_thread(lambda: User.objects.get(username="quota-learner"))
        actor = f"user:{user.pk}"
        self.page.set_viewport_size({"width": 1440, "height": 1000})
        self.set_usage(actor, 19)
        self.naturalize_text("The twentieth text of the day.")
        expect(self.page.locator(".quota-note")).to_have_text("0 din 20 de utilizări rămase astăzi")
        self.naturalize_text("One text too many.")
        alert = self.page.locator(".quota-box[role=alert]")
        expect(alert).to_contain_text("Ai folosit cele 20 de utilizări de azi. Cu Pro ai cereri nelimitate, "
                                      "în regim Fair Use.")
        expect(alert.get_by_role("link", name="Vezi planul Pro")).to_have_attribute("href", "/about/#plans")
        self.page.screenshot(path=str(self.artifacts / "quota-free-1440.png"))
        self.page.goto(self.live_server_url + "/accounts/profile/")
        expect(self.page.locator(".plan-status")).to_have_text("Plan Free · 0 din 20 de utilizări rămase astăzi")
        for path in ("/history/", "/progress/", "/mistakes/"):  # Nothing else is blocked.
            self.page.goto(self.live_server_url + path)
            expect(self.page.locator("h1")).to_be_visible()
        self.in_database_thread(lambda: user.groups.add(Group.objects.get_or_create(name="Pro")[0]))
        self.set_usage(actor, 199)
        self.page.goto(self.live_server_url)
        self.naturalize_text("The two hundredth text of the day.")
        expect(self.page.locator(".result-text").first).to_be_visible()
        expect(self.page.locator(".quota-note")).to_have_count(0)  # Pro is not counted down.
        self.naturalize_text("One Pro text too many.")
        alert = self.page.locator(".quota-box[role=alert]")
        expect(alert).to_contain_text("Ai atins limita Fair Use de 200 de utilizări pentru astăzi. Limita se resetează la "
                                      "miezul nopții (ora Regatului Unit).")
        expect(alert.get_by_role("link")).to_have_count(0)  # No plan to upgrade to.
        self.page.goto(self.live_server_url + "/accounts/profile/")
        expect(self.page.locator(".plan-status")).to_have_text("Plan Pro · Cereri nelimitate (Fair Use)")
        self.assertEqual(self.usage(actor), 200)
        self.assertEqual(self.errors, [])

    def test_polite_mode_switch_is_accessible_posted_and_remembered(self):
        for width in (1440, 390):
            with self.subTest(width=width):
                context = self.browser.new_context(viewport={"width": width, "height": 900})
                context.add_cookies([self.consent_cookie()])
                page = context.new_page()
                page.on("pageerror", lambda error: self.errors.append(str(error)))
                page.goto(self.live_server_url)
                switch = page.get_by_role("switch", name="Mod Politicos")
                expect(switch).not_to_be_checked()
                # Upper right of the writing panel, above the text box.
                top, text = page.locator(".editor-top").bounding_box(), page.locator("#text").bounding_box()
                label, panel = page.locator(".polite-switch").bounding_box(), page.locator(".editor-panel").bounding_box()
                self.assertLessEqual(top["y"] + top["height"], text["y"] + 2)
                self.assertGreater(label["x"] + label["width"], panel["x"] + panel["width"] * 0.75)  # Right-aligned.
                self.assertLess(page.locator(".polite-info summary").bounding_box()["x"], panel["x"] + panel["width"])
                self.assertLess(label["height"], 40)  # A compact switch, not a large button.
                switch.focus()
                page.keyboard.press("Space")
                expect(switch).to_be_checked()
                page.locator(".polite-info summary").click()
                expect(page.locator("#polite-info-text")).to_be_visible()
                page.locator("#text").fill("Give me the report by Friday.")
                with page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/naturalize/")) as sent:
                    page.get_by_role("button", name=ACTION, exact=True).click()
                self.assertEqual(parse_qs(sent.value.post_data).get("polite"), ["on"])
                expect(page.locator(".result-text").first).to_be_visible()
                self.assertTrue(self.naturalize.call_args.kwargs["polite"])
                page.screenshot(path=str(self.artifacts / f"polite-mode-{width}.png"))
                page.reload()
                expect(page.get_by_role("switch", name="Mod Politicos")).to_be_checked()  # Remembered in this browser.
                context.close()
        self.assertEqual(self.errors, [])

    def test_clear_button_empties_the_text_box(self):
        clear = self.page.locator("[data-clear-text]")
        for width, height in ((390, 844), (360, 640), (1440, 900)):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": height})
                self.page.goto(self.live_server_url)
                text = self.page.locator("#text")
                expect(clear).to_be_hidden()  # Nothing to clear.
                text.fill("I goed home yesterday because I was very tired after the work.")
                expect(self.page.get_by_role("button", name="Șterge tot textul")).to_be_visible()
                box, button = text.bounding_box(), clear.bounding_box()
                self.assertGreater(button["x"], box["x"] + box["width"] / 2)  # Top right, inside the box.
                self.assertLessEqual(button["x"] + button["width"], box["x"] + box["width"])
                self.assertLess(button["y"] - box["y"], 20)
                self.assertGreaterEqual(button["width"], 40)
                self.assertEqual(clear.get_attribute("tabindex"), "-1")  # The keyboard already has Delete.
                # Text keeps clear of the button.
                self.assertGreaterEqual(text.evaluate("t => parseFloat(getComputedStyle(t).paddingRight)"), button["width"])
                self.page.screenshot(path=str(self.artifacts / f"clear-button-{width}.png"))
                clear.click()
                expect(text).to_have_value("")
                expect(text).to_be_focused()
                expect(clear).to_be_hidden()
                expect(self.page.locator(".mobile-count .count-hint")).to_have_text("Scrie în acest ecran sau vorbește aici")
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
        expect(self.page.locator(".mobile-count .count-hint")).to_have_text("Scrie în acest ecran sau vorbește aici")
        expect(self.page.locator(".mobile-count .count-value")).to_be_hidden()
        # The hint stays in the page for aria-describedby, but steps aside while an example is being typed, because on
        # a short box its white background would sit over the sentence's last line.
        opacity = "() => getComputedStyle(document.querySelector('.character-count')).opacity"
        self.assertEqual(self.page.evaluate(opacity), "0")
        text.focus()
        expect(example).to_be_hidden()
        expect(self.page.locator(".mobile-count .count-hint")).to_be_visible()
        self.assertEqual(self.page.evaluate(opacity), "1")
        self.page.locator("#result").click()
        expect(example).to_be_visible(timeout=6000)
        # Typed with the mistake, then the mistake is struck through in red and the correction typed beside it in green.
        self.page.wait_for_function("s => document.querySelector('.example-prompt').textContent === s", arg=TYPED[0])
        self.page.wait_for_function("s => document.querySelector('.example-prompt').textContent === s", arg=CORRECTED[0])
        marks = self.page.evaluate("""() => {
            const style = s => getComputedStyle(document.querySelector(s));
            return {wrong: document.querySelector('.example-wrong').textContent, right: document.querySelector('.example-right').textContent,
                    strike: style('.example-wrong').textDecorationLine, red: style('.example-wrong').color, green: style('.example-right').color};
        }""")
        self.assertEqual(marks, {"wrong": EXAMPLES[0][1], "right": f" {EXAMPLES[0][2]}", "strike": "line-through",
                                 "red": "rgb(200, 32, 49)", "green": "rgb(15, 122, 66)"})
        self.assertEqual(text.input_value(), "")
        self.page.wait_for_function("document.querySelector('.example-prompt').textContent.startsWith('Sorry, I have')")
        self.assertEqual(text.input_value(), "")
        for width, height in ((390, 844), (375, 667), (360, 740)):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": height})
                expect(example).to_be_visible()
                layout = self.page.evaluate("""() => {
                    const example = document.querySelector('.example-prompt');
                    const r = s => document.querySelector(s).getBoundingClientRect().toJSON();
                    const box = example.getBoundingClientRect();
                    return {example: box, mic: r('[data-voice-record]'), count: r('.mobile-count'),
                            // How far the sentence itself may reach: the layer copies the box's right padding.
                            textRight: box.right - parseFloat(getComputedStyle(example).paddingRight),
                            countOpacity: getComputedStyle(document.querySelector('.character-count')).opacity,
                            box: r('#text'), scrollWidth: document.documentElement.scrollWidth};
                }""")
                # The sentence never reaches the microphone: the text box's right padding, which the layer copies,
                # keeps it clear whatever the height. On a short box the layer does take the counter's strip, so the
                # counter fades while an example is showing (it returns the moment the box is focused).
                self.assertLessEqual(layout["textRight"], layout["mic"]["left"])
                self.assertEqual(layout["countOpacity"], "0")
                self.assertGreaterEqual(layout["example"]["left"], layout["box"]["left"])
                self.assertLessEqual(layout["example"]["right"], layout["box"]["right"])
                self.assertLessEqual(layout["example"]["bottom"], layout["box"]["bottom"])
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
        with self.page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/naturalize/")) as sent:
            self.submit()
        body = sent.value.post_data or ""
        self.assertEqual(parse_qs(body, keep_blank_values=True).get("text"), [""])
        for sentence in TYPED:
            self.assertNotIn(sentence[:12], unquote_plus(body))
        expect(self.page.locator(".error-box")).to_contain_text("scrie ceva în casetă sau apasă microfonul")
        self.page.locator("#text").fill("I goed home.")
        with self.page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/naturalize/")) as sent:
            self.submit()
        fields = parse_qs(sent.value.post_data, keep_blank_values=True)
        self.assertEqual(fields["text"], ["I goed home."])
        self.assertEqual(set(fields), {"csrfmiddlewaretoken", "submission_token", "leave_empty", "text"})  # No operation.
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
            expect(example.locator(".example-wrong")).to_have_text(EXAMPLES[0][1])
            expect(example.locator(".example-right")).to_have_text(EXAMPLES[0][2])
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

        def send():
            page.locator("#text").fill(correction_result().original_text)
            self.submit(page)
            expect(page.locator(".result-text")).to_have_text(correction_result().corrected_text)

        # Nothing is blocked: the action works while the notice shows, and anonymous analytics is on by default.
        send()
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
        send()
        self.assertNotIn(VISITOR_COOKIE, self.cookie_values(context))
        save_cookie_settings(True)
        send()
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
        expect(page.locator(".landing-wide-only").first).to_be_hidden()
        expect(page.get_by_role("button", name=ACTION, exact=True)).to_be_visible()
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

    # ----- Accessibility -----
    def test_accessibility_with_axe(self):
        source = Path(os.environ.get("AXE_CORE_PATH") or Path(settings.BASE_DIR) / "artifacts" / "axe.min.js")
        if not source.exists():
            self.skipTest("axe-core not available: set AXE_CORE_PATH to axe.min.js")
        script = source.read_text(encoding="utf-8")
        checked = []

        def audit(name):
            self.page.add_script_tag(content=script)
            violations = self.page.evaluate("""async () => (await axe.run(document, {runOnly: {type: 'tag',
                values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})).violations.map(v => ({id: v.id, impact: v.impact,
                help: v.help, nodes: v.nodes.slice(0, 5).map(n => ({target: n.target.join(' '),
                summary: n.failureSummary}))}))""")
            (self.artifacts / f"axe-{name}.json").write_text(json.dumps(violations, indent=2), encoding="utf-8")
            checked.append((name, [violation for violation in violations if violation["impact"] in ("serious", "critical")]))

        for width in (390, 1440):
            self.page.set_viewport_size({"width": width, "height": 900})
            self.page.goto(self.live_server_url)
            audit(f"home-{width}")
        self.page.set_viewport_size({"width": 390, "height": 844})
        for value, name in ((correction_result().original_text, "english"), (UNNATURAL, "unnatural"), (ROMANIAN, "romanian")):
            self.page.goto(self.live_server_url)
            self.page.locator("#text").fill(value)
            self.submit()
            expect(self.page.locator(".result-loading")).to_have_count(0)
            expect(self.page.locator("#result-actions")).to_be_visible()
            self.page.wait_for_timeout(400)  # Let the buttons' 0.15 s colour transition finish before measuring contrast.
            audit(f"result-{name}")
        self.page.goto(self.live_server_url + "/about/")
        audit("despre")
        with self.settings(NATURALIZE_DAILY_LIMITS={"anonymous": 5, "free": 20, "pro": 200}):
            self.set_usage(self.ANONYMOUS_ACTOR, 5)
            self.page.goto(self.live_server_url)
            self.page.locator("#text").fill(correction_result().original_text)
            self.submit()
            expect(self.page.locator(".quota-box")).to_be_visible()
            self.page.wait_for_timeout(400)
            audit("quota-anonymous")
        self.assertEqual([(name, found) for name, found in checked if found], [])
