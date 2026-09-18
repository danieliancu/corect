"""Scoring rules of the live eval, and the guards that keep live commands from running by accident."""
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from apps.assistant.evals.dataset import EvalCase
from apps.assistant.evals.scoring import contains, normalise, score_case, summarise
from apps.assistant.services.naturalize import Naturalized
from apps.assistant.services.openai_client import AssistantError
from apps.assistant.services.usage import ProviderUsage
from .examples import correction_result, translation_result
from .provider import ProviderMock


def case(group="en_romanian_transfer", text="I didn't went to work yesterday.", **expect):
    return EvalCase(id="case-1", group=group, input=text, expect=expect)


def usage(output_tokens, duration_ms=900):
    return ProviderUsage(model="m", response_model="m", input_tokens=1000, cached_input_tokens=800,
                         output_tokens=output_tokens, reasoning_tokens=0, total_tokens=1000 + output_tokens,
                         duration_ms=duration_ms)


class ScoringTests(SimpleTestCase):
    def english(self, **changes):
        result = correction_result()
        for name, value in changes.items():
            setattr(result, name, value)
        return Naturalized("correction", "en", result)

    def test_a_good_english_answer_passes(self):
        score = score_case(case(source_language="en", operation="correction", has_errors=True, categories_any=["verb_form"],
                                output_includes_any=[["didn't go", "did not go"]], output_excludes=["didn’t went"],
                                min_corrections=1, max_corrections=2), self.english())
        self.assertTrue(score.passed, score.failures)

    def test_routing_false_errors_missing_phrases_and_paraphrases_are_reported(self):
        score = score_case(case(source_language="ro", has_errors=False, natural="forbidden",
                                output_includes_any=[["went to work"]], output_excludes=["didn't go"]),
                           self.english(native_text="I didn't make it in yesterday."))
        joined = " | ".join(score.failures)
        for expected in ("routing", "has_errors", "paraphrased", "output lacks", "output keeps"):
            self.assertIn(expected, joined)
        self.assertFalse(score.passed)

    def test_natural_version_rules_and_correction_counts(self):
        required = score_case(case(natural="required", max_corrections=0), self.english())
        self.assertEqual(required.failures, ["natural version missing", "1 corrections (expected at most 0)"])
        american = score_case(case(corrected_includes_any=[["truck"]]), self.english())
        self.assertIn("corrected text lacks", american.failures[0])

    def test_translations_and_expected_errors(self):
        good = score_case(case(group="ro_to_en", source_language="ro", operation="translation",
                               output_includes_any=[["sorry"]], output_excludes=["i am sorry that"]),
                          Naturalized("translation", "ro", translation_result()))
        self.assertTrue(good.passed, good.failures)
        error = AssistantError("language")
        error.source_language = "other"
        self.assertTrue(score_case(case(group="unsupported", error_code="language"), error=error).passed)
        self.assertEqual(score_case(case(), error=error).failures, ["error language"])
        self.assertFalse(score_case(case(group="unsupported", error_code="language"), self.english()).passed)

    def test_invented_meaning_is_reported_apart_from_literal_wording(self):
        def romanian(text):
            return Naturalized("translation", "ro", translation_result().model_copy(update={"translated_text": text}))

        expect = dict(group="ro_intent_first", source_language="ro", operation="translation",
                      output_includes_any=[["come"]], output_excludes=["come with me in"], must_not_invent=["lift", "car"])
        good = score_case(case(**expect), romanian("Do you want to come with me at five this evening?"))
        self.assertTrue(good.passed, good.failures)
        invented = score_case(case(**expect), romanian("Do you want to come? I can give you a lift in my car."))
        self.assertEqual(invented.failures, ["invented 'lift'", "invented 'car'"])
        literal = score_case(case(**expect), romanian("Do you want to come with me in the evening?"))
        self.assertEqual(literal.failures, ["output keeps 'come with me in'"])
        summary = summarise([{"case": case(**expect), "score": score, "duration_ms": 900, "calls": [], "budget": 3000}
                             for score in (good, invented, literal)])
        self.assertEqual(summary["invention_failures"], 1)
        self.assertEqual(summary["failure_reasons"], {"invented": 2, "output": 1})

    def test_normalisation_ignores_case_punctuation_and_apostrophe_style(self):
        self.assertEqual(normalise("  I’M   Here, OK!\n"), "i'm here ok")
        self.assertTrue(contains(normalise("Come with me to the doctor’s."), "doctor"))  # Possessive 's.
        self.assertTrue(contains(normalise("The report's due."), "the report"))
        self.assertFalse(contains(normalise("I'm carrying it."), "car"))  # Still whole words only.
        self.assertFalse(contains(normalise("The doctors are busy."), "doctor"))

    def test_summary_metrics(self):
        rows = []
        for group, has_errors, natural, expect in (
                ("en_already_natural", False, "", dict(has_errors=False, natural="forbidden")),
                ("en_already_natural", True, "Something else.", dict(has_errors=False, natural="forbidden")),
                ("en_romanian_transfer", True, "", dict(source_language="en", has_errors=True))):
            outcome = self.english(has_errors=has_errors, native_text=natural)
            item = case(group=group, **expect)
            rows.append({"case": item, "score": score_case(item, outcome), "duration_ms": 1200,
                         "calls": [usage(600)], "budget": 3000})
        summary = summarise(rows)
        self.assertEqual((summary["cases"], summary["passed"]), (3, 2))
        self.assertEqual((summary["routing_accuracy"], summary["false_error_rate"], summary["paraphrase_rate"]),
                         (1.0, 0.5, 0.5))
        self.assertEqual((summary["provider_calls"], summary["max_output_budget_ratio"]), (3, 0.2))
        self.assertEqual(summary["latency_ms"]["p50"], 1200)


class LiveCommandGuardTests(ProviderMock, SimpleTestCase):
    def test_live_commands_refuse_without_live_or_with_the_test_model(self):
        for command in ("eval_naturalize", "benchmark_text_latency", "benchmark_voice_latency"):
            with self.subTest(command=command):
                with self.assertRaisesMessage(CommandError, "--live"):
                    call_command(command)
                with self.assertRaisesMessage(CommandError, "OPENAI_MODEL"):
                    call_command(command, "--live")  # The test settings use "test-model".
        self.sdk.assert_not_called()
