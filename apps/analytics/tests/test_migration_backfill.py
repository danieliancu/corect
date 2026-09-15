from importlib import import_module

from django.apps import apps as django_apps
from django.contrib.auth.models import User
from django.test import TestCase

from apps.analytics.models import AudioUsageEvent, UsageEvent
from apps.assistant.models import AssistantRequest

backfill = import_module("apps.analytics.migrations.0008_effective_operation_backfill")


class EffectiveOperationBackfillTests(TestCase):
    def test_earlier_rows_get_their_effective_operation_and_language_without_touching_history(self):
        user = User.objects.create_user("ana", password="test-password")
        romanian = AssistantRequest.objects.create(user=user, request_type="translation", detected_language="ro",
                                                   model_used="m", prompt_version="2026-09-v6", status="success",
                                                   result_data={"translated_text": "Hello"})
        english = AssistantRequest.objects.create(user=user, request_type="correction", detected_language="en",
                                                  model_used="m", prompt_version="2026-09-v6", status="success",
                                                  result_data={"corrected_text": "Hi"})
        auto_translated = UsageEvent.objects.create(audience="registered", user=user, request_type="correction",
                                                    auto_translated=True, status="success", assistant_request=romanian)
        corrected = UsageEvent.objects.create(audience="registered", user=user, request_type="correction",
                                              status="success", assistant_request=english)
        anonymous = UsageEvent.objects.create(audience="anonymous", request_type="translation", status="success")
        backfill.forward(django_apps, None)
        for event, expected in ((auto_translated, ("translation", "ro")), (corrected, ("correction", "en")),
                                (anonymous, ("translation", ""))):
            with self.subTest(event=event.pk):
                event.refresh_from_db()
                self.assertEqual((event.request_type, event.source_language), expected)
        self.assertEqual(AssistantRequest.objects.get(pk=romanian.pk).result_data, {"translated_text": "Hello"})
        backfill.backward(django_apps, None)
        auto_translated.refresh_from_db()
        self.assertEqual((auto_translated.request_type, auto_translated.source_language), ("correction", ""))

    def test_plan_backfill_marks_only_rows_that_can_only_have_been_anonymous(self):
        plans = import_module("apps.analytics.migrations.0009_plan_tier")
        anonymous = UsageEvent.objects.create(audience="anonymous", request_type="correction", status="success")
        registered = UsageEvent.objects.create(audience="registered", request_type="correction", status="success")
        audio = AudioUsageEvent.objects.create(operation="speech", audience="anonymous", status="success")
        plans.forward(django_apps, None)
        for row, expected in ((anonymous, "anonymous"), (registered, ""), (audio, "anonymous")):
            row.refresh_from_db()
            self.assertEqual(row.plan, expected)  # A registered row's historical Free or Pro tier is not guessed.
        plans.backward(django_apps, None)
