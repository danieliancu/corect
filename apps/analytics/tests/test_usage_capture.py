from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.assistant.schemas import CorrectionResult
from apps.assistant.services.correction import CorrectionService
from apps.assistant.services.openai_client import AssistantError, parse_response
from apps.assistant.services.usage import ParsedResponse, collect_provider_usage
from apps.assistant.tests.examples import CORRECTION_CASES, correction_result

USAGE = SimpleNamespace(input_tokens=1389, input_tokens_details=SimpleNamespace(cached_tokens=1386), output_tokens=2341,
                        output_tokens_details=SimpleNamespace(reasoning_tokens=967), total_tokens=3730)


class UsageCaptureTests(SimpleTestCase):
    def setUp(self):
        self.sdk = patch("apps.assistant.services.openai_client.OpenAI").start()
        self.addCleanup(patch.stopall)
        self.client = self.sdk.return_value.__enter__.return_value

    def respond(self, **overrides):
        values = dict(status="completed", output=[], output_parsed=correction_result(), usage=USAGE,
                      model="test-model-2026-09-01")
        values.update(overrides)
        self.client.responses.parse.return_value = SimpleNamespace(**values)

    def test_parse_response_returns_output_and_real_usage(self):
        self.respond()
        with collect_provider_usage() as calls:
            parsed = parse_response("prompt", CORRECTION_CASES[0][0], CorrectionResult)
        self.assertIsInstance(parsed, ParsedResponse)
        self.assertIsInstance(parsed.output, CorrectionResult)
        usage = parsed.usage
        self.assertEqual((usage.input_tokens, usage.cached_input_tokens, usage.output_tokens, usage.reasoning_tokens,
                          usage.total_tokens), (1389, 1386, 2341, 967, 3730))
        self.assertEqual((usage.model, usage.response_model), ("test-model", "test-model-2026-09-01"))
        self.assertEqual(calls, [usage])
        kwargs = self.client.responses.parse.call_args.kwargs
        self.assertFalse(kwargs["store"])
        self.assertIs(kwargs["text_format"], CorrectionResult)
        self.assertEqual(self.sdk.call_args.kwargs["max_retries"], 0)

    def test_billed_usage_is_reported_even_when_the_response_is_rejected(self):
        refusal = [SimpleNamespace(content=[SimpleNamespace(type="refusal")])]
        for case, overrides in (("incomplete", dict(status="incomplete")), ("missing", dict(output_parsed=None)),
                                ("refusal", dict(output=refusal))):
            with self.subTest(case=case):
                self.respond(**overrides)
                with collect_provider_usage() as calls, self.assertRaises(AssistantError):
                    parse_response("prompt", "text", CorrectionResult)
                self.assertEqual([call.total_tokens for call in calls], [3730])

    def test_services_keep_their_return_types_and_tolerate_missing_usage(self):
        self.respond(usage=None)
        with collect_provider_usage() as calls:
            result = CorrectionService().correct(CORRECTION_CASES[0][0])
        self.assertIsInstance(result, CorrectionResult)
        self.assertEqual(calls, [])

    def test_usage_outside_a_collector_is_ignored(self):
        self.respond()
        self.assertIsInstance(CorrectionService().correct(CORRECTION_CASES[0][0]), CorrectionResult)
