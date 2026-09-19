"""Learning dashboard, personalised practice and progress."""
from contextlib import ExitStack

from django.contrib.auth.models import User
from playwright.sync_api import expect

from apps.assistant.tests.examples import correction_result
from apps.learning.models import ExerciseAttempt
from apps.learning.services.profile import record_correction_occurrences, refresh_patterns
from apps.learning.tests.helpers import batch, make_correction, make_exercise, patch_ai
from .base import BrowserTestCase


class LearningChecks(BrowserTestCase):
    def test_learning_dashboard_layout_and_personalised_session(self):
        ai = ExitStack()
        self.addCleanup(ai.close)
        model = ai.enter_context(patch_ai(batch()))
        self.sign_in_learner("dashboard-learner", mistakes=3, exercises=5, pro=True)
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
                expect(self.page.locator(".learn-welcome .learn-plan-pill")).to_have_text("Pro")
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
        # Preparing practice takes seconds on the server: the shared waiting screen covers the page meanwhile.
        # The first press is stopped after the page reacted to it (a window listener runs last), to see the screen.
        self.page.evaluate("window.addEventListener('submit', (event) => event.preventDefault(), {once: true})")
        start = self.page.get_by_role("button", name="Exersează: Since / for")
        start.click()
        wait = self.page.locator(".wait-screen")
        expect(wait).to_be_visible()
        expect(wait).to_have_text("Se pregătește exercițiul…")
        self.page.screenshot(path=str(self.artifacts / "wait-screen-practice-390.png"))
        start.click(force=True)  # Covered by the screen: a second press is ignored while the first one runs.
        expect(wait).to_be_visible()
        self.page.evaluate("""() => {
            document.querySelector('.wait-screen').hidden = true;
            document.querySelectorAll('form[data-submitting]').forEach((form) => delete form.dataset.submitting);
        }""")
        start.click()
        for number in range(1, 6):
            expect(self.page.get_by_role("heading", level=1)).to_have_text(f"Exercițiul {number} din 5")
            if number == 1:
                self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), 390)
                self.page.screenshot(path=str(self.artifacts / "learn-session-390.png"), full_page=True)
            self.page.get_by_label("since", exact=True).check()
            self.page.get_by_role("button", name="Verifică").click()
            expect(self.page.get_by_role("status")).to_contain_text("Corect!")
            self.page.get_by_role("button", name="Termină" if number == 5 else "Continuă →").click()
        expect(self.page.get_by_role("heading", name="Perfect!")).to_be_visible()
        finish = self.page.locator(".learn-finish")
        expect(finish).to_contain_text("Ai răspuns corect la 5 din 5 exerciții.")
        expect(finish.get_by_role("img", name="3 din 3 stele")).to_be_visible()
        expect(finish.get_by_role("heading", name="Progresul tău")).to_be_visible()
        expect(finish.get_by_role("button", name="Exersează din nou")).to_be_visible()
        expect(finish.get_by_role("link", name="Înapoi la Greșeli")).to_have_attribute("href", "/mistakes/")
        self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), 390)
        self.page.screenshot(path=str(self.artifacts / "learn-finish-390.png"), full_page=True)
        self.page.set_viewport_size({"width": 1280, "height": 900})
        self.page.screenshot(path=str(self.artifacts / "learn-finish-1280.png"), full_page=True)
        model.assert_not_called()
        self.assertEqual(self.errors, [])

    def test_repeated_correction_leads_to_a_practice_session(self):
        ai = ExitStack()
        self.addCleanup(ai.close)
        model = ai.enter_context(patch_ai(batch("base_form_after_did")))
        self.sign_in_learner("loop-learner", pro=True)
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
        self.sign_in_learner("changes-learner", mistakes=3, pro=True)

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
