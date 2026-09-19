"""The base for every browser check: a live server on the test database, a fresh Chromium page per test with the
Terms already accepted (analytics off), and a mocked „Vreau să sune natural!” so no paid call is made.

Checks are grouped by area in the test_*.py modules next to this one; qa/browser_check.py still runs them all.
"""
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.db import connections
from django.test import override_settings
from django.utils.crypto import salted_hmac
from playwright.sync_api import sync_playwright, expect

from apps.accounts.models import LegalAcceptance
from apps.accounts.testing import make_pro, verified_user
from apps.analytics.models import AudioUsageEvent
from apps.assistant.models import NaturalizeUsage
from apps.assistant.schemas import TranslationResult
from apps.assistant.services.localday import local_day
from apps.assistant.services.naturalize import Naturalized
from apps.assistant.services.openai_client import AssistantError
from apps.assistant.services.voice import AudioUsage
from apps.assistant.tests.examples import CORRECTION_CASES, correction_result
from apps.core.consent import CONSENT_COOKIE, consent_cookie_value
from apps.learning.services.profile import record_correction_occurrences
from apps.learning.tests.helpers import make_correction, make_exercise
from .mocks import ACTION, BRITISH, REALTIME_MOCKS


@override_settings(NATURALIZE_RATE_LIMIT_MINUTE=100, NATURALIZE_DAILY_LIMITS={"anonymous": 1000, "free": 1000, "pro": 1000})
class BrowserTestCase(StaticLiveServerTestCase):
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

    # ----- Learning dashboard and personalised practice -----
    def sign_in_learner(self, username, mistakes=0, exercises=0, pro=False):
        """A learner with a confirmed address who accepted the current Terms, with repeated since/for mistakes and stored
        exercises for them; `pro` for the Pro pages (dashboard, progress, mistakes, practice)."""
        def seed():
            user = verified_user(username, f"{username}@example.com", "Browser-test-password-815")
            if pro:
                make_pro(user)
            LegalAcceptance.objects.create(user=user, terms_version=settings.TERMS_VERSION,
                                           privacy_version=settings.PRIVACY_VERSION, source="visit")
            for days_ago in range(mistakes):
                record_correction_occurrences(user, make_correction(user, days_ago=days_ago + 1))
            make_exercise(user, count=exercises)
        self.in_database_thread(seed)
        self.page.goto(self.live_server_url + "/accounts/login/")
        self.page.locator("#id_login").fill(f"{username}@example.com")
        self.page.locator("#id_password").fill("Browser-test-password-815")
        self.page.locator("#id_password").press("Enter")
        expect(self.page.locator("#text")).to_be_visible()

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
