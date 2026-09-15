from django.test import SimpleTestCase

from apps.assistant.schemas import CorrectionResult, NaturalizeResult
from apps.assistant.services.naturalize import NaturalizeService, output_token_budget
from apps.assistant.services.openai_client import AssistantError, parse_response
from apps.assistant.services.usage import ParsedResponse, collect_provider_usage
from apps.assistant.tests.examples import CORRECTION_CASES, correction_result, english_raw
from apps.assistant.tests.provider import USAGE, ProviderMock
from types import SimpleNamespace


class UsageCaptureTests(ProviderMock, SimpleTestCase):
    def test_parse_response_returns_output_real_usage_and_duration(self):
        self.respond(correction_result(), usage=USAGE, model="test-model-2026-09-01")
        with collect_provider_usage() as calls:
            parsed = parse_response("prompt", CORRECTION_CASES[0][0], CorrectionResult)
        self.assertIsInstance(parsed, ParsedResponse)
        self.assertIsInstance(parsed.output, CorrectionResult)
        usage = parsed.usage
        self.assertEqual((usage.input_tokens, usage.cached_input_tokens, usage.output_tokens, usage.reasoning_tokens,
                          usage.total_tokens), (1389, 1386, 2341, 967, 3730))
        self.assertEqual((usage.model, usage.response_model), ("test-model", "test-model-2026-09-01"))
        self.assertIsInstance(usage.duration_ms, int)
        self.assertGreaterEqual(usage.duration_ms, 0)
        self.assertEqual(calls, [usage])
        kwargs = self.api.responses.parse.call_args.kwargs
        self.assertFalse(kwargs["store"])
        self.assertIs(kwargs["text_format"], CorrectionResult)
        self.assertEqual(self.sdk.call_args.kwargs["max_retries"], 0)

    def test_billed_usage_is_reported_even_when_the_response_is_rejected(self):
        refusal = [SimpleNamespace(content=[SimpleNamespace(type="refusal")])]
        for case, overrides in (("incomplete", dict(status="incomplete")), ("missing", dict(output_parsed=None)),
                                ("refusal", dict(output=refusal))):
            with self.subTest(case=case):
                self.respond(correction_result(), usage=USAGE, **overrides)
                with collect_provider_usage() as calls, self.assertRaises(AssistantError):
                    parse_response("prompt", "text", CorrectionResult)
                self.assertEqual([call.total_tokens for call in calls], [3730])

    def test_only_known_request_options_reach_the_provider(self):
        self.respond(correction_result())
        parse_response("prompt", "text", CorrectionResult, prompt_cache_key="corect:test", reasoning={"effort": "low"})
        kwargs = self.api.responses.parse.call_args.kwargs
        self.assertEqual((kwargs["prompt_cache_key"], kwargs["reasoning"]), ("corect:test", {"effort": "low"}))
        with self.assertRaises(TypeError):
            parse_response("prompt", "text", CorrectionResult, temperature=2)

    def test_the_service_tolerates_missing_usage_and_calls_outside_a_collector(self):
        self.respond(english_raw(), usage=None)
        with collect_provider_usage() as calls:
            outcome = NaturalizeService().naturalize(CORRECTION_CASES[0][0])
        self.assertIsInstance(outcome.result, CorrectionResult)
        self.assertEqual(calls, [])
        self.respond(english_raw(), usage=USAGE)
        self.assertIsInstance(NaturalizeService().naturalize(CORRECTION_CASES[0][0]).result, CorrectionResult)
        self.assertIs(self.api.responses.parse.call_args.kwargs["text_format"], NaturalizeResult)

    def test_output_budget_grows_with_input_within_fixed_bounds(self):
        budgets = [output_token_budget("x" * length) for length in (0, 100, 500, 1000, 1500, 2000, 5000)]
        self.assertEqual(budgets, sorted(budgets))
        self.assertEqual((budgets[0], budgets[5], budgets[-1]), (3000, 7000, 8000))
