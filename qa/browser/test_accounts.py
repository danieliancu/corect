"""Signup, sign-in, history, profile and pages without JavaScript."""
from playwright.sync_api import expect

from apps.assistant.tests.examples import correction_result
from .base import BrowserTestCase
from .mocks import BRITISH, ROMANIAN


class AccountChecks(BrowserTestCase):
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
