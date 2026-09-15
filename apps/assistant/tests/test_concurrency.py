from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings

from apps.assistant.models import NaturalizeUsage, RateBucket, SubmissionClaim
from apps.assistant.services import quota
from apps.assistant.services.limits import claim_submission, claim_voice
from apps.assistant.services.localday import local_day
from apps.assistant.services.openai_client import AssistantError

LIMITS = {"anonymous": 1, "free": 2, "pro": 3}


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
        results = self.compete(range(4), claim=lambda _: claim_voice("user:voice-test", "transcription", "pro"))
        self.assertCountEqual(results, ["accepted", "accepted", "audio_rate_limit", "audio_rate_limit"])
        self.assertEqual(sorted(RateBucket.objects.values_list("count", flat=True)), [2, 2])

    @override_settings(VOICE_TRANSCRIBE_LIMIT_MINUTE=5,
                       VOICE_DAILY_GUARDRAILS={"transcription": {"anonymous": 1, "free": 1, "pro": 1},
                                               "speech": {"anonymous": 1, "free": 1, "pro": 1}})
    def test_a_full_day_guardrail_leaves_the_minute_count_unchanged(self):
        claim_voice("user:window-test", "transcription", "free")
        with self.assertRaises(AssistantError):
            claim_voice("user:window-test", "transcription", "free")
        self.assertEqual(sorted(RateBucket.objects.values_list("count", flat=True)), [1, 1])

    def test_same_token_only_accepted_once(self):
        token = uuid4()
        self.assertCountEqual(self.compete([token, token]), ["accepted", "duplicate"])
        self.assertEqual(SubmissionClaim.objects.count(), 1)
        self.assertEqual(list(RateBucket.objects.values_list("count", flat=True)), [1])  # The minute window only.

    @override_settings(NATURALIZE_RATE_LIMIT_MINUTE=1)
    def test_concurrent_requests_cannot_exceed_the_minute_rate_limit(self):
        self.assertCountEqual(self.compete([uuid4(), uuid4()]), ["accepted", "rate_limit"])

    @override_settings(NATURALIZE_DAILY_LIMITS=LIMITS)
    def test_the_last_daily_use_goes_to_exactly_one_request(self):
        actor = "user:last-slot"
        NaturalizeUsage.objects.create(actor=actor, day=local_day(), used=1)  # Free: 1 of 2 used.
        results = self.compete(range(6), claim=lambda _: quota.reserve(actor, "free"))
        self.assertCountEqual(results, ["accepted"] + ["quota_exhausted"] * 5)
        usage = NaturalizeUsage.objects.get(actor=actor)
        self.assertEqual((usage.used, usage.reserved), (1, 1))

    @override_settings(NATURALIZE_DAILY_LIMITS=LIMITS)
    def test_concurrent_commits_and_releases_keep_the_count_exact(self):
        actor = "user:settle"
        reservations = [quota.reserve(actor, "pro") for _ in range(3)]
        actions = [quota.commit, quota.release, quota.release]
        self.compete(range(3), claim=lambda index: actions[index](reservations[index]))
        usage = NaturalizeUsage.objects.get(actor=actor)
        self.assertEqual((usage.used, usage.reserved), (1, 0))
        quota.release(reservations[1])  # A second release of the same reservation never goes below zero.
        self.assertEqual(NaturalizeUsage.objects.get(actor=actor).reserved, 0)
