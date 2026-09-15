"""Writes usage ledger rows: one per validated submission, voice call or learning AI call. Never stores content."""
import logging
from decimal import Decimal

from django.conf import settings
from django.db import DatabaseError

from apps.analytics.models import AudioUsageEvent, LearningUsageEvent, UsageEvent
from apps.assistant.services.pricing import estimate_cost, estimate_speech_cost, estimate_transcription_cost
from apps.assistant.services.prompts import POLITE_PROMPT_VERSION, PROMPT_VERSION
from apps.assistant.services.timing import bounded_ms
from apps.core.plans import ANONYMOUS, tier_for

logger = logging.getLogger("apps.analytics")
TOKEN_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens")
VOICE_TIMING_FIELDS = ("mic_ms", "session_ms", "connect_ms", "startup_ms", "first_word_ms", "finalise_ms")


def _total(values):
    """Sum only when every provider call reported the value; otherwise the total is unknown."""
    return None if not values or None in values else sum(values)


def _usage_totals(calls, status):
    """Token totals and USD cost of the reported provider calls (zero when rejected before any call)."""
    if status == UsageEvent.Status.REJECTED:
        return dict.fromkeys(TOKEN_FIELDS, 0), Decimal(0)
    tokens = {field: _total([getattr(call, field) for call in calls]) for field in TOKEN_FIELDS}
    costs = [estimate_cost(call) for call in calls]
    return tokens, None if not costs or None in costs else sum(costs, Decimal(0))


def record_usage_event(*, request, kind, status, calls=(), error_code="", visitor=None, assistant_request=None,
                       source_language="", duration_ms=None, moderation_ms=None, plan="", polite=False):
    """`kind` is the effective operation (correction, translation or unclassified), never the public action. `plan` is the
    tier at request time (anonymous, free or pro), kept even if the account changes plan later."""
    user = request.user if request.user.is_authenticated else None
    tokens, cost = _usage_totals(calls, status)
    try:
        return UsageEvent.objects.create(
            audience=UsageEvent.Audience.REGISTERED if user else UsageEvent.Audience.ANONYMOUS,
            plan=plan or tier_for(request.user), polite=polite,
            user=user, visitor=visitor, request_type=kind, source_language=(source_language or "")[:12],
            model=settings.OPENAI_MODEL[:100], response_model=next((c.response_model for c in calls if c.response_model), ""),
            prompt_version=POLITE_PROMPT_VERSION if polite else PROMPT_VERSION, status=status, error_code=error_code, provider_calls=len(calls),
            estimated_cost=cost, assistant_request=assistant_request, duration_ms=bounded_ms(duration_ms),
            provider_duration_ms=bounded_ms(_total([getattr(call, "duration_ms", None) for call in calls])),
            moderation_duration_ms=bounded_ms(moderation_ms), **tokens)
    except DatabaseError:
        logger.error("usage_event_unavailable")
        return None


def record_learning_event(*, user, feature, status, calls=(), error_code="", prompt_version="", model="", visitor=None):
    """One learning AI ledger row per call: identity, feature, model, prompt version, tokens and cost only."""
    user = user if user is not None and user.is_authenticated else None
    tokens, cost = _usage_totals(calls, status)
    try:
        return LearningUsageEvent.objects.create(
            audience=UsageEvent.Audience.REGISTERED if user else UsageEvent.Audience.ANONYMOUS, user=user,
            visitor=visitor, feature=feature, model=(model or settings.OPENAI_LEARNING_MODEL)[:100],
            response_model=next((c.response_model for c in calls if c.response_model), ""),
            prompt_version=prompt_version[:40], status=status, error_code=error_code[:40], provider_calls=len(calls),
            estimated_cost=cost, **tokens)
    except DatabaseError:
        logger.error("learning_usage_event_unavailable")
        return None


def _voice_timings(timings) -> dict:
    """Only the known live-transcription timing fields, each a bounded whole number (or a boolean for final_received)."""
    if not timings:
        return {}
    values = {field: bounded_ms(timings.get(field)) for field in VOICE_TIMING_FIELDS if field in timings}
    if isinstance(timings.get("final_received"), bool):
        values["final_received"] = timings["final_received"]
    return {field: value for field, value in values.items() if value is not None}


def record_audio_event(*, operation, status, request=None, user=None, audience="", usage=None, error_code="",
                       visitor=None, speech_target="", usage_event_id=None, provider_called=False, model="", stt_mode="",
                       metering_source="", timings=None, plan=""):
    """One audio ledger row per transcription or speech request (or live transcription session): usage, cost, identity
    and timings only, never content. `request` identifies the user; without one (cleanup) pass `user` and `audience`.
    `plan` is the tier at request time; for a live session closed later (finish or cleanup) it is the tier when the
    session was accounted for."""
    if request is not None:
        user = request.user if request.user.is_authenticated else None
        plan = plan or tier_for(request.user)
    elif not plan:
        plan = tier_for(user) if user is not None else (ANONYMOUS if audience == UsageEvent.Audience.ANONYMOUS else "")
    speech = operation == AudioUsageEvent.Operation.SPEECH
    model = usage.model if usage else (model or (settings.OPENAI_TTS_MODEL if speech else settings.OPENAI_TRANSCRIBE_MODEL))
    voice = ((usage.voice if usage and usage.voice else settings.OPENAI_TTS_VOICE) if speech else "")
    stt_mode = "" if speech else (stt_mode or AudioUsageEvent.SttMode.FILE)
    if status == AudioUsageEvent.Status.REJECTED:
        # Rejected before any provider call, so nothing was consumed.
        tokens, seconds, cost = dict.fromkeys(("input_tokens", "output_tokens", "total_tokens"), 0), None, Decimal(0)
        provider_ms = None
    else:
        tokens = {field: getattr(usage, field) if usage else None
                  for field in ("input_tokens", "output_tokens", "total_tokens")}
        seconds = usage.audio_seconds if usage else None
        cost = (estimate_speech_cost(usage) if speech else estimate_transcription_cost(usage)) if usage else None
        provider_ms = bounded_ms(getattr(usage, "duration_ms", None)) if usage else None
    if seconds is None:
        metering_source = ""
    elif not metering_source:
        metering_source = AudioUsageEvent.MeteringSource.PROVIDER  # Read from the provider's response by the server.
    try:
        linked = usage_event_id if usage_event_id and UsageEvent.objects.filter(pk=usage_event_id).exists() else None
        return AudioUsageEvent.objects.create(
            operation=operation, audience=audience or (UsageEvent.Audience.REGISTERED if user else UsageEvent.Audience.ANONYMOUS),
            plan=plan, user=user, visitor=visitor, usage_event_id=linked, speech_target=speech_target if speech else "",
            model=model[:100], voice=voice[:40], status=status, error_code=error_code, stt_mode=stt_mode,
            provider_calls=1 if provider_called and status != AudioUsageEvent.Status.REJECTED else 0,
            audio_seconds=seconds, metering_source=metering_source, estimated_cost=cost, provider_duration_ms=provider_ms,
            **_voice_timings(timings), **tokens)
    except DatabaseError:
        logger.error("audio_usage_event_unavailable")
        return None
