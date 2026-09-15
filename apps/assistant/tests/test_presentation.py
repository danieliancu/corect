from types import SimpleNamespace

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from apps.assistant.presentation import history_group, history_kind, history_label, result_outcome
from apps.assistant.templatetags.assistant_ui import sentence_comparison
from .examples import TRANSLATION_CASES, correction_result, translation_result


def entry(kind, language, result):
    return SimpleNamespace(request_type=kind, detected_language=language, result_data=result)


class CorrectionPresentationTests(SimpleTestCase):
    def test_highlights_explanatory_snippets_and_preserves_text(self):
        result = correction_result().model_dump()
        comparison = sentence_comparison(result)
        self.assertEqual("".join(p["text"] for p in comparison["original"]), result["original_text"])
        self.assertEqual("".join(p["text"] for p in comparison["corrected"]), result["corrected_text"])
        self.assertEqual([p["text"] for p in comparison["original"] if p["changed"]], ["didn't went"])
        self.assertEqual([p["text"] for p in comparison["corrected"] if p["changed"]], ["didn't go"])

    def test_unchanged_repeated_snippet_is_not_highlighted(self):
        result = {"original_text": "go now and go later", "corrected_text": "go now and went later",
                  "corrections": [{"original": "go", "replacement": "went"}]}
        comparison = sentence_comparison(result)
        self.assertEqual(sum(p["changed"] for p in comparison["original"]), 1)
        self.assertEqual(comparison["original"][0], {"text": "go now and ", "changed": False})

    def test_capitalisation_punctuation_and_spacing_are_not_highlighted(self):
        result = sentence_comparison({"original_text": "i  was there tomorow, ok", "corrected_text": "I was there tomorrow. OK!"})
        self.assertEqual([p["text"] for p in result["original"] if p["changed"]], ["tomorow,"])
        self.assertEqual([p["text"] for p in result["corrected"] if p["changed"]], ["tomorrow."])

    def test_insertions_deletions_and_whitespace_preserved(self):
        for original, corrected in [("I  agree.\n", "I agree.\n"), ("I happy.", "I am happy."), ("", "Hello"), ("Hello", "")]:
            with self.subTest(original=original):
                result = sentence_comparison({"original_text": original, "corrected_text": corrected})
                self.assertEqual("".join(p["text"] for p in result["original"]), original)
                self.assertEqual("".join(p["text"] for p in result["corrected"]), corrected)

    def test_highlighting_never_renders_user_html(self):
        result = correction_result().model_dump()
        result["original_text"] = '<script>alert("old")</script>'
        result["corrected_text"] = '<script>alert("new")</script>'
        html = render_to_string("assistant/result.html", {"result": result, "kind": "correction"})
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn('class="sentence-before"', html)
        self.assertIn('class="sentence-after"', html)

    def test_actions_below_result_follow_result_and_sign_in_state(self):
        result = correction_result().model_dump()
        render = lambda **context: render_to_string("assistant/result_actions.html", context)
        for user, sign_in in ((SimpleNamespace(is_authenticated=False), True), (SimpleNamespace(is_authenticated=True), False)):
            with self.subTest(sign_in=sign_in):
                html = render(result=result, kind="correction", user=user)
                self.assertNotIn(" hidden", html.split(">", 1)[0])
                self.assertIn('href="/" data-new-text>Text nou</a>', html)
                self.assertEqual("Autentificare / Creează cont" in html, sign_in)
        translation = render(result=result, kind="translation", user=SimpleNamespace(is_authenticated=True))
        self.assertNotIn(" hidden", translation.split(">", 1)[0])
        for context in ({}, {"result": result, "kind": "correction", "error": "Oops"},
                        {"result": result, "kind": "translation", "error": "Oops"}):
            html = render(**context)
            self.assertIn(" hidden", html.split(">", 1)[0])
            self.assertNotIn("data-new-text", html)
        self.assertNotIn("data-new-text", render_to_string("assistant/result.html", {"result": result, "kind": "correction"}))

    def test_errors_show_the_natural_version_only_when_it_adds_something(self):
        result = correction_result().model_dump()
        html = render_to_string("assistant/result.html", {"result": result, "kind": "correction"})
        self.assertIn('<h2 id="corrected-title">Engleza ta, corectată</h2>', html)
        self.assertNotIn('class="native-version"', html)
        result.update(native_text="I didn't make it to work yesterday.", native_explanation="Sună mai natural.")
        html = render_to_string("assistant/result.html", {"result": result, "kind": "correction"})
        self.assertIn('class="native-version result-box"', html)  # Its own box, beside the correction's box.
        self.assertIn('<h2 id="natural-title">Sună mai natural:</h2>', html)
        self.assertIn('class="correction-block result-box"', html)
        self.assertEqual(html.count("data-collapse-toggle"), 1)  # Only the correction's box collapses.
        self.assertLess(html.index('class="result-text corrected-sentence has-copy"'), html.index('id="corrected-body"'))
        self.assertNotIn("<span>Engleză britanică</span>", html)

    def test_correct_but_unnatural_english_emphasises_the_natural_version_without_errors(self):
        result = correction_result().model_dump()
        result.update(has_errors=False, corrections=[], corrected_text=result["original_text"],
                      native_text="I didn't make it to work yesterday.", native_explanation="Sună mai natural.")
        html = render_to_string("assistant/result.html", {"result": result, "kind": "correction"})
        self.assertIn("<h2>Sună mai natural:</h2>", html)
        self.assertLess(html.index("make it to work"), html.index("✓ Engleza ta e corectă."))
        self.assertNotIn("Greșit", html)
        self.assertNotIn('class="correction-comparison"', html)

    def test_every_english_result_has_a_copy_button_inside_its_sentence(self):
        errors = correction_result().model_dump()
        natural_errors = {**errors, "native_text": "I didn't make it to work yesterday."}
        correct = {**errors, "has_errors": False, "corrections": [], "corrected_text": errors["original_text"]}
        unnatural = {**correct, "native_text": "I didn't make it to work yesterday."}
        cases = [(errors, "correction", 1), (natural_errors, "correction", 2), (correct, "correction", 1),
                 (unnatural, "correction", 1), (translation_result().model_dump(), "translation", 1)]
        for result, kind, copies in cases:
            html = render_to_string("assistant/result.html", {"result": result, "kind": kind})
            self.assertEqual(html.count("data-copy-text"), copies)
            self.assertEqual(html.count('class="copy-button"'), html.count("has-copy"))
            self.assertIn('aria-label="Copiază textul"', html)
            self.assertIn("</button></p>", html)  # Inside the sentence's paragraph, at its end.
        legacy = {**translation_result().model_dump(), "source_language": "en", "target_language": "ro"}
        html = render_to_string("assistant/result.html", {"result": legacy, "kind": "translation"})
        self.assertIn("Engleză → română", html)
        self.assertNotIn("data-copy-text", html)  # Only English results are copied.

    def test_already_natural_english_and_british_english_from_romanian(self):
        result = correction_result().model_dump()
        result.update(has_errors=False, corrections=[], corrected_text=result["original_text"])
        html = render_to_string("assistant/result.html", {"result": result, "kind": "correction"})
        self.assertIn("<h2>✓ Sună deja natural.</h2>", html)
        self.assertNotIn('class="correction-comparison"', html)
        self.assertNotIn("Sună mai natural", html)
        html = render_to_string("assistant/result.html", {"result": translation_result().model_dump(), "kind": "translation"})
        self.assertIn("<h2>În engleză britanică</h2>", html)
        self.assertIn('lang="en-GB">I&#x27;m sorry I couldn&#x27;t get here earlier.', html)
        for jargon in ("Traducere", "traducere", "translation mode", "Română → engleză"):
            self.assertNotIn(jargon, html)

    def test_outcomes_for_new_and_legacy_results(self):
        correction = correction_result().model_dump()
        natural = dict(correction, has_errors=False, corrections=[], native_text="")
        unnatural = dict(natural, native_text="Could you help me?")
        legacy_to_romanian = translation_result(TRANSLATION_CASES[2]).model_dump()
        cases = [("correction", correction, "errors"), ("correction", unnatural, "unnatural"),
                 ("correction", natural, "natural"), ("translation", translation_result().model_dump(), "translated"),
                 ("translation", legacy_to_romanian, "legacy_to_romanian"), ("correction", {}, ""),
                 ("translation", None, ""), ("translation", {"translated_text": "Hello"}, "translated")]
        for kind, result, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(result_outcome(kind, result), expected)

    def test_history_labels_for_new_and_legacy_entries(self):
        cases = [
            (entry("correction", "en", correction_result().model_dump()), "english", "Engleză", ("text în engleză", "texte în engleză")),
            (entry("translation", "ro", translation_result().model_dump()), "into_english", "Română → engleză",
             ("text din română", "texte din română")),
            (entry("translation", "en", translation_result(TRANSLATION_CASES[2]).model_dump()), "legacy_to_romanian",
             "Engleză → română", ("text în română", "texte în română")),
        ]
        for item, kind, label, group in cases:
            with self.subTest(kind=kind):
                self.assertEqual((history_kind(item), history_label(item), history_group(item)), (kind, label, group))
