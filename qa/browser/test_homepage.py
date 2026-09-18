"""Homepage, about page and landing sections, at phone, tablet and desktop widths."""
import json

from django.conf import settings
from django.contrib.auth.models import Group, User
from playwright.sync_api import expect

from apps.accounts.models import LegalAcceptance
from apps.assistant.tests.examples import correction_result
from .base import BrowserTestCase
from .mocks import ACTION


class HomepageChecks(BrowserTestCase):
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
                for selector in (".hero-art", ".hero-lead", ".hero-points.is-wide", ".hero-points.is-compact"):
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
        user = self.in_database_thread(lambda: User.objects.create_user(username="pro-learner", email="pro-learner@example.com",
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
