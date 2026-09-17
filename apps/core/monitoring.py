"""Operational log events: a fixed event name, a category and short codes. Never user text, transcripts, prompts,
provider payloads, keys, emails or IP addresses.

Categories let logs, alerts and Sentry tell apart the problems that need different people:
- application: a bug or an unexpected exception in our code;
- database: PostgreSQL unreachable or failing;
- provider: OpenAI unavailable, slow or returning unusable output;
- quota: plan quotas and rate limits doing their job (a spike means abuse or a limit set too low);
- guardrail: requests refused on purpose (moderation, language, invalid input, suspended accounts).
"""
import contextvars
import logging

from django.db import DatabaseError

APPLICATION, DATABASE, PROVIDER, QUOTA, GUARDRAIL = "application", "database", "provider", "quota", "guardrail"
CATEGORIES = (APPLICATION, DATABASE, PROVIDER, QUOTA, GUARDRAIL)

QUOTA_CODES = frozenset({"quota_exhausted", "rate_limit", "audio_rate_limit", "duplicate", "learning_rate_limit",
                         "learning_quota_exhausted", "learning_budget_exhausted"})
GUARDRAIL_CODES = frozenset({
    "account_suspended", "content_blocked", "instruction_attempt", "language", "empty", "too_long",
    "audio_too_large", "unsupported_audio", "microphone_upload_invalid", "transcript_too_long", "transcription_empty",
    "realtime_session_expired", "realtime_session_invalid", "speech_token_expired", "speech_token_invalid"})
PROVIDER_CODES = frozenset({
    "not_configured", "timeout", "connection_error", "invalid_or_failed_response", "incomplete", "refused",
    "missing_output", "moderation_unavailable", "provider_error", "invalid_correction", "invalid_snippet",
    "invalid_translation", "invalid_output", "realtime_timeout", "realtime_unavailable", "transcription_failed",
    "transcription_timeout", "tts_failed", "tts_timeout", "voice_not_configured"})
DATABASE_CODES = frozenset({"database_unavailable"})

# Set by apps.core.middleware.RequestContextMiddleware for the duration of one request.
request_context = contextvars.ContextVar("request_context", default=None)


def category_for_code(code) -> str:
    for category, codes in ((QUOTA, QUOTA_CODES), (GUARDRAIL, GUARDRAIL_CODES), (PROVIDER, PROVIDER_CODES),
                            (DATABASE, DATABASE_CODES)):
        if code in codes:
            return category
    return APPLICATION


def category_for_exception(exc) -> str:
    if isinstance(exc, DatabaseError):
        return DATABASE
    module = type(exc).__module__ or ""
    if module.startswith(("openai", "httpx", "httpcore")):
        return PROVIDER
    return APPLICATION


def log_event(logger, level, event, category, **fields):
    """Logs "event key=value ..." with the category attached. Values must be short codes, never content."""
    message = " ".join([event, *(f"{key}={value}" for key, value in fields.items())])
    logger.log(level, message, extra={"category": category, "event": event})


def log_failure(logger, event, code, **fields):
    """A failed request with a known error code: quota and guardrail refusals are INFO-worthy, the rest WARNING."""
    category = category_for_code(code)
    level = logging.INFO if category in (QUOTA, GUARDRAIL) else logging.WARNING
    log_event(logger, level, event, category, code=code, **fields)


def set_error_category(category):
    """Tags the current error for Sentry when it is installed (apps/core/sentry.py); a no-op otherwise."""
    try:
        import sentry_sdk
    except ImportError:
        return
    sentry_sdk.set_tag("category", category)
