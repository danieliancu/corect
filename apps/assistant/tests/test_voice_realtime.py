"""Live transcription: the short-lived credential endpoint, one audio ledger row per session, and cleanup."""
import io
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import httpx
from django.contrib.auth.models import Group, User
from django.core import serializers, signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from openai import APIConnectionError, APITimeoutError

from apps.analytics.models import AnonymousVisitor, AudioUsageEvent
from apps.analytics.services.visitors import VISITOR_COOKIE
from apps.core.consent import CONSENT_COOKIE, consent_cookie_value
from apps.assistant.models import AssistantRequest, NaturalizeUsage, RateBucket, RealtimeTranscriptionSession
from apps.assistant.services.pricing import parse_audio_pricing
from apps.assistant.services.voice import REALTIME_CALLS_URL, TRANSCRIPTION_PROMPT
from .examples import naturalized_english
from .provider import ProviderMock

SESSION_URL, FINISH_URL = "/assistant/realtime-transcription/session/", "/assistant/realtime-transcription/finish/"
API_KEY = "sk-server-only-sentinel-key"
SECRET = "ek_test_short_lived_secret"
CLIENT_IP = "203.0.113.20"
SPOKEN = "Words the learner said out loud"
PRICING = parse_audio_pricing({"gpt-live-transcribe": {"per_minute": "0.017"}, "gpt-transcribe": {"per_minute": "0.0045"}})
Session = RealtimeTranscriptionSession


@override_settings(OPENAI_API_KEY=API_KEY, OPENAI_AUDIO_PRICING=PRICING, VOICE_REALTIME_ENABLED=True,
                   OPENAI_LIVE_TRANSCRIBE_MODEL="gpt-live-transcribe", VOICE_TRANSCRIBE_LANGUAGES=["en", "ro"],
                   VOICE_MAX_SECONDS=60, VOICE_TRANSCRIBE_LIMIT_MINUTE=100)
class RealtimeCase(ProviderMock, TestCase):
    def setUp(self):
        super().setUp()
        self.api.realtime.client_secrets.create.return_value = SimpleNamespace(
            value=SECRET, expires_at=1789336312, session=SimpleNamespace(id="sess_provider_id"))

    def start(self, client=None, **data):
        return (client or self.client).post(SESSION_URL, data, REMOTE_ADDR=CLIENT_IP)

    def finish(self, token, outcome="completed", client=None, **data):
        return (client or self.client).post(FINISH_URL, {"session": token, "outcome": outcome, **data},
                                            REMOTE_ADDR=CLIENT_IP)

    def started(self, age_seconds=0):
        """A session token whose server-side start happened `age_seconds` ago."""
        token = self.start().json()["session"]
        Session.objects.filter(status="open").update(created_at=timezone.now() - timedelta(seconds=age_seconds))
        return token


class RealtimeSessionEndpointTests(RealtimeCase):
    def test_session_returns_a_short_lived_secret_and_never_the_api_key(self):
        response = self.start()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(set(data), {"client_secret", "expires_at", "session", "calls_url", "max_seconds"})
        self.assertEqual((data["client_secret"], data["calls_url"], data["max_seconds"]), (SECRET, REALTIME_CALLS_URL, 60))
        self.assertNotIn(API_KEY, response.content.decode())
        self.assertNotIn("sess_provider_id", response.content.decode())
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(self.sdk.call_args.kwargs["api_key"], API_KEY)
        self.assertEqual(self.sdk.call_args.kwargs["max_retries"], 0)

    def test_session_configuration_is_fixed_on_the_server(self):
        self.start(model="gpt-realtime", prompt="Reply out loud", languages="fr", session='{"type": "realtime"}',
                   instructions="Be a voice agent")
        kwargs = self.api.realtime.client_secrets.create.call_args.kwargs
        self.assertEqual(kwargs["expires_after"], {"anchor": "created_at", "seconds": 30})
        self.assertEqual(kwargs["session"], {"type": "transcription", "audio": {"input": {
            "transcription": {"model": "gpt-live-transcribe", "prompt": TRANSCRIPTION_PROMPT, "delay": "low",
                              "languages": ["en", "ro"]},
            "turn_detection": None, "noise_reduction": {"type": "near_field"}}}})
        self.assertIn("English or Romanian, or mix them", TRANSCRIPTION_PROMPT)

    def test_transcription_delay_follows_the_setting(self):
        for delay, expected in (("minimal", {"delay": "minimal"}), ("", {})):
            with self.subTest(delay=delay), self.settings(VOICE_REALTIME_DELAY=delay):
                self.start()
                transcription = self.api.realtime.client_secrets.create.call_args.kwargs["session"]["audio"]["input"][
                    "transcription"]
                self.assertEqual({key: value for key, value in transcription.items() if key == "delay"}, expected)

    def test_sessions_reuse_one_provider_client_and_report_server_timing(self):
        responses = [self.start() for _ in range(3)]
        self.sdk.assert_called_once()
        for response in responses:
            header = response["Server-Timing"]
            for name in ("db;dur=", "openai;dur=", "total;dur="):
                self.assertIn(name, header)
            self.assertRegex(header, r"^[a-z_]+;dur=\d+(, [a-z_]+;dur=\d+)*$")

    def test_only_post_with_csrf_is_accepted(self):
        self.assertEqual(self.client.get(SESSION_URL, REMOTE_ADDR=CLIENT_IP).status_code, 405)
        self.assertEqual(self.start(client=Client(enforce_csrf_checks=True)).status_code, 403)
        self.assertEqual(self.client.get(FINISH_URL).status_code, 405)
        self.api.realtime.client_secrets.create.assert_not_called()
        self.assertFalse(Session.objects.exists())

    def test_kill_switch_hides_live_transcription(self):
        self.assertContains(self.client.get("/"), 'data-realtime-url="/assistant/realtime-transcription/session/"')
        with self.settings(VOICE_REALTIME_ENABLED=False):
            self.assertEqual(self.start().status_code, 404)
            home = self.client.get("/")
        self.assertNotContains(home, "data-realtime-url")
        self.assertContains(home, 'data-transcribe-url="/assistant/transcribe/"')
        self.api.realtime.client_secrets.create.assert_not_called()

    def test_rate_limit_shares_the_speech_to_text_quota(self):
        with self.settings(VOICE_TRANSCRIBE_LIMIT_MINUTE=1):
            self.assertEqual(self.start().status_code, 200)
            limited = self.start()
            self.assertEqual(limited.status_code, 429)
            self.assertEqual(limited.json()["code"], "audio_rate_limit")
            self.assertTrue(limited.has_header("Retry-After"))
            upload = self.client.post("/assistant/transcribe/", {"audio": SimpleUploadedFile(
                "a.webm", b"\x1a\x45\xdf\xa3" + b"\x00" * 64, content_type="audio/webm")}, REMOTE_ADDR=CLIENT_IP)
            self.assertEqual(upload.status_code, 429)  # Opening live sessions cannot bypass the file quota, or vice versa.
        self.api.realtime.client_secrets.create.assert_called_once()
        self.assertTrue(all(key.startswith("voice-stt:") for key in RateBucket.objects.values_list("key", flat=True)))
        rejected = AudioUsageEvent.objects.filter(status="rejected", stt_mode="realtime").get()
        self.assertEqual((rejected.model, rejected.estimated_cost, rejected.provider_calls),
                         ("gpt-live-transcribe", Decimal(0), 0))

    def test_provider_failures_are_recorded_once_without_opening_a_session(self):
        request = httpx.Request("POST", "https://api.openai.com/v1/realtime/client_secrets")
        for error, code in ((APITimeoutError(request=request), "realtime_timeout"),
                            (APIConnectionError(request=request), "realtime_unavailable")):
            with self.subTest(code=code):
                self.api.realtime.client_secrets.create.side_effect = error
                response = self.start()
                self.assertEqual((response.status_code, response.json()["code"]), (503, code))
                event = AudioUsageEvent.objects.get(error_code=code)
                self.assertEqual((event.status, event.stt_mode, event.model, event.provider_calls),
                                 ("failed", "realtime", "gpt-live-transcribe", 1))
        self.assertFalse(Session.objects.exists())

    def test_anonymous_visitors_are_tracked_and_the_cookie_can_be_switched_off(self):
        self.client.cookies[CONSENT_COOKIE] = consent_cookie_value(True)  # Analytics allowed.
        response = self.start()
        self.assertIn(VISITOR_COOKIE, response.cookies)
        visitor = AnonymousVisitor.objects.get()
        self.assertEqual((Session.objects.get().visitor, Session.objects.get().audience), (visitor, "anonymous"))
        with self.settings(ANALYTICS_VISITOR_COOKIE=False):
            response = self.start(client=Client())
        self.assertNotIn(VISITOR_COOKIE, response.cookies)
        self.assertEqual(Session.objects.filter(visitor__isnull=True).count(), 1)
        self.assertEqual(AnonymousVisitor.objects.count(), 1)


class VoiceAndThePlanQuotaTests(RealtimeCase):
    """Only „Vreau să sune natural!” uses the plan quota: the microphone, live transcription and the recording fallback
    never do, and text that came from the microphone costs exactly one use when it is submitted."""

    def test_live_failure_then_recording_fallback_then_submit_uses_exactly_one(self):
        token = self.start().json()["session"]
        self.finish(token, outcome="connect_failed")  # Live transcription could not connect before any audio.
        self.api.audio.transcriptions.create.return_value = SimpleNamespace(
            text=SPOKEN, usage=SimpleNamespace(type="duration", seconds=3))
        upload = self.client.post("/assistant/transcribe/", {"audio": SimpleUploadedFile(
            "a.webm", b"\x1a\x45\xdf\xa3" + b"\x00" * 64, content_type="audio/webm")}, REMOTE_ADDR=CLIENT_IP)
        self.assertEqual(upload.json(), {"text": SPOKEN})
        self.assertFalse(NaturalizeUsage.objects.exists())  # Microphone, live session and fallback: no use.
        with patch("apps.assistant.views.NaturalizeService.naturalize", return_value=naturalized_english()):
            submitted = self.client.post("/naturalize/", {"text": SPOKEN, "submission_token": uuid4()},
                                         REMOTE_ADDR=CLIENT_IP)
        self.assertEqual(submitted.status_code, 200)
        self.assertEqual(NaturalizeUsage.objects.get().used, 1)
        # The speech-to-text guardrail still accounts for both voice calls, separately from the plan quota.
        self.assertEqual(AudioUsageEvent.objects.filter(operation="transcription").count(), 2)
        self.assertEqual(set(AudioUsageEvent.objects.values_list("plan", flat=True)), {"anonymous"})

    @override_settings(VOICE_DAILY_GUARDRAILS={"transcription": {"anonymous": 1, "free": 2, "pro": 4},
                                               "speech": {"anonymous": 1, "free": 2, "pro": 4}})
    def test_the_daily_speech_to_text_guardrail_follows_the_plan(self):
        self.assertEqual(self.start().status_code, 200)
        self.assertEqual(self.start().status_code, 429)  # Anonymous: one session a day in this configuration.
        member = User.objects.create_user("pro-voice", password="Voice-test-pass-1")
        member.groups.add(Group.objects.get_or_create(name="Pro")[0])
        self.client.force_login(member)
        self.assertEqual([self.start().status_code for _ in range(4)], [200] * 4)
        self.assertEqual(self.start().status_code, 429)
        self.assertEqual(AudioUsageEvent.objects.filter(status="rejected").latest("pk").plan, "pro")
        self.assertFalse(NaturalizeUsage.objects.exists())


class RealtimeFinishTests(RealtimeCase):
    def test_one_session_is_one_ledger_event_however_often_it_is_finished(self):
        token = self.started(age_seconds=35)
        for _ in range(3):
            self.assertEqual(self.finish(token, provider_seconds="30").status_code, 200)
        self.finish(token, outcome="page_closed")
        event = AudioUsageEvent.objects.get(operation="transcription")
        self.assertEqual((event.status, event.stt_mode, event.model, event.metering_source, event.audio_seconds,
                          event.estimated_cost, event.provider_calls),
                         ("success", "realtime", "gpt-live-transcribe", "provider", Decimal("30.00"),
                          Decimal("0.00850000"), 1))
        self.assertEqual(Session.objects.get().status, "finished")

    def test_realtime_audio_is_priced_at_seventeen_thousandths_of_a_dollar_per_minute(self):
        for seconds, cost in (("10", "0.00283333"), ("30", "0.00850000"), ("60", "0.01700000")):
            with self.subTest(seconds=seconds):
                self.finish(self.started(age_seconds=65), provider_seconds=seconds)
                event = AudioUsageEvent.objects.latest("pk")
                self.assertEqual((event.audio_seconds, event.estimated_cost), (Decimal(seconds), Decimal(cost)))

    def test_unknown_realtime_pricing_leaves_the_cost_empty(self):
        with self.settings(OPENAI_AUDIO_PRICING=parse_audio_pricing({"gpt-transcribe": {"per_minute": "0.0045"}})):
            self.finish(self.started(age_seconds=20), provider_seconds="12")
        event = AudioUsageEvent.objects.get()
        self.assertEqual((event.audio_seconds, event.estimated_cost), (Decimal("12.00"), None))

    def test_reported_duration_is_only_trusted_within_the_server_observed_window(self):
        cases = [(20, "45", "stream_duration"), (20, None, "stream_duration"), (20, "NaN", "stream_duration"),
                 (20, "-5", "stream_duration"), (20, "abc", "stream_duration"), (20, "21.5", "provider")]
        for age, reported, source in cases:
            with self.subTest(reported=reported):
                extra = {"provider_seconds": reported} if reported is not None else {}
                self.finish(self.started(age_seconds=age), **extra)
                event = AudioUsageEvent.objects.latest("pk")
                self.assertEqual(event.metering_source, source)
                if source == "stream_duration":
                    self.assertTrue(Decimal("20") <= event.audio_seconds < Decimal("22"))
        # Never more than the recording limit plus connection overhead, whatever the browser claims.
        self.finish(self.started(age_seconds=900), provider_seconds="800")
        event = AudioUsageEvent.objects.latest("pk")
        self.assertEqual((event.metering_source, event.audio_seconds), ("stream_duration", Decimal("70.00")))

    def test_connect_failure_before_audio_costs_nothing(self):
        self.finish(self.started(age_seconds=5), outcome="connect_failed", provider_seconds="4")
        event = AudioUsageEvent.objects.get()
        self.assertEqual((event.status, event.error_code, event.audio_seconds, event.estimated_cost),
                         ("failed", "realtime_connect_failed", Decimal("0"), Decimal("0")))

    def test_interrupted_and_closed_sessions_keep_their_metered_cost(self):
        for outcome in ("interrupted", "page_closed"):
            with self.subTest(outcome=outcome):
                self.finish(self.started(age_seconds=12), outcome=outcome, provider_seconds="6")
                event = AudioUsageEvent.objects.latest("pk")
                self.assertEqual((event.status, event.error_code, event.audio_seconds, event.estimated_cost),
                                 ("failed", f"realtime_{outcome}", Decimal("6.00"), Decimal("0.00170000")))

    def test_tampered_expired_foreign_or_malformed_finishes_write_nothing(self):
        token = self.started(age_seconds=10)
        speech_token = signing.dumps({"t": "Hi", "k": "correction", "e": None}, salt="corect.speech.v1")
        for bad, status in ((token + "x", 403), (speech_token, 403), ("", 403), ("not-a-token", 403)):
            with self.subTest(token=bad[:12]):
                self.assertEqual(self.finish(bad).status_code, status)
        self.assertEqual(self.finish(token, outcome="delete_everything").status_code, 400)
        with self.settings(VOICE_MAX_SECONDS=-500):
            expired = self.finish(token)
        self.assertEqual((expired.status_code, expired.json()["code"]), (403, "realtime_session_expired"))
        self.assertEqual(self.finish(token, client=Client(enforce_csrf_checks=True)).status_code, 403)
        self.assertFalse(AudioUsageEvent.objects.exists())

    def test_registered_sessions_link_the_user_and_persist_no_speech_or_secret(self):
        learner = User.objects.create_user("learner", "learner@example.com", "pw-learner-1234")
        self.client.force_login(learner)
        token = self.started(age_seconds=8)
        self.finish(token, provider_seconds="7", transcript=SPOKEN, text=SPOKEN)
        event = AudioUsageEvent.objects.get()
        self.assertEqual((event.user, event.audience, event.visitor), (learner, "registered", None))
        self.assertFalse(AssistantRequest.objects.exists())  # Speech is not history until it is sent to be made natural.
        stored = serializers.serialize("json", [*Session.objects.all(), *AudioUsageEvent.objects.all()])
        for secret in (SPOKEN, SECRET, API_KEY, CLIENT_IP, "sess_provider_id"):
            self.assertNotIn(secret, stored)

    def test_finished_recordings_are_recorded_as_file_mode(self):
        self.api.audio.transcriptions.create.return_value = SimpleNamespace(
            text="Hello there.", usage=SimpleNamespace(type="duration", seconds=4.5))
        upload = SimpleUploadedFile("a.webm", b"\x1a\x45\xdf\xa3" + b"\x00" * 64, content_type="audio/webm")
        self.assertEqual(self.client.post("/assistant/transcribe/", {"audio": upload}, REMOTE_ADDR=CLIENT_IP).status_code, 200)
        event = AudioUsageEvent.objects.get()
        self.assertEqual((event.stt_mode, event.metering_source, event.model, event.estimated_cost),
                         ("file", "provider", "gpt-transcribe", Decimal("0.00033750")))


class RealtimeTimingTests(RealtimeCase):
    TIMINGS = {"mic_ms": "120", "session_ms": "910", "connect_ms": "640", "startup_ms": "1850",
               "first_word_ms": "1400", "finalise_ms": "930", "final_received": "1"}

    def test_browser_timings_are_stored_as_bounded_numbers_on_the_session_row(self):
        self.finish(self.started(age_seconds=10), provider_seconds="9", **self.TIMINGS)
        event = AudioUsageEvent.objects.get()
        self.assertEqual((event.mic_ms, event.session_ms, event.connect_ms, event.startup_ms, event.first_word_ms,
                          event.finalise_ms, event.final_received), (120, 910, 640, 1850, 1400, 930, True))

    def test_junk_timings_are_dropped_and_only_the_first_finish_counts(self):
        junk = {"mic_ms": "-1", "session_ms": "NaN", "connect_ms": "1e9", "startup_ms": "700000",
                "first_word_ms": "12.5", "finalise_ms": "abc", "final_received": "maybe"}
        token = self.started(age_seconds=10)
        self.finish(token, provider_seconds="9", **junk)
        self.finish(token, provider_seconds="9", **self.TIMINGS)
        event = AudioUsageEvent.objects.get()
        self.assertEqual((event.mic_ms, event.session_ms, event.connect_ms, event.startup_ms, event.first_word_ms,
                          event.finalise_ms, event.final_received), (None,) * 7)

    def test_a_failed_connection_keeps_its_start_up_timings_at_no_cost(self):
        self.finish(self.started(age_seconds=5), outcome="connect_failed", mic_ms="80", session_ms="950")
        event = AudioUsageEvent.objects.get()
        self.assertEqual((event.error_code, event.estimated_cost, event.mic_ms, event.session_ms, event.startup_ms),
                         ("realtime_connect_failed", Decimal("0"), 80, 950, None))

    def test_file_transcription_records_the_server_measured_provider_time(self):
        self.api.audio.transcriptions.create.return_value = SimpleNamespace(
            text="Hello there.", usage=SimpleNamespace(type="duration", seconds=4.5))
        upload = SimpleUploadedFile("a.webm", b"\x1a\x45\xdf\xa3" + b"\x00" * 64, content_type="audio/webm")
        self.client.post("/assistant/transcribe/", {"audio": upload}, REMOTE_ADDR=CLIENT_IP)
        self.assertIsInstance(AudioUsageEvent.objects.get().provider_duration_ms, int)


class RealtimeCleanupTests(RealtimeCase):
    def test_cleanup_accounts_for_abandoned_sessions_once_and_keeps_the_ledger(self):
        finished = self.started(age_seconds=10)
        self.finish(finished, provider_seconds="9")
        self.start()
        now = timezone.now()
        Session.objects.filter(status="open").update(expires_at=now - timedelta(minutes=1))
        Session.objects.filter(status="finished").update(finished_at=now - timedelta(days=8))
        self.start()  # Still in progress: left alone.
        call_command("cleanup_assistant", stdout=io.StringIO())
        call_command("cleanup_assistant", stdout=io.StringIO())
        abandoned = AudioUsageEvent.objects.get(error_code="realtime_abandoned")
        self.assertEqual((abandoned.status, abandoned.stt_mode, abandoned.audio_seconds, abandoned.estimated_cost,
                          abandoned.metering_source), ("failed", "realtime", None, None, ""))
        self.assertEqual(AudioUsageEvent.objects.filter(operation="transcription").count(), 2)
        self.assertEqual(sorted(Session.objects.values_list("status", flat=True)), ["abandoned", "open"])
        Session.objects.filter(status="abandoned").update(finished_at=now - timedelta(days=8))
        call_command("cleanup_assistant", stdout=io.StringIO())
        self.assertEqual(list(Session.objects.values_list("status", flat=True)), ["open"])
        self.assertEqual(AudioUsageEvent.objects.filter(operation="transcription").count(), 2)
