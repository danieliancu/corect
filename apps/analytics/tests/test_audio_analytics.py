from datetime import timedelta
from decimal import Decimal
from importlib import import_module

from django.apps import apps as django_apps
from django.contrib.auth.models import User
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.analytics.formatting import format_money
from apps.analytics.models import AnonymousVisitor, AudioUsageEvent, UsageEvent
from apps.assistant.services.pricing import (AudioPrice, estimate_speech_cost, estimate_transcription_cost,
                                             parse_audio_pricing)
from apps.assistant.services.voice import AudioUsage

DASHBOARD, USERS, VISITORS = "/admin/analytics/", "/admin/analytics/users/", "/admin/analytics/visitors/"
AUDIO_PRICING = parse_audio_pricing({"gpt-transcribe": {"per_minute": "0.0045"},
                                     "gpt-4o-mini-tts": {"input_per_1m": "0.60", "output_per_1m": "12.00"}})


@override_settings(OPENAI_AUDIO_PRICING=AUDIO_PRICING)
class AudioPricingTests(SimpleTestCase):
    def test_transcription_cost_uses_provider_duration(self):
        usage = AudioUsage(model="gpt-transcribe", audio_seconds=Decimal("90.00"))
        self.assertEqual(estimate_transcription_cost(usage), Decimal("0.00675000"))  # 1.5 min × $0.0045

    def test_speech_cost_uses_input_and_audio_output_tokens(self):
        usage = AudioUsage(model="gpt-4o-mini-tts", voice="cedar", input_tokens=14, output_tokens=1200, total_tokens=1214)
        self.assertEqual(estimate_speech_cost(usage), Decimal("0.01440840"))  # (14 × 0.60 + 1200 × 12.00) ÷ 1M

    def test_unknown_pricing_or_usage_gives_no_cost(self):
        self.assertIsNone(estimate_transcription_cost(AudioUsage(model="gpt-transcribe")))
        self.assertIsNone(estimate_speech_cost(AudioUsage(model="gpt-4o-mini-tts", input_tokens=10)))
        self.assertIsNone(estimate_transcription_cost(AudioUsage(model="unpriced", audio_seconds=Decimal(5))))
        with self.settings(OPENAI_AUDIO_PRICING={}):
            self.assertIsNone(estimate_speech_cost(AudioUsage(model="gpt-4o-mini-tts", input_tokens=1, output_tokens=1)))
        token_billed = parse_audio_pricing({"gpt-transcribe": {"input_per_1m": "2.50", "output_per_1m": "10"}})
        with self.settings(OPENAI_AUDIO_PRICING=token_billed):
            usage = AudioUsage(model="gpt-transcribe", input_tokens=400, output_tokens=40, total_tokens=440)
            self.assertEqual(estimate_transcription_cost(usage), Decimal("0.00140000"))

    def test_costs_are_shown_in_pounds_at_the_configured_rate(self):
        with self.settings(ANALYTICS_GBP_PER_USD=Decimal("0.74")):
            self.assertEqual([format_money(value) for value in (Decimal("10"), Decimal("0.0043"), Decimal("0.00001"), 0, None)],
                             ["£7.40", "£0.0032", "£0.000007", "£0.00", "—"])
        with self.settings(ANALYTICS_GBP_PER_USD=Decimal("0.5")):
            self.assertEqual(format_money(Decimal("10")), "£5.00")

    def test_audio_pricing_configuration_is_validated(self):
        self.assertEqual(parse_audio_pricing({"m": {"per_minute": 0.006}})["m"], AudioPrice(per_minute=Decimal("0.006")))
        for bad in ([], {"m": {}}, {"m": {"per_minute": "free"}}, {"m": {"output_per_1m": "-1"}}, {"m": "0.006"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_audio_pricing(bad)


class AudioAnalyticsReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.staff = User.objects.create_user("staff", "staff@example.com", password="pw-staff-1234", is_staff=True)
        cls.learner = User.objects.create_user("learner", "learner@example.com", "pw-learner-1234")
        cls.voice_only = AnonymousVisitor.objects.create()
        UsageEvent.objects.create(audience="registered", user=cls.learner, request_type="correction", status="success",
                                  model="gpt-5.6-luna", total_tokens=1500, estimated_cost=Decimal("0.00100000"))

        def audio(**fields):
            return AudioUsageEvent.objects.create(**{"status": "success", **fields})
        audio(operation="transcription", audience="registered", user=cls.learner, model="gpt-transcribe",
              audio_seconds=Decimal("40.00"), estimated_cost=Decimal("0.00030000"))
        audio(operation="speech", audience="registered", user=cls.learner, model="gpt-4o-mini-tts", voice="cedar",
              speech_target="correction", input_tokens=12, output_tokens=160, total_tokens=172, estimated_cost=Decimal("0.00200000"))
        audio(operation="speech", audience="anonymous", visitor=cls.voice_only, model="gpt-4o-mini-tts", voice="cedar",
              speech_target="native", input_tokens=8, output_tokens=80, total_tokens=88, estimated_cost=Decimal("0.00100000"))
        audio(operation="speech", audience="anonymous", visitor=cls.voice_only, model="gpt-4o-mini-tts", voice="cedar",
              speech_target="native", status="failed", error_code="tts_timeout", provider_calls=1)
        audio(operation="transcription", audience="anonymous", visitor=cls.voice_only, model="gpt-transcribe",
              audio_seconds=Decimal("60.00"), estimated_cost=Decimal("0.00450000"), created_at=now - timedelta(days=40))

    def setUp(self):
        self.client.force_login(self.staff)

    def test_dashboard_separates_text_speech_to_text_and_text_to_speech_costs(self):
        response = self.client.get(DASHBOARD, {"period": "30d"})
        costs, audio = response.context["costs"], response.context["audio_totals"]
        self.assertEqual((costs["text"], costs["stt"], costs["tts"], costs["audio"], costs["total"]),
                         (Decimal("0.001"), Decimal("0.0003"), Decimal("0.003"), Decimal("0.0033"), Decimal("0.0043")))
        self.assertEqual(costs["total"], costs["text"] + costs["audio"])
        self.assertEqual(costs["audio"], costs["stt"] + costs["tts"])
        self.assertAlmostEqual(float(costs["audio_share"]), 0.0033 / 0.0043, places=6)
        self.assertEqual((audio["transcriptions"], audio["speech_plays"], audio["speech_correction"], audio["speech_native"],
                          audio["tts_tokens_in"], audio["tts_tokens_out"], audio["audio_failures"]), (1, 3, 1, 2, 20, 240, 1))
        self.assertEqual(audio["stt_seconds"], Decimal("40.00"))
        for label in ("AI cost · all sources", "Text AI cost", "Audio cost", "Voice input · speech to text",
                      "Voice output · British TTS", "£0.0032", "76.7%", "tts_timeout"):  # $0.0043 at £0.74
            self.assertContains(response, label)
        everything = self.client.get(DASHBOARD, {"period": "all"}).context["costs"]
        self.assertEqual((everything["stt"], everything["total"]), (Decimal("0.0048"), Decimal("0.0088")))
        anonymous = self.client.get(DASHBOARD, {"period": "all", "audience": "anonymous"}).context["costs"]
        self.assertEqual((anonymous["text"], anonymous["audio"]), (None, Decimal("0.0055")))
        by_model = self.client.get(DASHBOARD, {"period": "all", "model": "gpt-transcribe"}).context["costs"]
        self.assertEqual((by_model["text"], by_model["stt"], by_model["tts"]), (None, Decimal("0.0048"), None))

    def test_unknown_audio_pricing_shows_a_dash_not_zero(self):
        AudioUsageEvent.objects.all().delete()
        AudioUsageEvent.objects.create(operation="speech", audience="registered", user=self.learner, status="success",
                                       model="unpriced-tts", input_tokens=5, output_tokens=50, total_tokens=55)
        response = self.client.get(DASHBOARD, {"period": "all"})
        self.assertIsNone(response.context["costs"]["tts"])
        self.assertEqual(response.context["costs"]["total"], Decimal("0.001"))
        self.assertEqual(response.context["audio_totals"]["audio_without_cost"], 1)

    def test_user_and_visitor_reports_include_audio_with_constant_queries(self):
        users = self.client.get(USERS, {"period": "30d"})
        learner = next(member for member in users.context["page_obj"] if member.pk == self.learner.pk)
        self.assertEqual((learner.requests, learner.transcriptions, learner.speech_plays, learner.cost, learner.stt_cost,
                          learner.tts_cost, learner.audio_cost, learner.total_cost),
                         (1, 1, 1, Decimal("0.001"), Decimal("0.0003"), Decimal("0.002"), Decimal("0.0023"), Decimal("0.0033")))
        self.assertEqual([m.pk for m in self.client.get(USERS, {"period": "all", "sort": "total_cost"}).context["page_obj"]][0],
                         self.learner.pk)
        visitors = self.client.get(VISITORS, {"period": "30d"}).context["page_obj"]
        row = next(visitor for visitor in visitors if visitor.pk == self.voice_only.pk)
        self.assertEqual((row.requests, row.speech_plays, row.transcriptions, row.tts_cost, row.total_cost),
                         (0, 2, 0, Decimal("0.001"), Decimal("0.001")))
        with CaptureQueriesContext(connection) as few:
            self.client.get(USERS, {"period": "all"})
            self.client.get(VISITORS, {"period": "all"})
        for index in range(4):
            member = User.objects.create_user(f"extra{index}", f"extra{index}@example.com")
            visitor = AnonymousVisitor.objects.create()
            for owner in ({"user": member, "audience": "registered"}, {"visitor": visitor, "audience": "anonymous"}):
                AudioUsageEvent.objects.create(operation="speech", status="success", model="gpt-4o-mini-tts",
                                               estimated_cost=Decimal("0.001"), **owner)
        with CaptureQueriesContext(connection) as many:
            self.client.get(USERS, {"period": "all"})
            self.client.get(VISITORS, {"period": "all"})
        self.assertEqual(len(few), len(many))

    def test_detail_pages_show_the_audio_breakdown(self):
        detail = self.client.get(f"{USERS}{self.learner.pk}/")
        self.assertEqual((detail.context["costs"]["stt"], detail.context["costs"]["tts"], detail.context["costs"]["total"]),
                         (Decimal("0.0003"), Decimal("0.002"), Decimal("0.0033")))
        self.assertContains(detail, "Audio usage")
        visitor = self.client.get(f"{VISITORS}{self.voice_only.pk}/")
        self.assertEqual((visitor.context["audio_totals"]["speech_plays"], visitor.context["audio_totals"]["transcriptions"],
                          visitor.context["costs"]["audio"]), (2, 1, Decimal("0.0055")))

    def test_audio_ledger_is_read_only_in_admin(self):
        superuser = User.objects.create_superuser("root", "root@example.com", "pw-root-1234")
        self.client.force_login(superuser)
        event = AudioUsageEvent.objects.filter(visitor=self.voice_only).first()
        self.assertEqual(self.client.get("/admin/analytics/audiousageevent/add/").status_code, 403)
        change = f"/admin/analytics/audiousageevent/{event.pk}/change/"
        self.assertEqual(self.client.get(change).status_code, 200)
        self.assertEqual(self.client.post(change, {"status": "failed"}).status_code, 403)
        listing = self.client.get("/admin/analytics/audiousageevent/", {"q": self.voice_only.short_id})
        self.assertContains(listing, self.voice_only.short_id)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get("/admin/analytics/audiousageevent/").status_code, 403)


class RealtimeAudioAnalyticsTests(TestCase):
    """Voice input is split into live (realtime) and finished-recording (file) transcription wherever it is costed."""

    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.staff = User.objects.create_user("staff", "staff@example.com", password="pw-staff-1234", is_staff=True)
        cls.learner = User.objects.create_user("learner", "learner@example.com", "pw-learner-1234")
        cls.visitor = AnonymousVisitor.objects.create()
        UsageEvent.objects.create(audience="registered", user=cls.learner, request_type="correction", status="success",
                                  model="gpt-5.6-luna", total_tokens=900, estimated_cost=Decimal("0.00100000"))

        def audio(**fields):
            return AudioUsageEvent.objects.create(**{"status": "success", "operation": "transcription", **fields})
        live = {"model": "gpt-live-transcribe", "stt_mode": "realtime"}
        audio(audience="registered", user=cls.learner, metering_source="provider", audio_seconds=Decimal("30.00"),
              estimated_cost=Decimal("0.00850000"), **live)
        audio(audience="registered", user=cls.learner, model="gpt-transcribe", stt_mode="file", metering_source="provider",
              audio_seconds=Decimal("60.00"), estimated_cost=Decimal("0.00450000"))
        audio(operation="speech", audience="registered", user=cls.learner, model="gpt-4o-mini-tts", voice="cedar",
              speech_target="correction", input_tokens=10, output_tokens=150, total_tokens=160,
              estimated_cost=Decimal("0.00200000"))
        audio(audience="anonymous", visitor=cls.visitor, metering_source="stream_duration", audio_seconds=Decimal("10.00"),
              estimated_cost=Decimal("0.00283333"), **live)
        audio(audience="anonymous", visitor=cls.visitor, status="failed", error_code="realtime_abandoned", provider_calls=1,
              **live)
        audio(audience="anonymous", visitor=cls.visitor, metering_source="provider", audio_seconds=Decimal("60.00"),
              estimated_cost=Decimal("0.01700000"), created_at=now - timedelta(days=40), **live)

    def setUp(self):
        self.client.force_login(self.staff)

    def test_totals_add_text_realtime_file_and_speech(self):
        response = self.client.get(DASHBOARD, {"period": "30d"})
        costs, audio = response.context["costs"], response.context["audio_totals"]
        text, realtime, file, tts = Decimal("0.001"), Decimal("0.01133333"), Decimal("0.0045"), Decimal("0.002")
        self.assertEqual((costs["text"], costs["stt_realtime"], costs["stt_file"], costs["tts"]), (text, realtime, file, tts))
        self.assertEqual(costs["stt"], realtime + file)  # Voice input total
        self.assertEqual(costs["audio"], realtime + file + tts)  # Audio total
        self.assertEqual(costs["total"], text + realtime + file + tts)  # Grand total
        self.assertAlmostEqual(float(costs["realtime_audio_share"]), float(realtime / (realtime + file + tts)), places=6)
        self.assertAlmostEqual(float(costs["audio_share"]), float(costs["audio"] / costs["total"]), places=6)
        self.assertEqual((audio["stt_realtime"], audio["stt_realtime_seconds"], audio["stt_file"], audio["stt_file_seconds"]),
                         (3, Decimal("40.00"), 1, Decimal("60.00")))
        self.assertEqual((audio["stt_provider_metered"], audio["stt_window_metered"], audio["audio_without_cost"]), (2, 1, 1))
        for label in ("Realtime speech to text", "File speech to text", "Grand total AI cost", "gpt-live-transcribe",
                      "realtime_abandoned", "Voice input · speech to text"):
            self.assertContains(response, label)
        everything = self.client.get(DASHBOARD, {"period": "all"}).context["costs"]
        self.assertEqual(everything["stt_realtime"], realtime + Decimal("0.017"))
        live_only = self.client.get(DASHBOARD, {"period": "30d", "model": "gpt-live-transcribe"}).context["costs"]
        self.assertEqual((live_only["text"], live_only["stt_realtime"], live_only["stt_file"], live_only["tts"]),
                         (None, realtime, None, None))

    def test_user_and_visitor_reports_and_detail_pages_split_voice_input(self):
        users = self.client.get(USERS, {"period": "30d"})
        learner = next(member for member in users.context["page_obj"] if member.pk == self.learner.pk)
        self.assertEqual((learner.transcriptions, learner.realtime_sessions, learner.realtime_seconds,
                          learner.realtime_stt_cost, learner.file_stt_cost, learner.stt_cost, learner.tts_cost,
                          learner.audio_cost, learner.total_cost),
                         (2, 1, Decimal("30"), Decimal("0.0085"), Decimal("0.0045"), Decimal("0.013"), Decimal("0.002"),
                          Decimal("0.015"), Decimal("0.016")))
        self.assertContains(users, "Realtime STT cost")
        self.assertEqual(self.client.get(USERS, {"period": "all", "sort": "realtime"}).status_code, 200)
        visitors = self.client.get(VISITORS, {"period": "all", "sort": "realtime"})
        row = next(visitor for visitor in visitors.context["page_obj"] if visitor.pk == self.visitor.pk)
        self.assertEqual((row.realtime_sessions, row.realtime_seconds, row.realtime_stt_cost, row.file_stt_cost,
                          row.total_cost), (3, Decimal("70"), Decimal("0.01983333"), None, Decimal("0.01983333")))
        detail = self.client.get(f"{USERS}{self.learner.pk}/")
        self.assertEqual((detail.context["costs"]["stt_realtime"], detail.context["costs"]["stt_file"],
                          detail.context["costs"]["total"]), (Decimal("0.0085"), Decimal("0.0045"), Decimal("0.016")))
        self.assertContains(detail, "Realtime transcription")
        visitor = self.client.get(f"{VISITORS}{self.visitor.pk}/")
        self.assertEqual((visitor.context["audio_totals"]["stt_realtime"], visitor.context["costs"]["stt_realtime"]),
                         (3, Decimal("0.01983333")))

    def test_admin_filters_the_audio_ledger_by_transcription_mode(self):
        superuser = User.objects.create_superuser("root", "root@example.com", "pw-root-1234")
        self.client.force_login(superuser)
        listing = self.client.get("/admin/analytics/audiousageevent/", {"stt_mode__exact": "realtime"})
        self.assertEqual(listing.context["cl"].result_count, 4)
        self.assertContains(listing, "Server-observed session window")
        self.assertContains(listing, "Transcription mode")

    def test_existing_transcriptions_become_file_mode_without_cost_changes(self):
        migration = import_module("apps.analytics.migrations.0005_audio_stt_mode")
        legacy = AudioUsageEvent.objects.create(operation="transcription", audience="registered", user=self.learner,
                                                status="success", model="gpt-transcribe", audio_seconds=Decimal("12.00"),
                                                estimated_cost=Decimal("0.00090000"))
        failed = AudioUsageEvent.objects.create(operation="transcription", audience="registered", user=self.learner,
                                                status="failed", model="gpt-transcribe", error_code="transcription_failed")
        migration.mark_existing_transcriptions(django_apps, None)
        legacy.refresh_from_db()
        failed.refresh_from_db()
        self.assertEqual((legacy.stt_mode, legacy.metering_source, legacy.estimated_cost),
                         ("file", "provider", Decimal("0.00090000")))
        self.assertEqual((failed.stt_mode, failed.metering_source), ("file", ""))
        self.assertEqual(AudioUsageEvent.objects.get(operation="speech").stt_mode, "")
        self.assertEqual(AudioUsageEvent.objects.filter(stt_mode="realtime").count(), 4)
