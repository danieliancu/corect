"""One OpenAI client per process, shared by every request thread (text, moderation, learning and voice).

Creating a client per call opened a new connection pool each time, paying DNS, TCP and TLS set-up again. The SDK client
wraps a thread-safe httpx pool, so one instance serves Waitress's request threads and the moderation executor. Callers
never use it as a context manager: that would close the shared pool. Timeouts and the no-retry policy are unchanged.
"""
import threading

import httpx
from django.conf import settings
from openai import DefaultHttpxClient, OpenAI


class ProviderNotConfigured(Exception):
    """No OpenAI API key is configured."""


_lock = threading.Lock()
_slot: tuple[tuple, OpenAI] | None = None  # (settings key, client): a single slot, never a growing cache.


def _settings_key() -> tuple:
    # The class is part of the key so a test that patches OpenAI never receives a client built before the patch.
    return OpenAI, settings.OPENAI_API_KEY, settings.OPENAI_TIMEOUT, settings.OPENAI_KEEPALIVE_SECONDS


def _build() -> OpenAI:
    # httpx closes idle connections after 5 seconds by default, which would make reuse rare at low traffic.
    limits = httpx.Limits(max_connections=50, max_keepalive_connections=10,
                          keepalive_expiry=settings.OPENAI_KEEPALIVE_SECONDS)
    return OpenAI(api_key=settings.OPENAI_API_KEY, timeout=settings.OPENAI_TIMEOUT, max_retries=0,
                  http_client=DefaultHttpxClient(limits=limits, timeout=settings.OPENAI_TIMEOUT))


def openai_client() -> OpenAI:
    """The shared client for the current settings. Raises ProviderNotConfigured when there is no API key."""
    global _slot
    if not settings.OPENAI_API_KEY:
        raise ProviderNotConfigured
    key = _settings_key()
    slot = _slot
    if slot is not None and slot[0] == key:
        return slot[1]
    with _lock:
        if _slot is None or _slot[0] != key:
            # A replaced client is not closed here: another thread may still be using it. Settings only change in tests.
            _slot = (key, _build())
        return _slot[1]


def fresh_openai_client() -> OpenAI:
    """A new, unshared client (benchmarks compare it with the shared one). The caller must close it."""
    if not settings.OPENAI_API_KEY:
        raise ProviderNotConfigured
    return _build()


def reset_openai_client() -> None:
    """Closes and forgets the shared client (tests and benchmarks)."""
    global _slot
    with _lock:
        slot, _slot = _slot, None
    if slot is not None:
        try:
            slot[1].close()
        except Exception:  # noqa: BLE001 - a mock or an already-closed pool must not break the reset
            pass
