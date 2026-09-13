"""Voice endpoints: start and finish live transcription, transcribe a finished recording, and speak a signed sentence
that Corect.uk generated."""
import io
import logging

from django.conf import settings
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.files.uploadhandler import FileUploadHandler, StopUpload
from django.db import DatabaseError
from django.http import Http404, HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.http import require_POST

from apps.analytics.models import AudioUsageEvent
from apps.analytics.services.recording import record_audio_event
from apps.analytics.services.visitors import attach_visitor_cookie, existing_visitor, get_or_create_visitor
from .services.limits import actor_key, claim_voice
from .services.openai_client import AssistantError
from .services.realtime import OUTCOMES, finish_session, parse_seconds, read_session_token, start_session
from .services.voice import (REALTIME_CALLS_URL, SPEECH_UNAVAILABLE, VOICE_UNAVAILABLE, VoiceError, create_realtime_secret,
                             declared_type_matches, read_speech_token, sniff_audio, synthesize_speech, transcribe)

logger = logging.getLogger("apps.assistant")
MULTIPART_OVERHEAD = 64 * 1024
TOO_LARGE = "Înregistrarea este prea lungă. Păstreaz-o sub un minut."
Operation = AudioUsageEvent.Operation
Status = AudioUsageEvent.Status


class BoundedMemoryUploadHandler(FileUploadHandler):
    """Keeps the recording in memory only (never a temporary file) and stops once it passes VOICE_MAX_BYTES."""

    too_large = False

    def new_file(self, *args, **kwargs):
        super().new_file(*args, **kwargs)
        self.buffer = bytearray()

    def receive_data_chunk(self, raw_data, start):
        self.buffer.extend(raw_data)
        if len(self.buffer) > settings.VOICE_MAX_BYTES:
            self.too_large = True
            self.buffer = bytearray()
            raise StopUpload(connection_reset=False)
        return None

    def file_complete(self, file_size):
        # The uploaded filename is deliberately dropped.
        return InMemoryUploadedFile(io.BytesIO(bytes(self.buffer)), self.field_name, "recording", self.content_type,
                                    len(self.buffer), self.charset, self.content_type_extra)


def error_response(error: VoiceError):
    response = JsonResponse({"error": error.message, "code": error.code}, status=error.status)
    if error.code == "audio_rate_limit" and getattr(error, "retry_after", None):
        response["Retry-After"] = str(error.retry_after)
    return response


def finish(request, response, visitor):
    if not request.user.is_authenticated:
        attach_visitor_cookie(response, visitor)
    return response


def resolve_visitor(request):
    return existing_visitor(request) if request.user.is_authenticated else get_or_create_visitor(request)


def claim_or_reject(request, operation, visitor, **event):
    """Counts the call against the voice quota; returns an error response when the quota is full."""
    try:
        claim_voice(actor_key(request), operation)
    except AssistantError as exc:
        record_audio_event(request=request, operation=operation, status=Status.REJECTED, error_code=exc.code,
                           visitor=visitor, **event)
        logger.warning("voice_failed code=%s operation=%s", exc.code, operation)
        error = VoiceError(exc.code, exc.message, 429)
        error.retry_after = exc.retry_after
        return error_response(error)
    except DatabaseError:
        logger.error("voice_database_unavailable operation=%s", operation)
        return error_response(VoiceError("voice_unavailable", VOICE_UNAVAILABLE))
    return None


@require_POST
@csrf_protect
@never_cache
def start_realtime_transcription(request):
    """Mints a short-lived client secret for one live transcription session. The session configuration is fixed on the
    server: nothing in the request body is read, and the normal API key never leaves the server."""
    if not settings.VOICE_REALTIME_ENABLED:
        raise Http404
    visitor = resolve_visitor(request)
    live = {"stt_mode": AudioUsageEvent.SttMode.REALTIME, "model": settings.OPENAI_LIVE_TRANSCRIBE_MODEL}
    rejected = claim_or_reject(request, Operation.TRANSCRIPTION, visitor, **live)
    if rejected:
        return finish(request, rejected, visitor)
    try:
        secret, expires_at = create_realtime_secret()
    except VoiceError as exc:
        record_audio_event(request=request, operation=Operation.TRANSCRIPTION, status=Status.FAILED, error_code=exc.code,
                           visitor=visitor, provider_called=exc.code != "voice_not_configured", **live)
        logger.warning("voice_failed code=%s operation=realtime", exc.code)
        return finish(request, error_response(exc), visitor)
    try:
        session = start_session(request, visitor)
    except DatabaseError:
        logger.error("voice_database_unavailable operation=realtime")
        return error_response(VoiceError("voice_unavailable", VOICE_UNAVAILABLE))
    return finish(request, JsonResponse({"client_secret": secret, "expires_at": expires_at, "session": session,
                                         "calls_url": REALTIME_CALLS_URL, "max_seconds": settings.VOICE_MAX_SECONDS}),
                  visitor)


@require_POST
@csrf_protect
@never_cache
def finish_realtime_transcription(request):
    """Accounts for a live session exactly once. Receives only the signed session, how it ended and the provider's
    reported duration: never transcript text."""
    outcome = request.POST.get("outcome", "")
    try:
        if outcome not in OUTCOMES:
            raise VoiceError("realtime_session_invalid", VOICE_UNAVAILABLE, 400)
        session_id = read_session_token(request.POST.get("session", ""))
    except VoiceError as exc:
        logger.warning("voice_failed code=%s operation=realtime", exc.code)
        return error_response(exc)
    try:
        finish_session(session_id, outcome=outcome, reported_seconds=parse_seconds(request.POST.get("provider_seconds")))
    except DatabaseError:
        logger.error("voice_database_unavailable operation=realtime")
        return error_response(VoiceError("voice_unavailable", VOICE_UNAVAILABLE))
    return JsonResponse({"finished": True})


@csrf_exempt
@require_POST
def transcribe_recording(request):
    # Refuse oversized bodies before reading them, then keep the upload in memory. Upload handlers must be
    # replaced before anything reads the body, so CSRF is checked by the protected inner view instead.
    if int(request.META.get("CONTENT_LENGTH") or 0) > settings.VOICE_MAX_BYTES + MULTIPART_OVERHEAD:
        return error_response(VoiceError("audio_too_large", TOO_LARGE, 413))
    request.upload_handlers = [BoundedMemoryUploadHandler(request)]
    return _transcribe(request)


@csrf_protect
@never_cache
def _transcribe(request):
    handler = request.upload_handlers[0]
    upload = request.FILES.get("audio")
    if getattr(handler, "too_large", False):
        error = VoiceError("audio_too_large", TOO_LARGE, 413)
    elif upload is None:
        error = VoiceError("microphone_upload_invalid", "Nu am primit nicio înregistrare. Încearcă din nou.", 400)
    else:
        error = None
    if error:
        logger.warning("voice_failed code=%s operation=transcription", error.code)
        return error_response(error)
    data = upload.read()
    audio_format = sniff_audio(data[:16])
    if audio_format is None or not declared_type_matches(audio_format, upload.content_type or ""):
        logger.warning("voice_failed code=unsupported_audio operation=transcription")
        return error_response(VoiceError("unsupported_audio", "Formatul înregistrării nu este acceptat în acest browser.", 415))
    visitor = resolve_visitor(request)
    rejected = claim_or_reject(request, Operation.TRANSCRIPTION, visitor)
    if rejected:
        return finish(request, rejected, visitor)
    try:
        text, usage = transcribe(data, audio_format)
    except VoiceError as exc:
        record_audio_event(request=request, operation=Operation.TRANSCRIPTION, status=Status.FAILED, error_code=exc.code,
                           usage=exc.usage, visitor=visitor, provider_called=exc.code != "voice_not_configured")
        logger.warning("voice_failed code=%s operation=transcription", exc.code)
        return finish(request, error_response(exc), visitor)
    finally:
        del data
    record_audio_event(request=request, operation=Operation.TRANSCRIPTION, status=Status.SUCCESS, usage=usage,
                       visitor=visitor, provider_called=True)
    if len(text) > settings.ASSISTANT_MAX_CHARACTERS:
        message = f"Textul înregistrat este prea lung. Păstrează textul sub {settings.ASSISTANT_MAX_CHARACTERS} de caractere."
        return finish(request, error_response(VoiceError("transcript_too_long", message, 422)), visitor)
    return finish(request, JsonResponse({"text": text}), visitor)


@require_POST
@csrf_protect
@never_cache
def speak(request):
    """Speaks only a sentence carried by a valid signed token; raw text parameters are never read."""
    token = request.POST.get("token", "")
    try:
        if not token:
            raise VoiceError("speech_token_invalid", SPEECH_UNAVAILABLE, 400)
        text, target, usage_event_id = read_speech_token(token)
    except VoiceError as exc:
        logger.warning("voice_failed code=%s operation=speech", exc.code)
        return error_response(exc)
    visitor = resolve_visitor(request)
    event = {"speech_target": target, "usage_event_id": usage_event_id}
    rejected = claim_or_reject(request, Operation.SPEECH, visitor, **event)
    if rejected:
        return finish(request, rejected, visitor)
    try:
        audio, usage = synthesize_speech(text)
    except VoiceError as exc:
        record_audio_event(request=request, operation=Operation.SPEECH, status=Status.FAILED, error_code=exc.code,
                           usage=exc.usage, visitor=visitor, provider_called=exc.code != "voice_not_configured", **event)
        logger.warning("voice_failed code=%s operation=speech", exc.code)
        return finish(request, error_response(exc), visitor)
    record_audio_event(request=request, operation=Operation.SPEECH, status=Status.SUCCESS, usage=usage, visitor=visitor,
                       provider_called=True, **event)
    return finish(request, HttpResponse(audio, content_type="audio/mpeg"), visitor)
