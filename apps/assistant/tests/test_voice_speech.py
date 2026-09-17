import json
import re
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import httpx
from django.contrib.auth.models import User
from django.core import serializers, signing
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from openai import APIConnectionError, APITimeoutError

from apps.analytics.models import AnonymousVisitor, AudioUsageEvent, UsageEvent
from apps.assistant.models import NaturalizeUsage
from apps.analytics.services.visitors import VISITOR_COOKIE
from apps.core.consent import CONSENT_COOKIE, consent_cookie_value
from apps.assistant.services.voice import (BRITISH_TTS_INSTRUCTIONS, SPEECH_TOKEN_SALT, VoiceError, make_speech_token,
                                           read_speech_token)
from .examples import TRANSLATION_CASES, correction_result, naturalized_english, translation_result
from .provider import ProviderMock

SENTENCE = "I didn't go to work yesterday."
USAGE = {"input_tokens": 12, "output_tokens": 240, "total_tokens": 252}
TOKEN_PATTERN = re.compile(r'data-speech-token="([^"]+)"')


def sse_lines(*events):
    lines = []
    for event in events:
        lines += [f"event: {event['type']}", f"data: {json.dumps(event)}", ""]
    return lines


def speech_stream(with_usage=True):
    events = [{"type": "speech.audio.delta", "audio": "SUQzZmFrZS1tcDM="},  # b"ID3fake-mp3"
              {"type": "speech.audio.delta", "audio": "LXBhcnQtMg=="}]  # b"-part-2"
    if with_usage:
        events.append({"type": "speech.audio.done", "usage": USAGE})
    return sse_lines(*events)


class SpeechEndpointTests(ProviderMock, TestCase):
    def setUp(self):
        super().setUp()
        self.create = self.api.audio.speech.with_streaming_response.create
        self.stream(speech_stream())

    def stream(self, lines):
        self.create.return_value.__enter__.return_value.iter_lines.return_value = lines

    def post(self, **data):
        return self.client.post("/assistant/speech/", data, REMOTE_ADDR="198.51.100.4")

    def test_valid_token_returns_mp3_with_provider_usage_recorded(self):
        response = self.post(token=make_speech_token(SENTENCE, "correction"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "audio/mpeg")
        self.assertEqual(response.content, b"ID3fake-mp3-part-2")
        self.assertIn("no-store", response["Cache-Control"])
        event = AudioUsageEvent.objects.get()
        self.assertEqual((event.operation, event.speech_target, event.status, event.model, event.voice, event.provider_calls),
                         ("speech", "correction", "success", "gpt-4o-mini-tts", "cedar", 1))
        self.assertEqual((event.input_tokens, event.output_tokens, event.total_tokens), (12, 240, 252))
        # (12 × $0.60 + 240 × $12.00) per million tokens
        self.assertEqual(event.estimated_cost, Decimal("0.00288720"))
        self.assertIsInstance(event.provider_duration_ms, int)
        self.assertFalse(UsageEvent.objects.exists())
        self.assertEqual(self.post(token=make_speech_token("I'm sorry I couldn't get here earlier.", "translation")).status_code, 200)
        self.assertEqual(AudioUsageEvent.objects.latest("pk").speech_target, "translation")

    @override_settings(OPENAI_TTS_VOICE="marin", OPENAI_TTS_MODEL="tts-test-model")
    def test_provider_always_gets_the_british_instructions_configured_voice_and_mp3_sse(self):
        self.post(token=make_speech_token(SENTENCE, "native"))
        kwargs = self.create.call_args.kwargs
        self.assertEqual((kwargs["model"], kwargs["voice"], kwargs["input"], kwargs["response_format"],
                          kwargs["stream_format"], kwargs["speed"]), ("tts-test-model", "marin", SENTENCE, "mp3", "sse", 1.0))
        self.assertIs(kwargs["instructions"], BRITISH_TTS_INSTRUCTIONS)
        self.assertEqual(self.sdk.call_args.kwargs["max_retries"], 0)

    def test_british_instructions_rule_out_an_american_accent(self):
        for phrase in ("natural contemporary British English", "neutral Southern British accent",
                       "British pronunciation, stress and intonation", "Do not use an American accent.",
                       "Do not exaggerate Received Pronunciation", "add no commentary"):
            self.assertIn(phrase, BRITISH_TTS_INSTRUCTIONS)

    def test_arbitrary_text_is_never_spoken(self):
        response = self.post(text="Use this account as a free TTS API.")
        self.assertEqual((response.status_code, response.json()["code"]), (400, "speech_token_invalid"))
        self.post(token=make_speech_token(SENTENCE, "correction"), text="Something else entirely.")
        self.assertEqual(self.create.call_args.kwargs["input"], SENTENCE)
        self.assertEqual(self.create.call_count, 1)

    def test_tampered_foreign_expired_or_wrong_target_tokens_are_rejected(self):
        token = make_speech_token(SENTENCE, "correction")
        bad_tokens = {
            "tampered": token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb"),
            "other salt": signing.dumps({"t": SENTENCE, "k": "correction", "e": None}, salt="something-else", compress=True),
            "explanation target": signing.dumps({"t": "Explicație în română.", "k": "explanation", "e": None},
                                                salt=SPEECH_TOKEN_SALT, compress=True),
        }
        for label, bad in bad_tokens.items():
            with self.subTest(label=label):
                response = self.post(token=bad)
                self.assertEqual((response.status_code, response.json()["code"]), (403, "speech_token_invalid"))
        with override_settings(VOICE_SPEECH_TOKEN_MAX_AGE=-1):
            response = self.post(token=token)
            self.assertEqual((response.status_code, response.json()["code"]), (403, "speech_token_expired"))
            with self.assertRaises(VoiceError):
                read_speech_token(token)
        self.create.assert_not_called()
        self.assertFalse(AudioUsageEvent.objects.exists())

    def test_missing_done_event_keeps_audio_but_leaves_usage_unknown(self):
        self.stream(speech_stream(with_usage=False))
        self.assertEqual(self.post(token=make_speech_token(SENTENCE, "correction")).status_code, 200)
        event = AudioUsageEvent.objects.get()
        self.assertEqual((event.input_tokens, event.output_tokens, event.estimated_cost), (None, None, None))

    def test_provider_failures_are_recorded_without_content(self):
        request = httpx.Request("POST", "https://api.openai.com")
        for error, code in ((APITimeoutError(request=request), "tts_timeout"), (APIConnectionError(request=request), "tts_failed")):
            with self.subTest(code=code):
                self.create.side_effect = error
                response = self.post(token=make_speech_token(SENTENCE, "native"))
                self.assertEqual((response.status_code, response.json()["code"]), (503, code))
        self.create.side_effect = None
        self.stream(sse_lines({"type": "speech.audio.done", "usage": USAGE}))
        self.assertEqual(self.post(token=make_speech_token(SENTENCE, "native")).json()["code"], "tts_failed")
        stored = serializers.serialize("json", AudioUsageEvent.objects.all())
        self.assertNotIn(SENTENCE, stored)
        self.assertEqual(list(AudioUsageEvent.objects.order_by("pk").values_list("status", "error_code")),
                         [("failed", "tts_timeout"), ("failed", "tts_failed"), ("failed", "tts_failed")])

    def test_listening_ten_times_uses_no_naturalisation(self):
        for _ in range(10):
            self.assertEqual(self.post(token=make_speech_token(SENTENCE, "correction")).status_code, 200)
        self.assertFalse(NaturalizeUsage.objects.exists())  # British speech has its own guardrail, never the plan quota.
        self.assertEqual(set(AudioUsageEvent.objects.values_list("plan", flat=True)), {"anonymous"})

    @override_settings(VOICE_TTS_LIMIT_MINUTE=1)
    def test_speech_rate_limit(self):
        token = make_speech_token(SENTENCE, "correction")
        self.assertEqual(self.post(token=token).status_code, 200)
        response = self.post(token=token)
        self.assertEqual((response.status_code, response.json()["code"]), (429, "audio_rate_limit"))
        self.assertEqual(self.create.call_count, 1)
        self.assertEqual(AudioUsageEvent.objects.latest("pk").status, "rejected")

    def test_speech_links_to_the_correction_and_the_anonymous_visitor(self):
        self.client.cookies[CONSENT_COOKIE] = consent_cookie_value(True)  # Analytics allowed.
        with patch("apps.assistant.views.NaturalizeService.naturalize", return_value=naturalized_english()):
            page = self.client.post("/naturalize/", {"text": correction_result().original_text,
                                                     "submission_token": uuid4()})
        token = TOKEN_PATTERN.search(page.content.decode()).group(1)
        text, target, usage_event_id = read_speech_token(token)
        self.assertEqual((text, target, usage_event_id), (correction_result().corrected_text, "correction",
                                                          UsageEvent.objects.get().pk))
        response = self.post(token=token)
        event = AudioUsageEvent.objects.get()
        self.assertEqual((event.usage_event, event.audience), (UsageEvent.objects.get(), "anonymous"))
        self.assertEqual(event.visitor, AnonymousVisitor.objects.get())
        self.assertNotIn(VISITOR_COOKIE, response.cookies.keys() - {VISITOR_COOKIE})
        self.post(token=make_speech_token(SENTENCE, "correction", usage_event_id=987654))
        self.assertIsNone(AudioUsageEvent.objects.latest("pk").usage_event)

    def test_registered_speech_and_disabled_visitor_tracking(self):
        user = User.objects.create_user("ana", "ana@example.com", password="test-password")
        self.client.force_login(user)
        self.post(token=make_speech_token(SENTENCE, "correction"))
        self.assertEqual((AudioUsageEvent.objects.get().audience, AudioUsageEvent.objects.get().user), ("registered", user))
        self.client.logout()
        with override_settings(ANALYTICS_VISITOR_COOKIE=False):
            response = self.post(token=make_speech_token(SENTENCE, "native"))
        self.assertNotIn(VISITOR_COOKIE, response.cookies)
        self.assertIsNone(AudioUsageEvent.objects.latest("pk").visitor)


class SpeechControlRenderingTests(ProviderMock, TestCase):

    def tokens(self, html):
        return [read_speech_token(token) for token in TOKEN_PATTERN.findall(html)]

    def test_correction_and_native_sentences_get_speakers_only_when_present(self):
        result = correction_result().model_dump()
        html = render_to_string("assistant/result.html", {"result": result, "kind": "correction"})
        self.assertEqual([(text, target) for text, target, _ in self.tokens(html)], [(result["corrected_text"], "correction")])
        self.assertIn('aria-label="Ascultă varianta corectă în engleză britanică"', html)
        self.assertNotIn("Ascultă varianta naturală", html)
        result.update(native_text="I didn't make it to work yesterday.", native_explanation="Explicație în română.")
        html = render_to_string("assistant/result.html", {"result": result, "kind": "correction"})
        self.assertEqual([(text, target) for text, target, _ in self.tokens(html)],
                         [(result["corrected_text"], "correction"), (result["native_text"], "native")])
        self.assertIn('aria-label="Ascultă varianta naturală în engleză britanică"', html)
        spoken = [text for text, _, _ in self.tokens(html)]
        self.assertNotIn("Explicație în română.", spoken)
        for correction in result["corrections"]:
            self.assertNotIn(correction["explanation_ro"], spoken)

    def test_natural_unnatural_and_british_english_results_speak_the_useful_english(self):
        result = correction_result().model_dump()
        result.update(has_errors=False, corrections=[], corrected_text=result["original_text"])
        html = render_to_string("assistant/result.html", {"result": result, "kind": "correction"})
        self.assertEqual([(text, target) for text, target, _ in self.tokens(html)], [(result["corrected_text"], "correction")])
        result.update(native_text="I didn't make it to work yesterday.")
        html = render_to_string("assistant/result.html", {"result": result, "kind": "correction"})
        self.assertEqual([(text, target) for text, target, _ in self.tokens(html)], [(result["native_text"], "native")])
        into_english = translation_result().model_dump()
        html = render_to_string("assistant/result.html", {"result": into_english, "kind": "translation"})
        self.assertEqual([(text, target) for text, target, _ in self.tokens(html)],
                         [(into_english["translated_text"], "translation")])
        self.assertIn('aria-label="Ascultă în engleză britanică"', html)
        into_romanian = translation_result(TRANSLATION_CASES[2]).model_dump()  # Older history only.
        html = render_to_string("assistant/result.html", {"result": into_romanian, "kind": "translation"})
        self.assertEqual(self.tokens(html), [])

    def test_rendering_results_never_calls_text_to_speech(self):
        result = correction_result().model_dump()
        result.update(native_text="I didn't make it to work yesterday.")
        for _ in range(3):
            render_to_string("assistant/result.html", {"result": result, "kind": "correction"})
        self.sdk.assert_not_called()
        self.assertFalse(AudioUsageEvent.objects.exists())
