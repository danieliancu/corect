from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from django.contrib.auth.models import User
from django.core import serializers
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import models
from django.test import Client, TestCase, override_settings
from openai import APITimeoutError

from apps.analytics.models import AnonymousVisitor, AudioUsageEvent, UsageEvent
from apps.analytics.services.visitors import VISITOR_COOKIE
from apps.core.consent import CONSENT_COOKIE, consent_cookie_value
from apps.assistant.models import AssistantRequest, GrammarCorrection, RateBucket, SubmissionClaim
from apps.assistant.services.voice import TRANSCRIPTION_PROMPT

WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 64
MP4 = b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 64
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 64
OGG = b"OggS" + b"\x00" * 64
CLIENT_IP = "203.0.113.9"
ENGLISH = "Let's meet behind the house."
ROMANIAN = "Mâine mergem la piață și cumpărăm mere."


def recording(data=WEBM, content_type="audio/webm;codecs=opus", name="my-private-name.webm"):
    return SimpleUploadedFile(name, data, content_type=content_type)


class TranscriptionEndpointTests(TestCase):
    def setUp(self):
        self.sdk = patch("apps.assistant.services.voice.OpenAI").start()
        self.addCleanup(patch.stopall)
        self.api = self.sdk.return_value.__enter__.return_value
        self.respond(ENGLISH)

    def respond(self, text, seconds=4.5):
        usage = SimpleNamespace(type="duration", seconds=seconds) if seconds is not None else None
        self.api.audio.transcriptions.create.return_value = SimpleNamespace(text=text, usage=usage)

    def post(self, file=None, client=None, **data):
        payload = {"audio": file or recording(), **data}
        return (client or self.client).post("/assistant/transcribe/", payload, REMOTE_ADDR=CLIENT_IP)

    def test_english_and_romanian_transcripts_are_returned(self):
        for text in (ENGLISH, ROMANIAN):
            with self.subTest(text=text):
                self.respond(text)
                response = self.post()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"text": text})
                self.assertIn("no-store", response["Cache-Control"])

    @override_settings(OPENAI_TRANSCRIBE_MODEL="transcribe-test-model")
    def test_provider_gets_configured_model_language_hints_and_no_uploaded_filename(self):
        for data, content_type, sent_name, sent_mime in ((MP4, "audio/mp4", "recording.m4a", "audio/mp4"),
                                                         (WAV, "audio/wav", "recording.wav", "audio/wav")):
            with self.subTest(content_type=content_type):
                self.assertEqual(self.post(recording(data, content_type, "secret-name.bin")).status_code, 200)
                kwargs = self.api.audio.transcriptions.create.call_args.kwargs
                self.assertEqual(kwargs["model"], "transcribe-test-model")
                self.assertEqual((kwargs["file"][0], kwargs["file"][2]), (sent_name, sent_mime))
                self.assertEqual(kwargs["prompt"], TRANSCRIPTION_PROMPT)
                self.assertEqual(kwargs["extra_body"], {"languages": ["en", "ro"]})
                self.assertNotIn("language", kwargs)
        self.assertEqual(self.sdk.call_args.kwargs["max_retries"], 0)

    def test_anonymous_transcription_persists_no_audio_transcript_filename_or_ip(self):
        self.client.cookies[CONSENT_COOKIE] = consent_cookie_value(True)  # Analytics allowed: the event carries the visitor.
        response = self.post()
        self.assertEqual(response.status_code, 200)
        stored = serializers.serialize("json", [
            *AudioUsageEvent.objects.all(), *AnonymousVisitor.objects.all(), *UsageEvent.objects.all(),
            *AssistantRequest.objects.all(), *GrammarCorrection.objects.all(), *RateBucket.objects.all(),
            *SubmissionClaim.objects.all()])
        for secret in (ENGLISH, "behind the house", "my-private-name", CLIENT_IP):
            self.assertNotIn(secret, stored)
        self.assertFalse(AssistantRequest.objects.exists())
        self.assertFalse(UsageEvent.objects.exists())
        event = AudioUsageEvent.objects.get()
        self.assertEqual((event.operation, event.audience, event.status, event.model, event.provider_calls),
                         ("transcription", "anonymous", "success", "gpt-transcribe", 1))
        # 4.5 seconds ÷ 60 × $0.0045 per minute
        self.assertEqual((event.audio_seconds, event.estimated_cost), (Decimal("4.50"), Decimal("0.00033750")))
        self.assertEqual(event.visitor, AnonymousVisitor.objects.get())
        self.assertEqual(response.cookies[VISITOR_COOKIE].value, str(event.visitor_id))

    def test_registered_transcription_is_not_added_to_history(self):
        user = User.objects.create_user("ana", password="test-password")
        self.client.force_login(user)
        self.assertEqual(self.post().status_code, 200)
        self.assertFalse(AssistantRequest.objects.exists())
        event = AudioUsageEvent.objects.get()
        self.assertEqual((event.audience, event.user, event.visitor), ("registered", user, None))

    def test_invalid_uploads_are_rejected_before_any_provider_call(self):
        cases = [
            ({"note": "no audio"}, 400, "microphone_upload_invalid"),
            ({"audio": recording(b"hello, this is not audio", "audio/webm")}, 415, "unsupported_audio"),
            ({"audio": recording(WEBM, "audio/wav")}, 415, "unsupported_audio"),
            ({"audio": recording(WEBM, "text/plain")}, 415, "unsupported_audio"),
            ({"audio": recording(OGG, "audio/ogg")}, 415, "unsupported_audio"),
        ]
        for payload, status, code in cases:
            with self.subTest(code=code, payload=list(payload)):
                response = self.client.post("/assistant/transcribe/", payload, REMOTE_ADDR=CLIENT_IP)
                self.assertEqual((response.status_code, response.json()["code"]), (status, code))
        self.api.audio.transcriptions.create.assert_not_called()
        self.assertFalse(AudioUsageEvent.objects.exists())

    @override_settings(VOICE_MAX_BYTES=100)
    def test_oversized_recordings_are_rejected(self):
        response = self.post(recording(WEBM + b"\x00" * 500))
        self.assertEqual((response.status_code, response.json()["code"]), (413, "audio_too_large"))
        with override_settings(VOICE_MAX_BYTES=10):
            response = self.post(recording(WEBM + b"\x00" * 80_000))
        self.assertEqual((response.status_code, response.json()["code"]), (413, "audio_too_large"))
        self.api.audio.transcriptions.create.assert_not_called()

    def test_provider_timeout_and_empty_transcripts_fail_cleanly(self):
        self.api.audio.transcriptions.create.side_effect = APITimeoutError(request=httpx.Request("POST", "https://api.openai.com"))
        response = self.post()
        self.assertEqual((response.status_code, response.json()["code"]), (503, "transcription_timeout"))
        self.assertNotIn("openai", response.content.decode().lower())
        self.api.audio.transcriptions.create.side_effect = None
        self.respond("   ")
        response = self.post()
        self.assertEqual((response.status_code, response.json()["code"]), (422, "transcription_empty"))
        timeout, empty = AudioUsageEvent.objects.order_by("pk")
        self.assertEqual((timeout.status, timeout.error_code, timeout.provider_calls, timeout.audio_seconds,
                          timeout.estimated_cost), ("failed", "transcription_timeout", 1, None, None))
        self.assertEqual((empty.status, empty.error_code, empty.audio_seconds), ("failed", "transcription_empty", Decimal("4.50")))

    @override_settings(ASSISTANT_MAX_CHARACTERS=10)
    def test_transcript_longer_than_the_text_box_is_refused(self):
        response = self.post()
        self.assertEqual((response.status_code, response.json()["code"]), (422, "transcript_too_long"))

    @override_settings(VOICE_TRANSCRIBE_LIMIT_MINUTE=1)
    def test_voice_rate_limit_is_separate_from_text_limits(self):
        self.assertEqual(self.post().status_code, 200)
        response = self.post()
        self.assertEqual((response.status_code, response.json()["code"]), (429, "audio_rate_limit"))
        self.assertIn("Retry-After", response)
        self.api.audio.transcriptions.create.assert_called_once()
        self.assertEqual(list(AudioUsageEvent.objects.order_by("pk").values_list("status", "estimated_cost")),
                         [("success", Decimal("0.00033750")), ("rejected", Decimal("0"))])
        self.assertTrue(RateBucket.objects.exists())
        self.assertFalse(RateBucket.objects.exclude(key__startswith="voice-stt:").exists())

    def test_csrf_is_enforced(self):
        response = self.post(client=Client(enforce_csrf_checks=True))
        self.assertEqual(response.status_code, 403)
        self.api.audio.transcriptions.create.assert_not_called()

    def test_visitor_cookie_is_reused_and_can_be_switched_off(self):
        self.client.cookies[CONSENT_COOKIE] = consent_cookie_value(True)  # Analytics allowed.
        self.post()
        self.post()
        self.assertEqual(AnonymousVisitor.objects.count(), 1)
        self.assertEqual(set(AudioUsageEvent.objects.values_list("visitor_id", flat=True)), {AnonymousVisitor.objects.get().pk})
        with override_settings(ANALYTICS_VISITOR_COOKIE=False):
            response = self.post(client=Client())
        self.assertNotIn(VISITOR_COOKIE, response.cookies)
        self.assertIsNone(AudioUsageEvent.objects.latest("pk").visitor)
        self.assertEqual(AnonymousVisitor.objects.count(), 1)

    def test_audio_ledger_has_no_fields_for_content_audio_or_ip(self):
        content_types = (models.TextField, models.JSONField, models.BinaryField, models.FileField,
                         models.GenericIPAddressField)
        for field in AudioUsageEvent._meta.get_fields():
            with self.subTest(field=field.name):
                self.assertNotIsInstance(field, content_types)
                self.assertFalse(any(word in field.name for word in ("text", "transcript", "sentence", "blob", "ip")))
