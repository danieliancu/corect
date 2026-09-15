from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings

from apps.assistant.models import RateBucket, SubmissionClaim
from apps.assistant.services.limits import claim_submission, claim_voice
from apps.assistant.services.openai_client import AssistantError


class ConcurrencyTests(TransactionTestCase):
    def compete(self, tokens, claim=None):
        barrier = Barrier(len(tokens))
        claim = claim or (lambda token: claim_submission("user:concurrency-test", token))

        def worker(token):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                claim(token)
                return "accepted"
            except AssistantError as error:
                return error.code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(tokens)) as pool:
            return list(pool.map(worker, tokens))

    @override_settings(VOICE_TRANSCRIBE_LIMIT_MINUTE=2)
    def test_concurrent_voice_sessions_cannot_exceed_the_voice_limit(self):
        results = self.compete(range(4), claim=lambda _: claim_voice("user:voice-test", "transcription"))
        self.assertCountEqual(results, ["accepted", "accepted", "audio_rate_limit", "audio_rate_limit"])
        self.assertEqual(sorted(RateBucket.objects.values_list("count", flat=True)), [2, 2])

    @override_settings(RATE_LIMIT_MINUTE=5, RATE_LIMIT_DAY=1)
    def test_a_full_day_window_leaves_the_minute_count_unchanged(self):
        claim_submission("user:window-test", uuid4())
        with self.assertRaises(AssistantError):
            claim_submission("user:window-test", uuid4())
        self.assertEqual(sorted(RateBucket.objects.values_list("count", flat=True)), [1, 1])

    def test_same_token_only_accepted_once(self):
        token = uuid4()
        self.assertCountEqual(self.compete([token, token]), ["accepted", "duplicate"])
        self.assertEqual(SubmissionClaim.objects.count(), 1)
        self.assertEqual(list(RateBucket.objects.values_list("count", flat=True)), [1, 1])

    @override_settings(RATE_LIMIT_MINUTE=1)
    def test_concurrent_requests_cannot_exceed_limit(self):
        self.assertCountEqual(self.compete([uuid4(), uuid4()]), ["accepted", "rate_limit"])
