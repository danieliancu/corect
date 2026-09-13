from types import SimpleNamespace

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from apps.assistant.templatetags.assistant_ui import sentence_comparison
from .examples import correction_result


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
                self.assertIn('href="/" data-new-correction', html)
                self.assertEqual("Autentificare / Creează cont" in html, sign_in)
        translation = render(result=result, kind="translation", user=SimpleNamespace(is_authenticated=True))
        self.assertNotIn(" hidden", translation.split(">", 1)[0])
        self.assertIn('href="/" data-new-correction', translation)
        for context in ({}, {"result": result, "kind": "correction", "error": "Oops"},
                        {"result": result, "kind": "translation", "error": "Oops"}):
            html = render(**context)
            self.assertIn(" hidden", html.split(">", 1)[0])
            self.assertNotIn("data-new-correction", html)
        self.assertNotIn("data-new-correction", render_to_string("assistant/result.html", {"result": result, "kind": "correction"}))

    def test_native_version_shown_only_when_present(self):
        result = correction_result().model_dump()
        html = render_to_string("assistant/result.html", {"result": result, "kind": "correction"})
        self.assertNotIn('class="native-version"', html)
        native = dict(native_text="I didn't make it to work yesterday.", native_explanation="Sună mai natural.")
        unchanged = dict(has_errors=False, corrections=[], corrected_text=result["original_text"])
        for variant in ({}, unchanged):
            with self.subTest(has_errors=not variant):
                html = render_to_string("assistant/result.html", {"result": {**result, **native, **variant}, "kind": "correction"})
                self.assertIn('class="native-version"', html)
                self.assertIn("make it to work", html)

    def test_unchanged_text_and_translation_do_not_show_error_comparison(self):
        result = correction_result().model_dump()
        result.update(has_errors=False, corrections=[], corrected_text=result["original_text"])
        html = render_to_string("assistant/result.html", {"result": result, "kind": "correction"})
        self.assertNotIn('class="correction-comparison"', html)
        self.assertIn("Textul tău este corect", html)
        html = render_to_string("assistant/result.html", {"result": {"translated_text": "Hello", "target_language": "en"}, "kind": "translation"})
        self.assertNotIn('class="correction-comparison"', html)
        self.assertIn("Traducere", html)
