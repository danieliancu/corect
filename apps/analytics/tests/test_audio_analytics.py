from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

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

    def test_audio_pricing_configuration_is_validated(self):
        self.assertEqual(parse_audio_pricing({"m": {"per_minute": 0.006}})["m"], AudioPrice(per_minute=Decimal("0.006")))
        for bad in ([], {"m": {}}, {"m": {"per_minute": "free"}}, {"m": {"output_per_1m": "-1"}}, {"m": "0.006"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_audio_pricing(bad)


class AudioAnalyticsReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.staff = User.objects.create_user("staff", password="pw-staff-1234", is_staff=True)
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
                      "Voice output · British TTS", "$0.0043", "76.7%", "tts_timeout"):
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
            member = User.objects.create_user(f"extra{index}")
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
