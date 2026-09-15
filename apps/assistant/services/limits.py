"""RATE LIMITS / ABUSE GUARDRAILS: technical limits against bursts, bots and provider-cost attacks.

These are never the plan quota (quota.py). The per-minute window is a fixed 60-second window; daily guardrails use the
London calendar day (localday.py).
"""
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.crypto import salted_hmac

from apps.assistant.models import RateBucket, SubmissionClaim
from .localday import local_day, next_reset, seconds_until_reset
from .openai_client import AssistantError

RATE_LIMIT_MESSAGE = "Ai trimis multe cereri într-un timp scurt. Încearcă din nou peste un minut."
VOICE_LIMIT_MESSAGE = "Ai atins limita pentru voce deocamdată. Încearcă din nou mai târziu."
MINUTE, DAY = 60, "day"
# Voice operations count in their own namespaced buckets: the microphone and British speech never touch the plan quota.
VOICE_GUARDRAILS = {"transcription": ("voice-stt", "VOICE_TRANSCRIBE_LIMIT_MINUTE"),
                    "speech": ("voice-tts", "VOICE_TTS_LIMIT_MINUTE")}


def actor_key(request) -> str:
    if request.user.is_authenticated:
        return f"user:{request.user.pk}"
    # REMOTE_ADDR only: do not trust client-supplied forwarding headers.
    return "anon:" + salted_hmac("assistant-rate", request.META.get("REMOTE_ADDR", "unknown")).hexdigest()


def consume_quota(key_prefix: str, windows, code: str, message: str) -> None:
    """Counts one request in every (MINUTE or DAY, limit) window, raising once any window is full. Call inside a
    transaction."""
    now = timezone.now()
    for window, limit in windows:
        if window == DAY:
            key, expires_at, retry_after = f"{key_prefix}:day:{local_day(now).isoformat()}", next_reset(now), \
                seconds_until_reset(now)
        else:
            number = int(now.timestamp()) // window
            key, expires_at = f"{key_prefix}:{window}:{number}", now + timedelta(seconds=window)
            retry_after = max(1, (number + 1) * window - int(now.timestamp()))
        # One conditional UPDATE counts the request only while the window has room. The database locks and re-checks
        # the row, so concurrent requests can never pass the limit; the enclosing transaction rolls every window back
        # when a later one is full.
        if RateBucket.objects.filter(key=key, count__lt=limit).update(count=F("count") + 1):
            continue
        bucket, _ = RateBucket.objects.get_or_create(key=key, defaults={"expires_at": expires_at})
        if not RateBucket.objects.filter(pk=bucket.pk, count__lt=limit).update(count=F("count") + 1):
            raise AssistantError(code, message, retry_after=retry_after)


@transaction.atomic
def claim_submission(actor: str, token) -> None:
    """The per-minute guardrail and duplicate protection for /naturalize/. The daily allowance is the plan quota."""
    consume_quota(actor, [(MINUTE, settings.NATURALIZE_RATE_LIMIT_MINUTE)], "rate_limit", RATE_LIMIT_MESSAGE)
    try:
        with transaction.atomic():
            SubmissionClaim.objects.create(actor=actor, token=token)
    except IntegrityError:
        raise AssistantError("duplicate", "Cererea a fost deja trimisă. Încearcă din nou după ce se termină.") from None


@transaction.atomic
def claim_voice(actor: str, operation: str, tier: str) -> None:
    """Per-minute and per-day guardrails for one transcription or British speech call; the daily ceiling depends on the
    plan tier so voice never blocks legitimate use within a plan."""
    prefix, per_minute = VOICE_GUARDRAILS[operation]
    consume_quota(f"{prefix}:{actor}", [(MINUTE, getattr(settings, per_minute)),
                                        (DAY, settings.VOICE_DAILY_GUARDRAILS[operation][tier])],
                  "audio_rate_limit", VOICE_LIMIT_MESSAGE)
