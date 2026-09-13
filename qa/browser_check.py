"""Opt-in Chromium checks. Run separately with the documented test settings."""
import json
import time
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from playwright.sync_api import sync_playwright, expect

from apps.assistant.tests.examples import correction_result, translation_result
from apps.assistant.services.openai_client import AssistantError


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
            raise AssistantError("timeout", "That took too long. Please try again.")
        result = correction_result()
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
                expect(self.page.get_by_role("heading", name="A little clarity starts here.")).to_be_visible()
                self.page.screenshot(path=str(self.artifacts / f"home-{width}.png"), full_page=True)
                self.page.locator("#text").fill(correction_result().original_text)
                self.assertEqual(self.correction.call_count, len(measurements))
                self.page.get_by_role("button", name="Correct", exact=True).click()
                expect(self.page.get_by_role("button", name="Translating…", exact=True)).to_have_count(0)
                expect(self.page.locator(".action-buttons button").nth(1)).to_be_disabled()
                expect(self.page.locator(".result-text")).to_have_text(correction_result().corrected_text)
                expect(self.page.get_by_role("button", name="Correct", exact=True)).to_be_enabled()
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
        self.page.get_by_role("button", name="Correct", exact=True).click()
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
        self.page.get_by_role("button", name="Translate", exact=True).click()
        expect(self.page.locator(".result-text")).to_have_text(translation_result().translated_text)
        self.page.locator("#text").fill("Simulate failure")
        self.page.get_by_role("button", name="Correct", exact=True).click()
        expect(self.page.get_by_role("alert")).to_have_text("That took too long. Please try again.")
        expect(self.page.locator("#text")).to_have_value("Simulate failure")
        expect(self.page.get_by_role("button", name="Correct", exact=True)).to_be_enabled()
        self.page.locator("#text").fill("Long example")
        self.page.get_by_role("button", name="Correct", exact=True).click()
        expect(self.page.locator(".result-text")).to_contain_text("I've been learning English")
        self.assertEqual(self.page.evaluate("document.documentElement.scrollWidth"), 390)
        self.page.screenshot(path=str(self.artifacts / "long-result-390.png"), full_page=True)
        self.page.locator("#navigation-menu summary").click()
        expect(self.page.get_by_role("navigation", name="All pages")).to_be_visible()
        self.page.keyboard.press("Escape")
        expect(self.page.get_by_role("navigation", name="All pages")).not_to_be_visible()
        expect(self.page.locator("#navigation-menu summary")).to_be_focused()
        self.assertEqual(self.errors, [])

    def test_authentication_history_practice_and_no_javascript(self):
        self.page.goto(self.live_server_url + "/accounts/signup/")
        self.page.get_by_label("Username").fill("browser-learner")
        self.page.get_by_label("Email").fill("browser@example.com")
        self.page.locator("#id_password1").fill("Browser-test-password-815")
        self.page.locator("#id_password2").fill("Browser-test-password-815")
        self.page.get_by_role("button", name="Create account", exact=True).click()
        expect(self.page.locator("#text")).to_be_visible()
        self.page.locator("#text").fill(correction_result().original_text)
        self.page.get_by_role("button", name="Correct", exact=True).click()
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
        self.page.get_by_role("button", name="Check answer").click()
        expect(self.page.get_by_role("status")).to_contain_text("That's right!")
        self.assertEqual(self.errors, [])
        no_js = self.browser.new_context(java_script_enabled=False, viewport={"width": 390, "height": 844})
        try:
            page = no_js.new_page()
            page.goto(self.live_server_url)
            page.locator("#text").fill(correction_result().original_text)
            page.get_by_role("button", name="Correct", exact=True).click()
            expect(page.locator(".result-text")).to_have_text(correction_result().corrected_text)
            page.get_by_role("button", name="Translate", exact=True).click()
            expect(page.locator(".result-text")).to_have_text(translation_result().translated_text)
        finally:
            no_js.close()
