from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.crypto import salted_hmac

from apps.assistant.models import RateBucket, SubmissionClaim
from .openai_client import AssistantError

RATE_LIMIT_MESSAGE = "Ai atins limita de cereri. Încearcă din nou mai târziu."
VOICE_LIMIT_MESSAGE = "Ai atins limita pentru voce deocamdată. Încearcă din nou mai târziu."
# Voice operations count in their own namespaced buckets, so they never use up the text quota.
VOICE_QUOTAS = {
    "transcription": ("voice-stt", "VOICE_TRANSCRIBE_LIMIT_MINUTE", "VOICE_TRANSCRIBE_LIMIT_DAY"),
    "speech": ("voice-tts", "VOICE_TTS_LIMIT_MINUTE", "VOICE_TTS_LIMIT_DAY"),
}


def actor_key(request) -> str:
    if request.user.is_authenticated:
        return f"user:{request.user.pk}"
    # REMOTE_ADDR only: do not trust client-supplied forwarding headers.
    return "anon:" + salted_hmac("assistant-rate", request.META.get("REMOTE_ADDR", "unknown")).hexdigest()


def consume_quota(key_prefix: str, windows, code: str, message: str) -> None:
    """Counts one request in every (seconds, limit) window, raising once any window is full. Call inside a transaction."""
    now = timezone.now()
    for seconds, limit in windows:
        window = int(now.timestamp()) // seconds
        key = f"{key_prefix}:{seconds}:{window}"
        # One conditional UPDATE counts the request only while the window has room. The database locks and re-checks
        # the row, so concurrent requests can never pass the limit; the enclosing transaction rolls every window back
        # when a later one is full.
        if RateBucket.objects.filter(key=key, count__lt=limit).update(count=F("count") + 1):
            continue
        bucket, _ = RateBucket.objects.get_or_create(key=key, defaults={"expires_at": now + timedelta(seconds=seconds)})
        if not RateBucket.objects.filter(pk=bucket.pk, count__lt=limit).update(count=F("count") + 1):
            retry_after = max(1, (window + 1) * seconds - int(now.timestamp()))
            raise AssistantError(code, message, retry_after=retry_after)


@transaction.atomic
def claim_submission(actor: str, token) -> None:
    consume_quota(actor, [(60, settings.RATE_LIMIT_MINUTE), (86400, settings.RATE_LIMIT_DAY)], "rate_limit",
                  RATE_LIMIT_MESSAGE)
    try:
        with transaction.atomic():
            SubmissionClaim.objects.create(actor=actor, token=token)
    except IntegrityError:
        raise AssistantError("duplicate", "Cererea a fost deja trimisă. Încearcă din nou după ce se termină.") from None


@transaction.atomic
def claim_voice(actor: str, operation: str) -> None:
    prefix, per_minute, per_day = VOICE_QUOTAS[operation]
    consume_quota(f"{prefix}:{actor}", [(60, getattr(settings, per_minute)), (86400, getattr(settings, per_day))],
                  "audio_rate_limit", VOICE_LIMIT_MESSAGE)
