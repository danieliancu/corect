"""The Terms/Privacy notice bar and cookie settings."""
from playwright.sync_api import expect

from apps.analytics.services.visitors import VISITOR_COOKIE
from apps.assistant.tests.examples import correction_result
from apps.core.consent import CONSENT_COOKIE, consent_cookie_value
from .base import BrowserTestCase
from .mocks import ACTION


class LegalChecks(BrowserTestCase):
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
