from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings

from apps.assistant.models import RateBucket, SubmissionClaim
from apps.assistant.services.limits import claim_submission
from apps.assistant.services.openai_client import AssistantError


class ConcurrencyTests(TransactionTestCase):
    def compete(self, tokens):
        barrier = Barrier(len(tokens))

        def worker(token):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                claim_submission("user:concurrency-test", token)
                return "accepted"
            except AssistantError as error:
                return error.code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(tokens)) as pool:
            return list(pool.map(worker, tokens))

    def test_same_token_only_accepted_once(self):
        token = uuid4()
        self.assertCountEqual(self.compete([token, token]), ["accepted", "duplicate"])
        self.assertEqual(SubmissionClaim.objects.count(), 1)
        self.assertEqual(list(RateBucket.objects.values_list("count", flat=True)), [1, 1])

    @override_settings(RATE_LIMIT_MINUTE=1)
    def test_concurrent_requests_cannot_exceed_limit(self):
        self.assertCountEqual(self.compete([uuid4(), uuid4()]), ["accepted", "rate_limit"])
