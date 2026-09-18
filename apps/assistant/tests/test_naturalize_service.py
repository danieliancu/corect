"""NaturalizeService: English is corrected (and made natural only when that adds something), Romanian goes straight to
British English, and every request makes exactly one generative call."""
from pathlib import Path
from types import SimpleNamespace

import httpx
from django.conf import settings
from django.test import SimpleTestCase, override_settings
from openai import APIConnectionError, APITimeoutError
from pydantic import ValidationError

from apps.assistant.languages import unsupported_message
from apps.assistant.schemas import CorrectionResult, NaturalizeResult, TranslationResult
from apps.assistant.services.naturalize import (POLITE_PROMPT_CACHE_KEY, PROMPT_CACHE_KEY, NaturalizeService,
                                                output_token_budget)
from apps.assistant.services.openai_client import AssistantError
from apps.assistant.services.prompts import (LANGUAGE_RULES, NATURALIZE_POLITE_PROMPT, NATURALIZE_PROMPT,
                                            POLITE_PROMPT_VERSION, POLITE_RULES, PROMPT_VERSION)
from apps.learning.taxonomy import derive_pattern
from .examples import CORRECTION_CASES, ROMANIAN_CASES, english_raw, romanian_raw
from .provider import ProviderMock, moderation


def correction(original, replacement, category="verb_form", severity="minor", explanation="Explicație scurtă.",
               preference=False):
    return dict(original=original, replacement=replacement, category=category,
                pattern=derive_pattern(category, original, replacement), severity=severity, explanation_ro=explanation,
                is_british_english_preference=preference)


def flat(text):
    return " ".join(text.split())


def raw(**fields):
    values = dict(source_language="en", corrected_text="", has_errors=False, corrections=[], overall_explanation="",
                  natural_text="", natural_explanation="")
    values.update(fields)
    return NaturalizeResult(**values)


class NaturalizeEnglishTests(ProviderMock, SimpleTestCase):
    def naturalize(self, text, output):
        self.respond(output)
        return NaturalizeService().naturalize(text)

    def refused(self, text, output):
        self.respond(output)
        with self.assertRaises(AssistantError) as raised:
            NaturalizeService().naturalize(text)
        return raised.exception

    def test_incorrect_english_is_corrected_in_one_structured_call(self):
        for case in [case for case in CORRECTION_CASES if case[2]]:
            with self.subTest(text=case[0]):
                self.api.responses.parse.reset_mock()
                outcome = self.naturalize(case[0], english_raw(case))
                self.assertEqual((outcome.operation, outcome.source_language), ("correction", "en"))
                self.assertIsInstance(outcome.result, CorrectionResult)
                self.assertEqual((outcome.result.original_text, outcome.result.corrected_text), (case[0], case[1]))
                self.assertTrue(outcome.result.has_errors)
                self.assertEqual((outcome.result.corrections[0].category, outcome.result.corrections[0].explanation_ro),
                                 (case[4], case[5]))
                self.api.responses.parse.assert_called_once()
                kwargs = self.api.responses.parse.call_args.kwargs
                self.assertIs(kwargs["text_format"], NaturalizeResult)
                self.assertFalse(kwargs["store"])
                self.assertEqual(kwargs["input"], [{"role": "system", "content": NATURALIZE_PROMPT},
                                                   {"role": "user", "content": case[0]}])
                self.assertEqual(kwargs["prompt_cache_key"], PROMPT_CACHE_KEY)
                self.assertEqual(kwargs["max_output_tokens"], output_token_budget(case[0]))
                self.assertNotIn("reasoning", kwargs)
        self.assertEqual(self.sdk.call_args.kwargs["max_retries"], 0)
        self.sdk.assert_called_once()  # One shared client for every request.

    def test_polite_mode_uses_the_polite_prompt_and_its_own_cache_key_in_the_same_single_call(self):
        text = "Give me the report by Friday."
        self.respond(raw(corrected_text=text, natural_text="Could you send me the report by Friday, please?",
                         natural_explanation="Sună mai politicos."))
        outcome = NaturalizeService().naturalize(text, polite=True)
        self.api.responses.parse.assert_called_once()
        kwargs = self.api.responses.parse.call_args.kwargs
        self.assertEqual(kwargs["input"], [{"role": "system", "content": NATURALIZE_POLITE_PROMPT},
                                           {"role": "user", "content": text}])  # The switch never enters the text.
        self.assertEqual(kwargs["prompt_cache_key"], POLITE_PROMPT_CACHE_KEY)
        self.assertNotEqual(POLITE_PROMPT_CACHE_KEY, PROMPT_CACHE_KEY)
        self.assertTrue(outcome.polite)
        self.assertEqual((outcome.result.corrected_text, outcome.result.has_errors), (text, False))
        self.assertEqual(outcome.result.native_text, "Could you send me the report by Friday, please?")
        self.assertTrue(NATURALIZE_POLITE_PROMPT.startswith(NATURALIZE_PROMPT))
        self.assertIn("natural_text is REQUIRED and is never empty", NATURALIZE_POLITE_PROMPT)
        self.assertNotIn("Mod Politicos", NATURALIZE_PROMPT)
        self.assertLessEqual(len(POLITE_PROMPT_VERSION), 30)  # Fits the prompt_version columns.

    def test_correct_english_is_never_rewritten(self):
        text = "I've lived here for five years."
        outcome = self.naturalize(text, raw(corrected_text="I have resided here for five years."))
        self.assertEqual((outcome.result.corrected_text, outcome.result.has_errors, outcome.result.corrections),
                         (text, False, []))

    def test_correct_but_unnatural_english_gets_the_natural_version_without_fake_errors(self):
        text = "I want to ask you if you can help me with a thing."
        outcome = self.naturalize(text, raw(corrected_text=text, natural_text="Could you help me with something?",
                                            natural_explanation="Sună mai direct și mai natural."))
        result = outcome.result
        self.assertEqual((result.has_errors, result.corrections, result.corrected_text), (False, [], text))
        self.assertEqual((result.native_text, result.native_explanation),
                         ("Could you help me with something?", "Sună mai direct și mai natural."))

    def test_already_natural_english_gets_no_alternative(self):
        text = "I'll give you a call when I get home."
        for natural in ("", "I'll give you a call when I get home", "i'll give you a call when i get home!"):
            with self.subTest(natural=natural):
                outcome = self.naturalize(text, raw(corrected_text=text, natural_text=natural,
                                                    natural_explanation="Deja natural." if natural else ""))
                self.assertEqual((outcome.result.native_text, outcome.result.native_explanation), ("", ""))
                self.assertFalse(outcome.result.has_errors)

    def test_british_preference_is_a_suggestion_not_an_error(self):
        text = "I like this color."
        outcome = self.naturalize(text, raw(corrected_text=text, has_errors=True, natural_text="I like this colour.",
                                            corrections=[correction("color", "colour", "british_english")]))
        result = outcome.result
        self.assertFalse(result.has_errors)
        self.assertEqual((result.corrections[0].is_british_english_preference, result.corrections[0].severity),
                         (True, "suggestion"))
        self.assertEqual(result.corrected_text, text)

    def test_valid_american_english_is_kept_and_the_natural_version_is_british(self):
        text = "We parked the truck near the parking lot."
        outcome = self.naturalize(text, raw(corrected_text=text, natural_text="We parked the lorry near the car park."))
        self.assertEqual((outcome.result.corrected_text, outcome.result.has_errors), (text, False))
        self.assertEqual(outcome.result.native_text, "We parked the lorry near the car park.")

    def test_capitalisation_and_punctuation_only_are_fixed_silently(self):
        text = "i see you tomorrow"
        outcome = self.naturalize(text, raw(corrected_text="I see you tomorrow.", has_errors=True,
                                            corrections=[correction("i", "I", "spelling")],
                                            overall_explanation="Am adăugat majuscula."))
        result = outcome.result
        self.assertEqual((result.corrected_text, result.has_errors, result.corrections, result.overall_explanation),
                         ("I see you tomorrow.", False, [], ""))

    def test_a_tense_that_contradicts_the_time_is_a_genuine_error(self):
        text = "I go to the dentist yesterday."
        outcome = self.naturalize(text, raw(
            corrected_text="I went to the dentist yesterday.", has_errors=True,
            corrections=[correction("go", "went", "verb_tense",
                                    explanation="Yesterday cere trecutul; poți schimba și cuvântul de timp.")]))
        self.assertTrue(outcome.result.has_errors)
        self.assertEqual(outcome.result.corrections[0].category, "verb_tense")

    def test_english_with_a_few_romanian_words_is_corrected_as_english(self):
        text = "I bought a bilet for the train to London."
        outcome = self.naturalize(text, raw(corrected_text="I bought a ticket for the train to London.", has_errors=True,
                                            corrections=[correction("bilet", "ticket", "vocabulary")]))
        self.assertEqual((outcome.operation, outcome.result.corrections[0].original), ("correction", "bilet"))

    def test_maximum_input_is_one_call_with_a_bounded_output_budget(self):
        text = ("I didn't went to work. " * 100)[:2000]
        outcome = self.naturalize(text, raw(corrected_text=text.replace("didn't went", "didn't go"), has_errors=True,
                                            corrections=[correction("didn't went", "didn't go")]))
        self.assertTrue(outcome.result.has_errors)
        self.assertEqual(self.api.responses.parse.call_args.kwargs["max_output_tokens"], 7000)
        self.assertEqual(output_token_budget("Hi"), 3004)
        self.assertEqual(output_token_budget("x" * 10000), 8000)

    def test_empty_or_too_long_input_never_reaches_the_provider(self):
        for text in ("", "   ", "x" * 2001):
            with self.subTest(length=len(text)), self.assertRaises(AssistantError):
                NaturalizeService().naturalize(text)
        self.api.responses.parse.assert_not_called()
        self.sdk.assert_not_called()

    def test_malformed_provider_responses_are_refused(self):
        refusal = [SimpleNamespace(content=[SimpleNamespace(type="refusal")])]
        for overrides, code in ((dict(status="incomplete"), "incomplete"), (dict(output_parsed=None), "missing_output"),
                                (dict(output=refusal), "refused")):
            with self.subTest(code=code):
                self.respond(raw(corrected_text="Hello there."), **overrides)
                with self.assertRaises(AssistantError) as raised:
                    NaturalizeService().naturalize("Hello there.")
                self.assertEqual(raised.exception.code, code)
        with self.assertRaises(ValidationError):
            NaturalizeResult.model_validate({**raw().model_dump(), "source_language": "fr"})
        with self.assertRaises(ValidationError):
            NaturalizeResult.model_validate({**raw().model_dump(), "original_text": "an echo"})
        # An inconsistent error flag is normalised from the corrections instead of losing the billed response.
        outcome = self.naturalize("She can sings.", raw(corrected_text="She can sing.", has_errors=False,
                                                        corrections=[correction("can sings", "can sing")]))
        self.assertTrue(outcome.result.has_errors)

    def test_invented_or_empty_corrections_are_refused_with_their_language(self):
        for output, code in ((raw(corrected_text="She can sing.", has_errors=True,
                                  corrections=[correction("not in the input", "x")]), "invalid_snippet"),
                             (raw(corrected_text="She can sings.", has_errors=True,
                                  corrections=[correction("can sings", "can sing")]), "invalid_correction")):
            with self.subTest(code=code):
                error = self.refused("She can sings.", output)
                self.assertEqual((error.code, error.source_language), (code, "en"))

    def test_provider_errors_are_sanitised(self):
        request = httpx.Request("POST", "https://api.openai.com/v1/responses")
        for exception, code in ((APIConnectionError(request=request), "connection_error"),
                                (APITimeoutError(request=request), "timeout")):
            with self.subTest(code=code):
                self.api.responses.parse.side_effect = exception
                with self.assertRaises(AssistantError) as raised:
                    NaturalizeService().naturalize("Hello there.")
                self.assertEqual(raised.exception.code, code)
                self.assertNotIn("api.openai.com", raised.exception.message)

    @override_settings(OPENAI_API_KEY="")
    def test_missing_configuration(self):
        with self.assertRaises(AssistantError) as raised:
            NaturalizeService().naturalize("Hello.")
        self.assertEqual(raised.exception.code, "not_configured")
        self.sdk.assert_not_called()

    @override_settings(OPENAI_REASONING_EFFORT="low")
    def test_reasoning_effort_is_sent_only_when_configured(self):
        self.naturalize(CORRECTION_CASES[0][0], english_raw())
        self.assertEqual(self.api.responses.parse.call_args.kwargs["reasoning"], {"effort": "low"})


class NaturalizeRomanianTests(ProviderMock, SimpleTestCase):
    def test_romanian_goes_straight_to_british_english(self):
        for case in ROMANIAN_CASES:
            with self.subTest(text=case[0]):
                self.api.responses.parse.reset_mock()
                self.respond(romanian_raw(case))
                outcome = NaturalizeService().naturalize(case[0])
                self.assertEqual((outcome.operation, outcome.source_language), ("translation", "ro"))
                self.assertIsInstance(outcome.result, TranslationResult)
                self.assertEqual(outcome.result.model_dump(), {"source_language": "ro", "target_language": "en",
                                                               "original_text": case[0], "translated_text": case[1]})
                self.api.responses.parse.assert_called_once()

    def test_romanian_never_produces_grammar_corrections(self):
        text = ROMANIAN_CASES[0][0]
        self.respond(raw(source_language="ro", has_errors=True, corrected_text="Nu cred...", overall_explanation="Explicație",
                         corrections=[correction("cred", "cred")], natural_text=ROMANIAN_CASES[0][1]))
        outcome = NaturalizeService().naturalize(text)
        self.assertEqual(set(outcome.result.model_dump()), {"source_language", "target_language", "original_text",
                                                            "translated_text"})

    def test_romanian_left_untranslated_is_refused(self):
        text = ROMANIAN_CASES[4][0]
        for natural in ("", "  ", "mersi mult"):
            with self.subTest(natural=natural):
                self.respond(raw(source_language="ro", natural_text=natural))
                with self.assertRaises(AssistantError) as raised:
                    NaturalizeService().naturalize(text)
                self.assertEqual((raised.exception.code, raised.exception.source_language), ("invalid_translation", "ro"))

    def test_other_and_ambiguous_languages_are_refused_after_the_single_call(self):
        for code in ("other", "ambiguous"):
            with self.subTest(code=code):
                self.respond(raw(source_language=code))
                with self.assertRaises(AssistantError) as raised:
                    NaturalizeService().naturalize("Bonjour, comment ça va ?")
                self.assertEqual((raised.exception.code, raised.exception.message, raised.exception.source_language),
                                 ("language", unsupported_message(), code))


@override_settings(CONTENT_MODERATION_ENABLED=True)
class SingleGenerativeCallTests(ProviderMock, SimpleTestCase):
    def test_english_and_romanian_each_make_exactly_one_generative_call(self):
        for text, output, operation in ((CORRECTION_CASES[0][0], english_raw(), "correction"),
                                         (ROMANIAN_CASES[0][0], romanian_raw(), "translation")):
            with self.subTest(operation=operation):
                self.api.reset_mock()
                self.api.moderations.create.return_value = moderation()
                self.respond(output)
                outcome = NaturalizeService().naturalize(text)
                self.assertEqual(outcome.operation, operation)
                self.assertEqual(self.api.responses.parse.call_count, 1)
                self.assertEqual(self.api.moderations.create.call_count, 1)  # Independent, alongside the model call.
                # No language-detection or other generative call happens first: these are the only provider calls.
                self.assertEqual({name for name, _, _ in self.api.method_calls}, {"responses.parse", "moderations.create"})


class PoliteRomanianTests(ProviderMock, SimpleTestCase):
    def test_polite_romanian_is_one_call_with_the_polite_prompt_and_stays_a_translation(self):
        text = "Mă duci și pe mine acasă?"
        self.respond(raw(source_language="ro", has_errors=True, corrected_text="Mă duci acasă?",
                         corrections=[correction("duci", "duci")], natural_text="Would you mind giving me a lift home?",
                         natural_explanation="Sună politicos."))
        outcome = NaturalizeService().naturalize(text, polite=True)
        self.api.responses.parse.assert_called_once()
        kwargs = self.api.responses.parse.call_args.kwargs
        self.assertEqual(kwargs["input"], [{"role": "system", "content": NATURALIZE_POLITE_PROMPT},
                                           {"role": "user", "content": text}])
        self.assertEqual(kwargs["prompt_cache_key"], POLITE_PROMPT_CACHE_KEY)
        self.assertEqual((outcome.operation, outcome.source_language, outcome.polite), ("translation", "ro", True))
        self.assertIsInstance(outcome.result, TranslationResult)  # No grammar corrections for Romanian.
        self.assertEqual(outcome.result.translated_text, "Would you mind giving me a lift home?")


class PromptContractTests(SimpleTestCase):
    """The prompt text is checked for its contract only; live language quality is measured by eval_naturalize."""

    def test_version_and_cache_keys_move_together(self):
        self.assertEqual(PROMPT_VERSION, "2026-09-v8-naturalize")
        self.assertEqual(POLITE_PROMPT_VERSION, f"{PROMPT_VERSION}+polite")
        self.assertEqual(PROMPT_CACHE_KEY, f"corect:naturalize:{PROMPT_VERSION}")
        self.assertEqual(POLITE_PROMPT_CACHE_KEY, f"corect:naturalize-polite:{PROMPT_VERSION}")

    def test_romanian_rules_are_intent_first_faithful_and_register_aware(self):
        rules = flat(LANGUAGE_RULES["ro"])
        self.assertIn("TRANSLATE THE INTENT, NOT THE WORDS.", rules)
        self.assertIn("A grammatical literal translation is NOT good enough", rules)
        self.assertIn("You may reorder, change the construction, split or combine sentences", rules)
        self.assertIn("never invent a situation to reach an idiom", rules)
        self.assertIn("Natural is not extra polite: a plain direct request stays direct.", rules)
        self.assertIn("contemporary British English", rules)
        self.assertIn("Romanian without diacritics", rules)
        self.assertIn("corrected_text empty, has_errors false, corrections empty", rules)
        # Ordered: intent, then British wording, then the limits of fidelity, then register.
        steps = [rules.index(marker) for marker in ("1. Intent", "2. British wording", "3. Fidelity", "4. Register")]
        self.assertEqual(steps, sorted(steps))
        # The original problem sentence and its counterexample are both taught.
        self.assertIn("Vrei să mergi cu mine cu mașina diseară, la 5? => Would you like a lift at five this evening?",
                      rules)
        self.assertIn("Vrei să vii cu mine diseară la 5? => Do you want to come with me", rules)

    def test_english_rules_keep_minimal_correction(self):
        rules = flat(LANGUAGE_RULES["en"])
        self.assertIn("correct English MUST remain unchanged in corrected_text", rules)
        self.assertIn("natural_text MUST be an empty string when corrected_text already sounds natural", rules)
        self.assertNotIn("TRANSLATE THE INTENT", rules)

    def test_polite_rules_build_on_intent_first_romanian(self):
        self.assertEqual(NATURALIZE_POLITE_PROMPT, NATURALIZE_PROMPT + POLITE_RULES)
        self.assertLess(NATURALIZE_POLITE_PROMPT.index("TRANSLATE THE INTENT"),
                        NATURALIZE_POLITE_PROMPT.index("## Mod Politicos is ON"))
        self.assertIn("For ro: first follow the Romanian rules above (intent first", flat(POLITE_RULES))
        self.assertIn("never fall back to a literal translation", flat(POLITE_RULES))
        self.assertIn("Trimite-mi adresa. => Send me the address.", NATURALIZE_PROMPT)  # Normal mode stays direct.
        self.assertIn("Trimite-mi adresa. => Could you send me the address, please?", POLITE_RULES)

    def test_runtime_code_has_no_phrase_substitution(self):
        """Idioms come from the model reading the context, never from Python rules keyed on Romanian words."""
        offenders = []
        for path in (Path(settings.BASE_DIR) / "apps" / "assistant").rglob("*.py"):
            if {"tests", "evals", "migrations"} & set(path.parts) or path.name == "prompts.py":
                continue
            source = path.read_text(encoding="utf-8").lower()
            if any(word in source for word in ("mașin", "masin", "lift", "pick up", "pop round")):
                offenders.append(path.name)
        self.assertEqual(offenders, [])
