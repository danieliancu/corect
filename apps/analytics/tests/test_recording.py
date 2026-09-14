from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.db import DatabaseError
from django.test import TestCase, override_settings

from apps.analytics.models import AnonymousVisitor, UsageEvent
from apps.analytics.services.visitors import VISITOR_COOKIE
from apps.core.consent import CONSENT_COOKIE, consent_cookie_value
from apps.assistant.models import AssistantRequest
from apps.assistant.services.openai_client import AssistantError
from apps.assistant.services.pricing import parse_pricing
from apps.assistant.services.usage import ProviderUsage, report_usage
from apps.assistant.tests.examples import correction_result, translation_result

PRICING = parse_pricing({"test-model": {"input_per_1m": "0.20", "cached_input_per_1m": "0.02", "output_per_1m": "1.20"}})


def provider_usage(input_tokens=1200, cached=1000, output=300, reasoning=50):
    return ProviderUsage(model="test-model", response_model="test-model-2026", input_tokens=input_tokens,
                         cached_input_tokens=cached, output_tokens=output, reasoning_tokens=reasoning,
                         total_tokens=input_tokens + output)


def replying(result, *usages):
    """A service stand-in that reports provider usage the way parse_response does, then returns the result."""
    def reply(text):
        for usage in usages:
            report_usage(usage)
        return result
    return reply


@override_settings(OPENAI_PRICING=PRICING)
class UsageRecordingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ana", "ana@example.com", "test-password")
        self.correct = patch("apps.assistant.views.CorrectionService.correct").start()
        self.translate = patch("apps.assistant.views.TranslationService.translate").start()
        self.addCleanup(patch.stopall)

    def post(self, path="/assistant/correct/", text=None, token=None):
        return self.client.post(path, {"text": text or correction_result().original_text,
                                       "submission_token": token or uuid4()})

    def test_registered_correction_and_translation_record_tokens_cost_and_history_link(self):
        self.client.force_login(self.user)
        self.correct.side_effect = replying(correction_result(), provider_usage())
        self.translate.side_effect = replying(translation_result(), provider_usage(400, 0, 60, 0))
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.post("/assistant/translate/", translation_result().original_text).status_code, 200)
        correction, translation = UsageEvent.objects.order_by("pk")
        self.assertEqual((correction.audience, correction.user, correction.request_type, correction.status),
                         ("registered", self.user, "correction", "success"))
        self.assertEqual((correction.input_tokens, correction.cached_input_tokens, correction.output_tokens,
                          correction.reasoning_tokens, correction.total_tokens), (1200, 1000, 300, 50, 1500))
        # (200 uncached × 0.20 + 1000 cached × 0.02 + 300 output × 1.20) per million tokens
        self.assertEqual(correction.estimated_cost, Decimal("0.00042000"))
        self.assertEqual((correction.model, correction.response_model, correction.provider_calls),
                         ("test-model", "test-model-2026", 1))
        self.assertEqual(correction.assistant_request, AssistantRequest.objects.get(request_type="correction"))
        self.assertIsNone(correction.visitor)
        self.assertEqual((translation.request_type, translation.estimated_cost, translation.assistant_request.request_type),
                         ("translation", Decimal("0.00015200"), "translation"))

    def test_anonymous_correction_and_translation_are_counted_without_history(self):
        self.client.cookies[CONSENT_COOKIE] = consent_cookie_value(True)  # Analytics allowed: events carry the visitor.
        self.correct.side_effect = replying(correction_result(), provider_usage())
        self.translate.side_effect = replying(translation_result(), provider_usage(400, 0, 60, 0))
        self.post()
        self.post("/assistant/translate/", translation_result().original_text)
        events = list(UsageEvent.objects.order_by("pk"))
        self.assertEqual([(e.audience, e.request_type, e.status, e.user_id) for e in events],
                         [("anonymous", "correction", "success", None), ("anonymous", "translation", "success", None)])
        self.assertEqual({e.visitor_id for e in events}, {AnonymousVisitor.objects.get().pk})
        self.assertEqual(sum(e.total_tokens for e in events), 1960)
        self.assertFalse(AssistantRequest.objects.exists())

    def test_failed_request_keeps_billed_tokens_and_sanitised_code(self):
        def fail(text):
            report_usage(provider_usage())
            raise AssistantError("invalid_original")
        self.correct.side_effect = fail
        self.client.force_login(self.user)
        self.assertEqual(self.post().status_code, 503)
        event = UsageEvent.objects.get()
        self.assertEqual((event.status, event.error_code, event.total_tokens), ("failed", "invalid_original", 1500))
        self.assertEqual(event.assistant_request, AssistantRequest.objects.get(status="failed"))

    def test_timeout_without_provider_usage_leaves_tokens_and_cost_unknown(self):
        self.correct.side_effect = AssistantError("timeout", "That took too long. Please try again.")
        self.post()
        event = UsageEvent.objects.get()
        self.assertEqual((event.status, event.error_code, event.provider_calls), ("failed", "timeout", 0))
        self.assertIsNone(event.total_tokens)
        self.assertIsNone(event.estimated_cost)

    def test_rejected_submissions_consume_nothing(self):
        self.correct.side_effect = replying(correction_result(), provider_usage())
        token = uuid4()
        self.post(token=token)
        self.assertEqual(self.post(token=token).status_code, 409)
        with override_settings(RATE_LIMIT_MINUTE=1):
            self.assertEqual(self.post().status_code, 429)
        rejected = list(UsageEvent.objects.filter(status="rejected").order_by("pk"))
        self.assertEqual([event.error_code for event in rejected], ["duplicate", "rate_limit"])
        self.assertTrue(all(event.total_tokens == 0 and event.estimated_cost == 0 for event in rejected))
        self.correct.assert_called_once()

    def test_romanian_sent_to_correct_sums_both_provider_calls(self):
        def romanian(text):
            report_usage(provider_usage(1200, 1000, 100, 0))
            raise AssistantError("romanian_input", "Textul pare să fie în română.")
        self.correct.side_effect = romanian
        self.translate.side_effect = replying(translation_result(), provider_usage(400, 0, 60, 0))
        self.post(text=translation_result().original_text)
        event = UsageEvent.objects.get()
        self.assertEqual((event.request_type, event.auto_translated, event.provider_calls, event.status),
                         ("correction", True, 2, "success"))
        self.assertEqual((event.input_tokens, event.output_tokens, event.total_tokens), (1600, 160, 1760))
        # (200 × 0.20 + 1000 × 0.02 + 100 × 1.20) + (400 × 0.20 + 60 × 1.20), per million tokens
        self.assertEqual(event.estimated_cost, Decimal("0.00033200"))

    @override_settings(OPENAI_PRICING={})
    def test_unknown_pricing_keeps_tokens_and_leaves_cost_empty(self):
        self.correct.side_effect = replying(correction_result(), provider_usage())
        self.post()
        event = UsageEvent.objects.get()
        self.assertEqual(event.total_tokens, 1500)
        self.assertIsNone(event.estimated_cost)

    def test_invalid_form_is_not_recorded_and_sets_no_cookie(self):
        response = self.post(text="   ")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(UsageEvent.objects.exists())
        self.assertFalse(AnonymousVisitor.objects.exists())
        self.assertNotIn(VISITOR_COOKIE, response.cookies)

    def test_ledger_failure_never_breaks_the_response(self):
        self.correct.side_effect = replying(correction_result(), provider_usage())
        with patch("apps.analytics.services.recording.UsageEvent.objects.create", side_effect=DatabaseError):
            response = self.post()
        self.assertContains(response, "După did/didn")
