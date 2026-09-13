from types import SimpleNamespace
from unittest.mock import patch

import httpx
from django.test import SimpleTestCase, override_settings
from openai import APIConnectionError, APITimeoutError
from pydantic import ValidationError

from apps.assistant.schemas import CorrectionResult, TranslationResult
from apps.assistant.services.correction import CorrectionService
from apps.assistant.services.openai_client import AssistantError, parse_response
from apps.assistant.services.translation import TranslationService
from .examples import CORRECTION_CASES, TRANSLATION_CASES, correction_result, translation_result


class ServiceTests(SimpleTestCase):
    def setUp(self):
        self.patcher = patch("apps.assistant.services.openai_client.OpenAI")
        self.sdk = self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.client = self.sdk.return_value.__enter__.return_value

    def response(self, result, **kwargs):
        values = dict(status="completed", output=[], output_parsed=result)
        values.update(kwargs)
        self.client.responses.parse.return_value = SimpleNamespace(**values)

    def test_all_correction_examples_and_single_structured_call(self):
        for case in CORRECTION_CASES:
            with self.subTest(text=case[0]):
                self.response(correction_result(case))
                self.client.responses.parse.reset_mock()
                result = CorrectionService().correct(case[0])
                self.assertEqual(result.corrected_text, case[1])
                self.client.responses.parse.assert_called_once()
                kwargs = self.client.responses.parse.call_args.kwargs
                self.assertIs(kwargs["text_format"], CorrectionResult)
                self.assertFalse(kwargs["store"])
                self.assertEqual(kwargs["input"][1]["content"], case[0])
                self.assertEqual(self.sdk.call_args.kwargs["max_retries"], 0)

    def test_all_translation_examples(self):
        for case in TRANSLATION_CASES:
            with self.subTest(text=case[0]):
                self.response(translation_result(case))
                result = TranslationService().translate(case[0])
                self.assertEqual(result.translated_text, case[1])
                self.assertIs(self.client.responses.parse.call_args.kwargs["text_format"], TranslationResult)

    def test_correct_text_preserved_even_if_model_polishes(self):
        result = correction_result(CORRECTION_CASES[4])
        result.corrected_text = "I have resided here for five years."
        self.response(result)
        self.assertEqual(CorrectionService().correct(result.original_text).corrected_text, result.original_text)

    def test_british_preference_is_not_an_error(self):
        result = CorrectionResult(detected_language="en", original_text="I like this color.", corrected_text="I like this colour.",
            has_errors=False, overall_explanation="Ambele variante sunt corecte.", corrections=[dict(original="color", replacement="colour",
            category="british_english", severity="suggestion", explanation_ro="Colour este varianta britanică.", is_british_english_preference=True)],
            native_text="", native_explanation="")
        self.response(result)
        self.assertEqual(CorrectionService().correct(result.original_text).corrected_text, result.original_text)

    def test_invalid_schema(self):
        payload = correction_result().model_dump()
        payload["corrections"][0]["category"] = "invented_category"
        with self.assertRaises(ValidationError):
            CorrectionResult.model_validate(payload)
        payload = correction_result().model_dump()
        payload["has_errors"] = False
        with self.assertRaises(ValidationError):
            CorrectionResult.model_validate(payload)

    def test_empty_and_long_inputs_do_not_call_provider(self):
        for text in ("", "   ", "x" * 2001):
            with self.subTest(text_length=len(text)), self.assertRaises(AssistantError):
                CorrectionService().correct(text)
        self.client.responses.parse.assert_not_called()

    def test_refusal_incomplete_and_missing_output(self):
        cases = [dict(status="incomplete"), dict(output_parsed=None), dict(output=[SimpleNamespace(content=[SimpleNamespace(type="refusal")])])]
        for case in cases:
            self.response(correction_result(), **case)
            with self.assertRaises(AssistantError):
                CorrectionService().correct(CORRECTION_CASES[0][0])

    def test_api_errors_are_sanitised(self):
        for error in (APIConnectionError(request=httpx.Request("POST", "https://api.openai.com")),
                      APITimeoutError(request=httpx.Request("POST", "https://api.openai.com"))):
            self.client.responses.parse.side_effect = error
            with self.assertRaises(AssistantError) as raised:
                CorrectionService().correct(CORRECTION_CASES[0][0])
            self.assertNotIn("api.openai.com", raised.exception.message)

    @override_settings(OPENAI_API_KEY="")
    def test_missing_configuration(self):
        with self.assertRaises(AssistantError) as raised:
            CorrectionService().correct("Hello.")
        self.assertEqual(raised.exception.code, "not_configured")
        self.sdk.assert_not_called()

    def test_romanian_correction_guidance(self):
        self.response(CorrectionResult(detected_language="ro", original_text="Bună ziua.", corrected_text="Bună ziua.", has_errors=False, corrections=[], overall_explanation="",
            native_text="", native_explanation=""))
        with self.assertRaises(AssistantError) as raised:
            CorrectionService().correct("Bună ziua.")
        self.assertEqual(raised.exception.code, "romanian_input")

    def test_invalid_original_and_snippet(self):
        for field in ("original", "snippet"):
            result = correction_result()
            if field == "original":
                result.original_text = "Changed input"
            else:
                result.corrections[0].original = "not in input"
            self.response(result)
            with self.assertRaises(AssistantError):
                CorrectionService().correct(CORRECTION_CASES[0][0])

    def test_capitalisation_and_punctuation_are_fixed_silently(self):
        spelling = dict(original="tomorow", replacement="tomorrow", category="spelling", severity="minor",
                        explanation_ro="Se scrie cu doi r.", is_british_english_preference=False)
        capital = dict(original="i", replacement="I", category="spelling", severity="minor",
                       explanation_ro="Am adăugat majuscula.", is_british_english_preference=False)
        for corrections, errors in (([capital], False), ([capital, spelling], True)):
            with self.subTest(errors=errors):
                text = "i see you tomorow" if errors else "i see you tomorrow"
                self.response(CorrectionResult(detected_language="en", original_text=text, corrected_text="I see you tomorrow.",
                    has_errors=True, corrections=corrections, overall_explanation="Am adăugat majuscula și punctul.",
                    native_text="", native_explanation=""))
                result = CorrectionService().correct(text)
                self.assertEqual(result.corrected_text, "I see you tomorrow.")
                self.assertEqual(result.has_errors, errors)
                self.assertEqual([item.original for item in result.corrections], ["tomorow"] if errors else [])
                self.assertEqual(bool(result.overall_explanation), errors)

    def test_native_version_only_when_it_differs_from_correction(self):
        for native, expected in (("I didn't  go to work yesterday.", ""), ("I didn't make it to work yesterday.", "I didn't make it to work yesterday.")):
            with self.subTest(native=native):
                result = correction_result()
                result.native_text, result.native_explanation = native, "Sună mai natural."
                self.response(result)
                corrected = CorrectionService().correct(CORRECTION_CASES[0][0])
                self.assertEqual(corrected.native_text, expected)
                self.assertEqual(bool(corrected.native_explanation), bool(expected))

    def test_echoed_spacing_differences_are_accepted(self):
        text = CORRECTION_CASES[0][0]
        result = correction_result()
        result.original_text = f" {text}  "
        self.response(result)
        self.assertEqual(CorrectionService().correct(text).original_text, text)
        self.response(TranslationResult(source_language="en", target_language="ro", original_text="Hello  there ", translated_text="Salut"))
        self.assertEqual(TranslationService().translate("Hello there").original_text, "Hello there")

    def test_translation_invalid_direction_and_unsupported_language(self):
        for source, target in (("en", "en"), ("other", "ambiguous"), ("ambiguous", "ambiguous")):
            self.response(TranslationResult(source_language=source, target_language=target, original_text="Hello", translated_text="Salut"))
            with self.assertRaises(AssistantError):
                TranslationService().translate("Hello")
