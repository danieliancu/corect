"""The shared OpenAI client: one per process and settings, reused across requests and threads, never closed by a call."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import httpx
from django.conf import settings
from django.test import SimpleTestCase, override_settings

from apps.assistant.services import provider
from apps.assistant.services.naturalize import NaturalizeService
from .examples import CORRECTION_CASES, english_raw
from .provider import ProviderMock


class SharedClientTests(ProviderMock, SimpleTestCase):
    def test_one_client_is_shared_across_calls_and_threads(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            clients = list(pool.map(lambda _: provider.openai_client(), range(32)))
        self.assertTrue(all(client is self.api for client in clients))
        self.sdk.assert_called_once()
        kwargs = self.sdk.call_args.kwargs
        self.assertEqual((kwargs["api_key"], kwargs["timeout"], kwargs["max_retries"]),
                         (settings.OPENAI_API_KEY, settings.OPENAI_TIMEOUT, 0))
        self.assertIsInstance(kwargs["http_client"], httpx.Client)

    def test_changed_settings_build_a_new_client(self):
        provider.openai_client()
        with override_settings(OPENAI_TIMEOUT=5.0):
            provider.openai_client()
        self.assertEqual(self.sdk.call_count, 2)
        self.assertEqual(self.sdk.call_args.kwargs["timeout"], 5.0)

    def test_reset_closes_the_client(self):
        client = provider.openai_client()
        provider.reset_openai_client()
        client.close.assert_called_once()
        provider.openai_client()
        self.assertEqual(self.sdk.call_count, 2)

    @override_settings(OPENAI_API_KEY="")
    def test_a_missing_key_builds_nothing(self):
        for build in (provider.openai_client, provider.fresh_openai_client):
            with self.subTest(build=build.__name__), self.assertRaises(provider.ProviderNotConfigured):
                build()
        self.sdk.assert_not_called()

    def test_services_never_close_the_shared_client(self):
        self.respond(english_raw())
        for _ in range(3):
            NaturalizeService().naturalize(CORRECTION_CASES[0][0])
        self.api.__enter__.assert_not_called()
        self.api.close.assert_not_called()
        self.sdk.assert_called_once()

    def test_a_patched_sdk_never_receives_a_client_built_before_the_patch(self):
        first = provider.openai_client()
        with patch("apps.assistant.services.provider.OpenAI") as other:
            self.assertIs(provider.openai_client(), other.return_value)
        self.assertIsNot(first, other.return_value)
