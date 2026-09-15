"""Test doubles for the process-wide OpenAI client (apps/assistant/services/provider.py)."""
from types import SimpleNamespace
from unittest.mock import patch

from apps.assistant.services.provider import reset_openai_client

USAGE = SimpleNamespace(input_tokens=1389, input_tokens_details=SimpleNamespace(cached_tokens=1386), output_tokens=2341,
                        output_tokens_details=SimpleNamespace(reasoning_tokens=967), total_tokens=3730)


class ProviderMock:
    """Patches the SDK class behind the shared client. `self.sdk` is the class; `self.api` is the one client every service
    receives, never entered as a context manager."""

    def setUp(self):
        super().setUp()
        reset_openai_client()
        self.sdk = patch("apps.assistant.services.provider.OpenAI").start()
        self.addCleanup(patch.stopall)
        self.addCleanup(reset_openai_client)
        self.api = self.sdk.return_value

    def respond(self, parsed, **overrides):
        self.api.responses.parse.return_value = completed(parsed, **overrides)


def completed(parsed, **overrides):
    values = dict(status="completed", output=[], output_parsed=parsed, usage=None, model="test-model")
    values.update(overrides)
    return SimpleNamespace(**values)


def moderation(flagged=False, **categories):
    return SimpleNamespace(results=[SimpleNamespace(flagged=flagged, categories=SimpleNamespace(**categories))])
