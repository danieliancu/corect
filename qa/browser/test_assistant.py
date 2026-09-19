"""„Vreau să sune natural!”: results, errors, examples, polite mode and the editor."""
from urllib.parse import parse_qs, unquote_plus

from playwright.sync_api import expect

from apps.assistant.tests.examples import CORRECTION_CASES, correction_result
from .base import BrowserTestCase
from .mocks import ACTION, BRITISH, CORRECTED, EXAMPLES, ROMANIAN, TYPED, UNNATURAL


class AssistantChecks(BrowserTestCase):
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

    def test_desktop_result_card_is_as_tall_as_the_editor_unless_longer(self):
        self.page.set_viewport_size({"width": 1440, "height": 900})
        self.page.goto(self.live_server_url)
        self.page.locator("#text").fill(ROMANIAN)
        self.submit()
        expect(self.page.locator("#result").get_by_role("heading", level=2).first).to_have_text("În engleză britanică")
        editor = self.page.locator(".editor-panel").bounding_box()
        result = self.page.locator("#result").bounding_box()
        self.assertAlmostEqual(result["y"], editor["y"], delta=1)
        self.assertAlmostEqual(result["height"], editor["height"], delta=2)  # a short result: the same height
        self.page.screenshot(path=str(self.artifacts / "result-same-height-1440.png"))
        # A long result makes the card taller; the editor keeps its own height.
        self.page.evaluate("document.querySelector('#result .result-text').textContent = 'Long line. '.repeat(400)")
        editor = self.page.locator(".editor-panel").bounding_box()
        result = self.page.locator("#result").bounding_box()
        self.assertGreater(result["height"], editor["height"] + 100)
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
