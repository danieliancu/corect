"""Writes one usage ledger row per validated submission. Never stores submitted or generated text."""
import logging
from decimal import Decimal

from django.conf import settings
from django.db import DatabaseError

from apps.analytics.models import AudioUsageEvent, UsageEvent
from apps.assistant.services.pricing import estimate_cost, estimate_speech_cost, estimate_transcription_cost
from apps.assistant.services.prompts import PROMPT_VERSION

logger = logging.getLogger("apps.analytics")
TOKEN_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens")


def _total(values):
    """Sum only when every provider call reported the value; otherwise the total is unknown."""
    return None if not values or None in values else sum(values)


def record_usage_event(*, request, kind, status, calls=(), error_code="", visitor=None, assistant_request=None,
                       auto_translated=False):
    user = request.user if request.user.is_authenticated else None
    if status == UsageEvent.Status.REJECTED:
        # Rejected before any provider call, so nothing was consumed.
        tokens, cost = dict.fromkeys(TOKEN_FIELDS, 0), Decimal(0)
    else:
        tokens = {field: _total([getattr(call, field) for call in calls]) for field in TOKEN_FIELDS}
        costs = [estimate_cost(call) for call in calls]
        cost = None if not costs or None in costs else sum(costs, Decimal(0))
    try:
        return UsageEvent.objects.create(
            audience=UsageEvent.Audience.REGISTERED if user else UsageEvent.Audience.ANONYMOUS,
            user=user, visitor=visitor, request_type=kind, auto_translated=auto_translated,
            model=settings.OPENAI_MODEL[:100], response_model=next((c.response_model for c in calls if c.response_model), ""),
            prompt_version=PROMPT_VERSION, status=status, error_code=error_code, provider_calls=len(calls),
            estimated_cost=cost, assistant_request=assistant_request, **tokens)
    except DatabaseError:
        logger.error("usage_event_unavailable")
        return None


def record_audio_event(*, operation, status, request=None, user=None, audience="", usage=None, error_code="",
                       visitor=None, speech_target="", usage_event_id=None, provider_called=False, model="", stt_mode="",
                       metering_source=""):
    """One audio ledger row per transcription or speech request (or live transcription session): usage, cost and
    identity only, never content. `request` identifies the user; without one (cleanup) pass `user` and `audience`."""
    if request is not None:
        user = request.user if request.user.is_authenticated else None
    speech = operation == AudioUsageEvent.Operation.SPEECH
    model = usage.model if usage else (model or (settings.OPENAI_TTS_MODEL if speech else settings.OPENAI_TRANSCRIBE_MODEL))
    voice = ((usage.voice if usage and usage.voice else settings.OPENAI_TTS_VOICE) if speech else "")
    stt_mode = "" if speech else (stt_mode or AudioUsageEvent.SttMode.FILE)
    if status == AudioUsageEvent.Status.REJECTED:
        # Rejected before any provider call, so nothing was consumed.
        tokens, seconds, cost = dict.fromkeys(("input_tokens", "output_tokens", "total_tokens"), 0), None, Decimal(0)
    else:
        tokens = {field: getattr(usage, field) if usage else None
                  for field in ("input_tokens", "output_tokens", "total_tokens")}
        seconds = usage.audio_seconds if usage else None
        cost = (estimate_speech_cost(usage) if speech else estimate_transcription_cost(usage)) if usage else None
    if seconds is None:
        metering_source = ""
    elif not metering_source:
        metering_source = AudioUsageEvent.MeteringSource.PROVIDER  # Read from the provider's response by the server.
    try:
        linked = usage_event_id if usage_event_id and UsageEvent.objects.filter(pk=usage_event_id).exists() else None
        return AudioUsageEvent.objects.create(
            operation=operation, audience=audience or (UsageEvent.Audience.REGISTERED if user else UsageEvent.Audience.ANONYMOUS),
            user=user, visitor=visitor, usage_event_id=linked, speech_target=speech_target if speech else "",
            model=model[:100], voice=voice[:40], status=status, error_code=error_code, stt_mode=stt_mode,
            provider_calls=1 if provider_called and status != AudioUsageEvent.Status.REJECTED else 0,
            audio_seconds=seconds, metering_source=metering_source, estimated_cost=cost, **tokens)
    except DatabaseError:
        logger.error("audio_usage_event_unavailable")
        return None
