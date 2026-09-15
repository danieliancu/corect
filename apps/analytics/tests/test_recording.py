from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import Group, User
from django.core import serializers
from django.db import DatabaseError
from django.test import TestCase, override_settings

from apps.analytics.models import AnonymousVisitor, UsageEvent
from apps.analytics.services.visitors import VISITOR_COOKIE
from apps.core.consent import CONSENT_COOKIE, consent_cookie_value
from apps.assistant.models import AssistantRequest
from apps.assistant.services.openai_client import AssistantError
from apps.assistant.services.pricing import parse_pricing
from apps.assistant.services.usage import ProviderUsage, report_usage
from apps.assistant.tests.examples import correction_result, naturalized_english, naturalized_romanian, translation_result

PRICING = parse_pricing({"test-model": {"input_per_1m": "0.20", "cached_input_per_1m": "0.02", "output_per_1m": "1.20"}})


def provider_usage(input_tokens=1200, cached=1000, output=300, reasoning=50, duration_ms=None):
    return ProviderUsage(model="test-model", response_model="test-model-2026", input_tokens=input_tokens,
                         cached_input_tokens=cached, output_tokens=output, reasoning_tokens=reasoning,
                         total_tokens=input_tokens + output, duration_ms=duration_ms)


def replying(outcome, *usages):
    """A NaturalizeService stand-in that reports provider usage the way parse_response does, then returns the outcome."""
    def reply(text):
        for usage in usages:
            report_usage(usage)
        return outcome
    return reply


@override_settings(OPENAI_PRICING=PRICING)
class UsageRecordingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ana", "ana@example.com", "test-password")
        self.naturalize = patch("apps.assistant.views.NaturalizeService.naturalize").start()
        self.addCleanup(patch.stopall)

    def post(self, text=None, token=None):
        return self.client.post("/naturalize/", {"text": text or correction_result().original_text,
                                                 "submission_token": token or uuid4()})

    def test_registered_english_and_romanian_record_their_effective_operation_tokens_and_cost(self):
        self.client.force_login(self.user)
        self.naturalize.side_effect = replying(naturalized_english(), provider_usage())
        self.assertEqual(self.post().status_code, 200)
        self.naturalize.side_effect = replying(naturalized_romanian(), provider_usage(400, 0, 60, 0))
        self.assertEqual(self.post(translation_result().original_text).status_code, 200)
        english, romanian = UsageEvent.objects.order_by("pk")
        self.assertEqual((english.audience, english.user, english.request_type, english.source_language, english.status),
                         ("registered", self.user, "correction", "en", "success"))
        self.assertEqual((english.input_tokens, english.cached_input_tokens, english.output_tokens,
                          english.reasoning_tokens, english.total_tokens), (1200, 1000, 300, 50, 1500))
        # (200 uncached × 0.20 + 1000 cached × 0.02 + 300 output × 1.20) per million tokens
        self.assertEqual(english.estimated_cost, Decimal("0.00042000"))
        self.assertEqual((english.model, english.response_model, english.provider_calls),
                         ("test-model", "test-model-2026", 1))
        self.assertEqual(english.assistant_request, AssistantRequest.objects.get(request_type="correction"))
        self.assertIsNone(english.visitor)
        self.assertEqual((romanian.request_type, romanian.source_language, romanian.provider_calls, romanian.estimated_cost,
                          romanian.assistant_request.request_type),
                         ("translation", "ro", 1, Decimal("0.00015200"), "translation"))
        self.assertFalse(romanian.auto_translated)

    def test_anonymous_requests_are_counted_without_history(self):
        self.client.cookies[CONSENT_COOKIE] = consent_cookie_value(True)  # Analytics allowed: events carry the visitor.
        self.naturalize.side_effect = replying(naturalized_english(), provider_usage())
        self.post()
        self.naturalize.side_effect = replying(naturalized_romanian(), provider_usage(400, 0, 60, 0))
        self.post(translation_result().original_text)
        events = list(UsageEvent.objects.order_by("pk"))
        self.assertEqual([(e.audience, e.request_type, e.source_language, e.status, e.user_id) for e in events],
                         [("anonymous", "correction", "en", "success", None),
                          ("anonymous", "translation", "ro", "success", None)])
        self.assertEqual({e.visitor_id for e in events}, {AnonymousVisitor.objects.get().pk})
        self.assertEqual(sum(e.total_tokens for e in events), 1960)
        self.assertFalse(AssistantRequest.objects.exists())

    def test_durations_are_whole_milliseconds_and_the_ledger_holds_no_text(self):
        self.naturalize.side_effect = replying(naturalized_english(), provider_usage(duration_ms=812))
        self.post()
        event = UsageEvent.objects.get()
        self.assertEqual(event.provider_duration_ms, 812)
        self.assertIsInstance(event.duration_ms, int)
        self.assertGreaterEqual(event.duration_ms, 0)
        self.assertIsNone(event.moderation_duration_ms)  # Moderation is off in tests.
        stored = serializers.serialize("json", [event])
        for text in (correction_result().original_text, correction_result().corrected_text, "didn"):
            self.assertNotIn(text, stored)

    def test_failed_request_keeps_billed_tokens_its_operation_and_sanitised_code(self):
        def fail(text):
            report_usage(provider_usage())
            error = AssistantError("invalid_snippet")
            error.source_language = "en"
            raise error
        self.naturalize.side_effect = fail
        self.client.force_login(self.user)
        self.assertEqual(self.post().status_code, 503)
        event = UsageEvent.objects.get()
        self.assertEqual((event.status, event.error_code, event.total_tokens, event.request_type, event.source_language),
                         ("failed", "invalid_snippet", 1500, "correction", "en"))
        self.assertEqual(event.assistant_request, AssistantRequest.objects.get(status="failed"))

    def test_timeout_without_provider_usage_is_unclassified_with_unknown_tokens(self):
        self.naturalize.side_effect = AssistantError("timeout", "That took too long. Please try again.")
        self.post()
        event = UsageEvent.objects.get()
        self.assertEqual((event.status, event.error_code, event.provider_calls, event.request_type, event.source_language),
                         ("failed", "timeout", 0, "unclassified", ""))
        self.assertIsNone(event.total_tokens)
        self.assertIsNone(event.estimated_cost)
        self.assertIsNone(event.provider_duration_ms)

    def test_rejected_submissions_consume_nothing(self):
        self.naturalize.side_effect = replying(naturalized_english(), provider_usage())
        token = uuid4()
        self.post(token=token)
        self.assertEqual(self.post(token=token).status_code, 409)
        with override_settings(NATURALIZE_RATE_LIMIT_MINUTE=1):
            self.assertEqual(self.post().status_code, 429)
        with override_settings(NATURALIZE_DAILY_LIMITS={"anonymous": 1, "free": 1, "pro": 1}):
            self.assertEqual(self.post().status_code, 429)
        rejected = list(UsageEvent.objects.filter(status="rejected").order_by("pk"))
        self.assertEqual([(event.error_code, event.request_type) for event in rejected],
                         [("duplicate", "unclassified"), ("rate_limit", "unclassified"),
                          ("quota_exhausted", "unclassified")])
        self.assertTrue(all(event.total_tokens == 0 and event.estimated_cost == 0 for event in rejected))
        self.naturalize.assert_called_once()

    def test_the_plan_at_request_time_is_kept_after_an_upgrade(self):
        self.naturalize.side_effect = replying(naturalized_english(), provider_usage())
        self.post()
        self.client.force_login(self.user)
        self.post()
        self.user.groups.add(Group.objects.get_or_create(name="Pro")[0])
        self.post()
        self.assertEqual(list(UsageEvent.objects.order_by("pk").values_list("audience", "plan")),
                         [("anonymous", "anonymous"), ("registered", "free"), ("registered", "pro")])

    @override_settings(OPENAI_PRICING={})
    def test_unknown_pricing_keeps_tokens_and_leaves_cost_empty(self):
        self.naturalize.side_effect = replying(naturalized_english(), provider_usage())
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
        self.naturalize.side_effect = replying(naturalized_english(), provider_usage())
        with patch("apps.analytics.services.recording.UsageEvent.objects.create", side_effect=DatabaseError):
            response = self.post()
        self.assertContains(response, "După did/didn")
