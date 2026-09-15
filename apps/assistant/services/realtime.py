"""Accounting for live transcription sessions: one audio ledger row per session, never audio or transcript text.

The browser streams audio straight to OpenAI, so Django only sees the session start and its end. The provider's
duration reaches us through the browser, so it is accepted only within the window the server observed itself.
"""
import re
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone

from apps.analytics.models import AudioUsageEvent, UsageEvent
from apps.analytics.services.recording import record_audio_event
from ..models import RealtimeTranscriptionSession
from .timing import MAX_MS
from .voice import VOICE_UNAVAILABLE, AudioUsage, VoiceError

SESSION_SALT = "corect.realtime.v1"
FINISH_GRACE_SECONDS = 120  # Connecting, the final transcript and a slow network, on top of VOICE_MAX_SECONDS.
METER_SLACK_SECONDS = 10  # The provider counts from connection to commit and rounds up to whole seconds.
REPORT_TOLERANCE = Decimal(2)
SECONDS = Decimal("0.01")
# How the browser says the session ended -> (ledger status, error code).
OUTCOMES = {
    "completed": (AudioUsageEvent.Status.SUCCESS, ""),
    "limit": (AudioUsageEvent.Status.SUCCESS, ""),
    "interrupted": (AudioUsageEvent.Status.FAILED, "realtime_interrupted"),
    "page_closed": (AudioUsageEvent.Status.FAILED, "realtime_page_closed"),
    "connect_failed": (AudioUsageEvent.Status.FAILED, "realtime_connect_failed"),
}
LIVE = AudioUsageEvent.SttMode.REALTIME
Session = RealtimeTranscriptionSession
# Browser-measured live transcription timings (static/js/voice.js). Numbers only: they never affect billing.
TIMING_FIELDS = ("mic_ms", "session_ms", "connect_ms", "startup_ms", "first_word_ms", "finalise_ms")
_WHOLE_MS = re.compile(r"^\d{1,6}$")


def session_lifetime() -> int:
    return settings.VOICE_MAX_SECONDS + FINISH_GRACE_SECONDS


def start_session(request, visitor) -> str:
    """Records who opened a live session and returns the signed, opaque token the browser must use to finish it."""
    user = request.user if request.user.is_authenticated else None
    now = timezone.now()
    session = Session.objects.create(
        user=user, visitor=visitor, model=settings.OPENAI_LIVE_TRANSCRIBE_MODEL[:100], created_at=now,
        audience=UsageEvent.Audience.REGISTERED if user else UsageEvent.Audience.ANONYMOUS,
        expires_at=now + timedelta(seconds=session_lifetime()))
    return signing.dumps(session.pk.hex, salt=SESSION_SALT)


def read_session_token(token: str) -> uuid.UUID:
    try:
        return uuid.UUID(signing.loads(token, salt=SESSION_SALT, max_age=session_lifetime()))
    except signing.SignatureExpired:
        raise VoiceError("realtime_session_expired", VOICE_UNAVAILABLE, 403) from None
    except (signing.BadSignature, TypeError, ValueError, AttributeError):
        raise VoiceError("realtime_session_invalid", VOICE_UNAVAILABLE, 403) from None


def parse_seconds(raw) -> Decimal | None:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    return value.quantize(SECONDS) if value.is_finite() and value > 0 else None


def parse_timings(data) -> dict:
    """The timings a browser reported when finishing a session: whole milliseconds up to MAX_MS; anything else is
    dropped silently, as is every unknown field."""
    timings = {}
    for field in TIMING_FIELDS:
        raw = data.get(field)
        if isinstance(raw, str) and _WHOLE_MS.match(raw) and int(raw) <= MAX_MS:
            timings[field] = int(raw)
    if data.get("final_received") in ("0", "1"):
        timings["final_received"] = data.get("final_received") == "1"
    return timings


def metered_seconds(session, reported: Decimal | None, now) -> tuple[Decimal, str]:
    """The provider's duration when it fits what the server saw; otherwise the server-observed session window."""
    elapsed = Decimal(str(max((now - session.created_at).total_seconds(), 0)))
    window = min(elapsed, Decimal(settings.VOICE_MAX_SECONDS + METER_SLACK_SECONDS)).quantize(SECONDS)
    if reported is not None and reported <= min(elapsed + REPORT_TOLERANCE, window + REPORT_TOLERANCE):
        return reported, AudioUsageEvent.MeteringSource.PROVIDER
    return window, AudioUsageEvent.MeteringSource.STREAM_DURATION


def finish_session(session_id, *, outcome: str, reported_seconds: Decimal | None, timings: dict | None = None):
    """Closes a session exactly once and writes its single ledger row, with the browser's timings; later calls for the
    same session do nothing."""
    status, error_code = OUTCOMES[outcome]
    now = timezone.now()
    with transaction.atomic():
        session = Session.objects.select_for_update().filter(pk=session_id, status=Session.Status.OPEN).first()
        if session is None:
            return None
        session.status, session.finished_at = Session.Status.FINISHED, now
        session.save(update_fields=["status", "finished_at"])
        if outcome == "connect_failed":
            seconds, source = Decimal("0.00"), AudioUsageEvent.MeteringSource.STREAM_DURATION  # No audio was sent.
        else:
            seconds, source = metered_seconds(session, reported_seconds, now)
        return record_audio_event(
            user=session.user, audience=session.audience, visitor=session.visitor,
            operation=AudioUsageEvent.Operation.TRANSCRIPTION, status=status, error_code=error_code,
            usage=AudioUsage(model=session.model, audio_seconds=seconds), stt_mode=LIVE, metering_source=source,
            provider_called=True, timings=timings)


def abandon_expired_sessions(now=None) -> int:
    """Sessions the browser never finished (closed tab, lost network): one ledger row each, with the cost left unknown."""
    now = now or timezone.now()
    abandoned = 0
    for pk in Session.objects.filter(status=Session.Status.OPEN, expires_at__lt=now).values_list("pk", flat=True):
        with transaction.atomic():
            session = Session.objects.select_for_update().filter(pk=pk, status=Session.Status.OPEN).first()
            if session is None:
                continue
            session.status, session.finished_at = Session.Status.ABANDONED, now
            session.save(update_fields=["status", "finished_at"])
            record_audio_event(user=session.user, audience=session.audience, visitor=session.visitor,
                               operation=AudioUsageEvent.Operation.TRANSCRIPTION, status=AudioUsageEvent.Status.FAILED,
                               error_code="realtime_abandoned", model=session.model, stt_mode=LIVE, provider_called=True)
            abandoned += 1
    return abandoned
