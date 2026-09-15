from datetime import date, datetime, timedelta, timezone as dt_timezone
from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.assistant.models import NaturalizeUsage, RateBucket
from apps.assistant.services import quota
from apps.assistant.services.limits import claim_voice
from apps.assistant.services.localday import local_day, seconds_until_reset
from apps.assistant.services.openai_client import AssistantError


def utc(*parts):
    return datetime(*parts, tzinfo=dt_timezone.utc)


class PlanQuotaTests(TestCase):
    def use(self, actor, tier, times):
        for _ in range(times):
            quota.commit(quota.reserve(actor, tier))

    def test_default_limits_are_five_twenty_and_two_hundred(self):
        self.assertEqual(settings.NATURALIZE_DAILY_LIMITS, {"anonymous": 5, "free": 20, "pro": 200})

    def test_each_plan_gets_its_daily_limit_then_is_refused(self):
        for tier, limit in (("anonymous", 5), ("free", 20), ("pro", 200)):
            with self.subTest(tier=tier):
                actor = f"test:{tier}"
                self.use(actor, tier, limit)
                with self.assertRaises(AssistantError) as refused:
                    quota.reserve(actor, tier)
                self.assertEqual(refused.exception.code, "quota_exhausted")
                self.assertGreater(refused.exception.retry_after, 0)
                status = quota.status(actor, tier)
                self.assertEqual((status.used, status.remaining, status.exhausted), (limit, 0, True))
                self.assertEqual(NaturalizeUsage.objects.get(actor=actor).reserved, 0)

    @override_settings(NATURALIZE_DAILY_LIMITS={"anonymous": 2, "free": 2, "pro": 2})
    def test_requests_in_progress_hold_a_use_until_they_finish(self):
        first, second = quota.reserve("user:1", "free"), quota.reserve("user:1", "free")
        with self.assertRaises(AssistantError):
            quota.reserve("user:1", "free")  # Two requests are still running: no third may start.
        quota.release(first)
        third = quota.reserve("user:1", "free")
        quota.commit(second)
        quota.commit(third)
        quota.release(first)  # Releasing twice never goes below zero.
        usage = NaturalizeUsage.objects.get()
        self.assertEqual((usage.used, usage.reserved), (2, 0))

    @override_settings(NATURALIZE_DAILY_LIMITS={"anonymous": 1, "free": 1, "pro": 1})
    def test_a_reservation_left_behind_by_a_stopped_request_is_given_back(self):
        quota.reserve("user:1", "free")
        with self.assertRaises(AssistantError):
            quota.reserve("user:1", "free")  # A recent reservation is a request that may still be running.
        NaturalizeUsage.objects.update(reserved_at=timezone.now() - timedelta(hours=1))
        quota.reserve("user:1", "free")
        self.assertEqual(NaturalizeUsage.objects.get().reserved, 1)

    def test_a_pro_upgrade_during_the_day_raises_the_limit_at_once(self):
        self.use("user:1", "free", 20)
        with self.assertRaises(AssistantError):
            quota.reserve("user:1", "free")
        quota.reserve("user:1", "pro")

    def test_status_never_creates_a_row(self):
        self.assertEqual(quota.status("user:9", "anonymous").remaining, 5)
        self.assertFalse(NaturalizeUsage.objects.exists())

    @override_settings(NATURALIZE_DAILY_LIMITS={"anonymous": 7, "free": 30, "pro": 150})
    def test_messages_and_notes_use_the_configured_limits_and_romanian_numerals(self):
        self.assertEqual(quota.exhausted_message("anonymous"),
                         "Ai folosit cele 7 utilizări gratuite de azi. Creează un cont gratuit și primești 30 pe zi.")
        self.assertEqual(quota.exhausted_message("free"),
                         "Ai folosit cele 30 de utilizări de azi. Pro oferă până la 150 de naturalizări pe zi, în regim Fair Use.")
        self.assertEqual(quota.exhausted_message("pro"), "Ai atins limita Fair Use de 150 de utilizări pentru astăzi. "
                                                         "Limita se resetează la miezul nopții (ora Regatului Unit).")
        self.assertEqual(quota.QuotaStatus("anonymous", 7, 4).note, "3 din 7 utilizări rămase astăzi")
        self.assertEqual(quota.QuotaStatus("free", 30, 30).note, "0 din 30 de utilizări rămase astăzi")
        self.assertEqual(quota.QuotaStatus("pro", 150, 149).note, "")

    def test_the_usage_row_holds_only_an_actor_key_a_day_and_counts(self):
        self.assertEqual({field.name for field in NaturalizeUsage._meta.get_fields()},
                         {"id", "actor", "day", "used", "reserved", "reserved_at"})

    def test_cleanup_keeps_two_days_of_counters(self):
        today = local_day()
        for days_ago in (0, 1, 2, 3):
            NaturalizeUsage.objects.create(actor=f"user:{days_ago}", day=today - timedelta(days=days_ago), used=1)
        call_command("cleanup_assistant", stdout=StringIO())
        self.assertEqual(sorted(NaturalizeUsage.objects.values_list("actor", flat=True)), ["user:0", "user:1", "user:2"])


class LondonCalendarDayTests(TestCase):
    def test_days_follow_london_midnight_in_summer_and_in_winter(self):
        self.assertEqual(local_day(utc(2026, 7, 1, 23, 30)), date(2026, 7, 2))  # 00:30 BST
        self.assertEqual(local_day(utc(2026, 1, 15, 23, 30)), date(2026, 1, 15))  # 23:30 GMT
        self.assertEqual(seconds_until_reset(utc(2026, 7, 1, 22, 59)), 60)  # BST midnight is 23:00 UTC.
        self.assertEqual(seconds_until_reset(utc(2026, 1, 15, 23, 59)), 60)  # GMT midnight is 00:00 UTC.

    def test_the_days_the_clocks_change_are_25_and_23_hours_long(self):
        self.assertEqual(seconds_until_reset(utc(2026, 10, 24, 23, 0)), 25 * 3600)  # Clocks go back on 25 October.
        self.assertEqual(seconds_until_reset(utc(2026, 3, 29, 0, 0)), 23 * 3600)  # Clocks go forward on 29 March.

    def test_uses_reset_at_london_midnight_not_utc_midnight(self):
        with patch("django.utils.timezone.now", return_value=utc(2026, 7, 1, 22, 30)):  # 23:30 BST, 1 July
            for _ in range(5):
                quota.commit(quota.reserve("anon:x", "anonymous"))
            with self.assertRaises(AssistantError) as refused:
                quota.reserve("anon:x", "anonymous")
            self.assertEqual(refused.exception.retry_after, 30 * 60)
        with patch("django.utils.timezone.now", return_value=utc(2026, 7, 1, 23, 1)):  # 00:01 BST, 2 July (1 July in UTC)
            quota.reserve("anon:x", "anonymous")
        self.assertEqual(sorted(NaturalizeUsage.objects.values_list("day", flat=True)),
                         [date(2026, 7, 1), date(2026, 7, 2)])

    @override_settings(VOICE_DAILY_GUARDRAILS={"transcription": {"anonymous": 1, "free": 2, "pro": 3},
                                               "speech": {"anonymous": 1, "free": 2, "pro": 3}})
    def test_daily_voice_guardrails_use_the_london_day_and_the_plan_tier(self):
        with patch("django.utils.timezone.now", return_value=utc(2026, 7, 1, 23, 30)):
            claim_voice("anon:y", "transcription", "anonymous")
            with self.assertRaises(AssistantError):
                claim_voice("anon:y", "transcription", "anonymous")
            for _ in range(3):
                claim_voice("user:7", "transcription", "pro")  # Pro's own, higher daily guardrail.
        self.assertTrue(RateBucket.objects.filter(key="voice-stt:anon:y:day:2026-07-02").exists())
        self.assertFalse(NaturalizeUsage.objects.exists())  # Voice guardrails never touch the plan quota.
