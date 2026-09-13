"""Server-side voice: transcription of a finished recording, and British English speech generated on request.

Audio, transcripts, speech text and provider payloads are never persisted or logged here.
"""
import base64
import json
from dataclasses import dataclass
from decimal import Decimal

import httpx
from django.conf import settings
from django.core import signing
from openai import APITimeoutError, OpenAI, OpenAIError

# The single source of the accent requirement: every speech request sends exactly these instructions.
BRITISH_TTS_INSTRUCTIONS = (
    "Speak in natural contemporary British English with a neutral Southern British accent. "
    "Use British pronunciation, stress and intonation. Sound warm, clear and conversational. "
    "Do not use an American accent. Do not exaggerate Received Pronunciation. "
    "Speak at a normal, learner-friendly conversational pace without sounding slow, theatrical or robotic. "
    "Read exactly the supplied English sentence and add no commentary."
)
TRANSCRIPTION_PROMPT = "The speaker may use British English, Romanian, or both in the same recording."
SPEECH_TARGETS = ("correction", "native", "translation")
SPEECH_TOKEN_SALT = "corect.speech.v1"
MAX_SPEECH_CHARACTERS = 4096
SPEECH_UNAVAILABLE = "Acest audio nu mai este disponibil. Trimite din nou textul ca să asculți."
VOICE_UNAVAILABLE = "Vocea este momentan indisponibilă. Încearcă din nou mai târziu."
TOO_SLOW = "A durat prea mult. Încearcă din nou."
# Sniffed container -> (extension sent to the provider, MIME sent to the provider, accepted declared content types).
AUDIO_FORMATS = {
    "webm": ("webm", "audio/webm", ("audio/webm", "video/webm")),
    "mp4": ("m4a", "audio/mp4", ("audio/mp4", "audio/m4a", "audio/x-m4a", "audio/aac", "video/mp4")),
    "wav": ("wav", "audio/wav", ("audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave")),
    "mpeg": ("mp3", "audio/mpeg", ("audio/mpeg", "audio/mp3")),
}


class VoiceError(Exception):
    def __init__(self, code, message=VOICE_UNAVAILABLE, status=503, usage=None):
        self.code = code
        self.message = message
        self.status = status
        self.usage = usage
        super().__init__(code)


@dataclass(frozen=True)
class AudioUsage:
    """Provider-reported usage for one audio call. Never holds audio, transcripts or speech text."""

    model: str
    voice: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    audio_seconds: Decimal | None = None


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def sniff_audio(head: bytes) -> str | None:
    """Identifies the audio container from its first bytes; the declared content type alone is never trusted."""
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if head[4:8] == b"ftyp":
        return "mp4"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "mpeg"
    return None


def declared_type_matches(audio_format: str, content_type: str) -> bool:
    return content_type.split(";")[0].strip().lower() in AUDIO_FORMATS[audio_format][2]


def _client():
    if not settings.OPENAI_API_KEY:
        raise VoiceError("voice_not_configured")
    return OpenAI(api_key=settings.OPENAI_API_KEY, timeout=settings.OPENAI_TIMEOUT, max_retries=0)


def transcription_usage(response) -> AudioUsage:
    usage = getattr(response, "usage", None)
    seconds = getattr(usage, "seconds", None)
    return AudioUsage(
        model=settings.OPENAI_TRANSCRIBE_MODEL,
        input_tokens=_count(getattr(usage, "input_tokens", None)),
        output_tokens=_count(getattr(usage, "output_tokens", None)),
        total_tokens=_count(getattr(usage, "total_tokens", None)),
        audio_seconds=(Decimal(str(seconds)).quantize(Decimal("0.01"))
                       if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) else None),
    )


def transcribe(data: bytes, audio_format: str) -> tuple[str, AudioUsage]:
    extension, mime, _ = AUDIO_FORMATS[audio_format]
    languages = settings.VOICE_TRANSCRIBE_LANGUAGES
    try:
        with _client() as client:
            response = client.audio.transcriptions.create(
                model=settings.OPENAI_TRANSCRIBE_MODEL, file=(f"recording.{extension}", data, mime),
                response_format="json", prompt=TRANSCRIPTION_PROMPT,
                # English and Romanian hints without forcing one language, so mixed speech still works.
                extra_body={"languages": languages} if languages else None,
            )
    except APITimeoutError:
        raise VoiceError("transcription_timeout", TOO_SLOW) from None
    except (OpenAIError, httpx.HTTPError, ValueError):
        raise VoiceError("transcription_failed") from None
    usage = transcription_usage(response)
    text = " ".join(str(getattr(response, "text", "") or "").split())
    if not text:
        raise VoiceError("transcription_empty", "Nu am înțeles înregistrarea. Încearcă din nou.", 422, usage)
    return text, usage


def _sse_payloads(lines):
    """The JSON payload of every `data:` line in a server-sent event stream."""
    for line in lines:
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload and payload != "[DONE]":
                yield json.loads(payload)


def synthesize_speech(text: str) -> tuple[bytes, AudioUsage]:
    """MP3 audio for one approved English sentence, with usage from the provider's speech.audio.done event."""
    model, voice = settings.OPENAI_TTS_MODEL, settings.OPENAI_TTS_VOICE
    audio, reported = bytearray(), None
    try:
        with _client() as client, client.audio.speech.with_streaming_response.create(
                model=model, voice=voice, input=text, instructions=BRITISH_TTS_INSTRUCTIONS, response_format="mp3",
                speed=settings.OPENAI_TTS_SPEED, stream_format="sse") as response:
            for event in _sse_payloads(response.iter_lines()):
                if event.get("type") == "speech.audio.delta" and isinstance(event.get("audio"), str):
                    audio.extend(base64.b64decode(event["audio"]))
                elif event.get("type") == "speech.audio.done" and isinstance(event.get("usage"), dict):
                    reported = event["usage"]
    except (APITimeoutError, httpx.TimeoutException):
        raise VoiceError("tts_timeout", TOO_SLOW) from None
    except (OpenAIError, httpx.HTTPError, ValueError):
        raise VoiceError("tts_failed") from None
    reported = reported or {}
    usage = AudioUsage(model=model, voice=voice, input_tokens=_count(reported.get("input_tokens")),
                       output_tokens=_count(reported.get("output_tokens")), total_tokens=_count(reported.get("total_tokens")))
    if not audio:
        raise VoiceError("tts_failed", usage=usage)
    return bytes(audio), usage


def make_speech_token(text: str, target: str, usage_event_id=None) -> str:
    """A signed, expiring permission to speak exactly this Corect.uk-generated sentence."""
    return signing.dumps({"t": text, "k": target, "e": usage_event_id}, salt=SPEECH_TOKEN_SALT, compress=True)


def read_speech_token(token: str) -> tuple[str, str, int | None]:
    invalid = VoiceError("speech_token_invalid", SPEECH_UNAVAILABLE, 403)
    try:
        payload = signing.loads(token, salt=SPEECH_TOKEN_SALT, max_age=settings.VOICE_SPEECH_TOKEN_MAX_AGE)
    except signing.SignatureExpired:
        raise VoiceError("speech_token_expired", "Acest audio a expirat. Trimite din nou textul ca să asculți.", 403) from None
    except (signing.BadSignature, ValueError):
        raise invalid from None
    if not isinstance(payload, dict):
        raise invalid
    text, target, event = payload.get("t"), payload.get("k"), payload.get("e")
    if target not in SPEECH_TARGETS or not isinstance(text, str) or not text.strip() or len(text) > MAX_SPEECH_CHARACTERS:
        raise invalid
    return text, target, _count(event)
