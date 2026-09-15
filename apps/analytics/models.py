import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class AnonymousVisitor(models.Model):
    """A browser known only by a random first-party cookie: never an IP address or submitted text."""

    class ConvertedVia(models.TextChoices):
        SIGNUP = "signup", "Signup"
        LOGIN = "login", "Sign-in"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    first_seen_at = models.DateTimeField(default=timezone.now, editable=False)
    last_seen_at = models.DateTimeField(default=timezone.now, editable=False)
    converted_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                       related_name="converted_visitors")
    converted_at = models.DateTimeField(null=True, blank=True)
    converted_via = models.CharField(max_length=10, blank=True, choices=ConvertedVia.choices)

    class Meta:
        ordering = ["-last_seen_at"]
        indexes = [models.Index(fields=["first_seen_at"], name="analytics_visitor_first_idx")]

    def __str__(self):
        return self.short_id

    @property
    def short_id(self):
        return f"anon-{self.id.hex[:6]}"


class UsageEvent(models.Model):
    """Append-only ledger: one row per validated "Vreau să sune natural!" submission. Holds no submitted or generated text."""

    class Audience(models.TextChoices):
        REGISTERED = "registered", "Registered"
        ANONYMOUS = "anonymous", "Anonymous"

    class Kind(models.TextChoices):
        CORRECTION = "correction", "English correction"
        TRANSLATION = "translation", "Translation into British English"
        UNCLASSIFIED = "unclassified", "Unclassified"

    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        REJECTED = "rejected", "Rejected"

    audience = models.CharField(max_length=10, choices=Audience.choices)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="usage_events")
    visitor = models.ForeignKey(AnonymousVisitor, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="usage_events")
    request_type = models.CharField(
        max_length=12, choices=Kind.choices,
        help_text="Effective operation: correction (English input) or translation (e.g. Romanian into British English); "
                  "unclassified when the request failed before its language was known.")
    source_language = models.CharField(max_length=12, blank=True,
                                       help_text="Language the input was classified as (en, ro, other...); empty if unknown.")
    auto_translated = models.BooleanField(default=False, help_text="Legacy, before the single action: Romanian sent to "
                                                                   "Correct and translated instead.")
    model = models.CharField(max_length=100, blank=True, help_text="Configured OPENAI_MODEL.")
    response_model = models.CharField(max_length=100, blank=True, help_text="Model name reported by the provider.")
    prompt_version = models.CharField(max_length=30, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices)
    error_code = models.CharField(max_length=40, blank=True)
    provider_calls = models.PositiveSmallIntegerField(default=0)
    input_tokens = models.PositiveIntegerField(null=True, blank=True)
    cached_input_tokens = models.PositiveIntegerField(null=True, blank=True)
    output_tokens = models.PositiveIntegerField(null=True, blank=True)
    reasoning_tokens = models.PositiveIntegerField(null=True, blank=True)
    total_tokens = models.PositiveIntegerField(null=True, blank=True)
    estimated_cost = models.DecimalField(max_digits=14, decimal_places=8, null=True, blank=True,
                                         help_text="USD at recording time; empty when pricing or tokens are unknown.")
    duration_ms = models.PositiveIntegerField(null=True, blank=True,
                                              help_text="Server time from receiving the request to recording it.")
    provider_duration_ms = models.PositiveIntegerField(null=True, blank=True,
                                                       help_text="Time spent in the generative provider call.")
    moderation_duration_ms = models.PositiveIntegerField(null=True, blank=True,
                                                         help_text="Moderation check, run alongside the provider call.")
    assistant_request = models.OneToOneField("assistant.AssistantRequest", null=True, blank=True,
                                             on_delete=models.SET_NULL, related_name="usage_event")
    is_backfilled = models.BooleanField(default=False, help_text="Created from history saved before usage tracking.")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"], name="analytics_usage_user_idx"),
            models.Index(fields=["visitor", "-created_at"], name="analytics_usage_visitor_idx"),
            models.Index(fields=["audience", "created_at"], name="analytics_usage_audience_idx"),
            models.Index(fields=["status", "created_at"], name="analytics_usage_status_idx"),
            models.Index(fields=["request_type", "created_at"], name="analytics_usage_type_idx"),
            models.Index(fields=["model"], name="analytics_usage_model_idx"),
        ]

    def __str__(self):
        return f"{self.get_request_type_display()} {self.status} #{self.pk}"


class AudioUsageEvent(models.Model):
    """Append-only audio ledger: one row per voice transcription or speech request. No text, audio or IP address."""

    class Operation(models.TextChoices):
        TRANSCRIPTION = "transcription", "Voice input (speech to text)"
        SPEECH = "speech", "Voice output (text to speech)"

    class SpeechTarget(models.TextChoices):
        CORRECTION = "correction", "Correction"
        NATIVE = "native", "Native version"
        TRANSLATION = "translation", "Translation into English"

    class SttMode(models.TextChoices):
        FILE = "file", "File (after recording)"
        REALTIME = "realtime", "Realtime (live)"

    class MeteringSource(models.TextChoices):
        PROVIDER = "provider", "Provider-reported duration"
        STREAM_DURATION = "stream_duration", "Server-observed session window"

    Status = UsageEvent.Status  # The same success / failed / rejected meanings as the text ledger.

    operation = models.CharField(max_length=13, choices=Operation.choices)
    audience = models.CharField(max_length=10, choices=UsageEvent.Audience.choices)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="audio_usage_events")
    visitor = models.ForeignKey(AnonymousVisitor, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="audio_usage_events")
    usage_event = models.ForeignKey(UsageEvent, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="audio_usage_events", help_text="The correction that was spoken.")
    speech_target = models.CharField(max_length=12, blank=True, choices=SpeechTarget.choices)
    model = models.CharField(max_length=100, blank=True)
    voice = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=10, choices=UsageEvent.Status.choices)
    error_code = models.CharField(max_length=40, blank=True)
    provider_calls = models.PositiveSmallIntegerField(default=0)
    input_tokens = models.PositiveIntegerField(null=True, blank=True)
    output_tokens = models.PositiveIntegerField(null=True, blank=True)
    total_tokens = models.PositiveIntegerField(null=True, blank=True)
    stt_mode = models.CharField("transcription mode", max_length=8, blank=True, choices=SttMode.choices,
                                help_text="Speech to text only: live in the browser, or a finished recording.")
    audio_seconds = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True,
                                        help_text="Duration of transcribed audio; see the metering source.")
    metering_source = models.CharField(max_length=16, blank=True, choices=MeteringSource.choices,
                                       help_text="Where the audio duration came from.")
    estimated_cost = models.DecimalField(max_digits=14, decimal_places=8, null=True, blank=True,
                                         help_text="USD at recording time; empty when pricing or usage is unknown.")
    provider_duration_ms = models.PositiveIntegerField(null=True, blank=True,
                                                       help_text="Server-measured provider call (file transcription, speech).")
    # Live transcription timings reported by the browser (bounded 0-600000 ms, numbers only).
    mic_ms = models.PositiveIntegerField(null=True, blank=True, help_text="Microphone press to microphone ready.")
    session_ms = models.PositiveIntegerField(null=True, blank=True, help_text="Session request to Corect.uk.")
    connect_ms = models.PositiveIntegerField(null=True, blank=True, help_text="WebRTC connection to the provider.")
    startup_ms = models.PositiveIntegerField(null=True, blank=True, help_text="Microphone press to listening.")
    first_word_ms = models.PositiveIntegerField(null=True, blank=True,
                                                      help_text="Listening to the first transcribed words.")
    finalise_ms = models.PositiveIntegerField(null=True, blank=True, help_text="Stop to final transcript.")
    final_received = models.BooleanField(null=True, blank=True,
                                         help_text="Whether the final transcript arrived before the safety timeout.")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["operation", "created_at"], name="analytics_audio_op_idx"),
            models.Index(fields=["user", "-created_at"], name="analytics_audio_user_idx"),
            models.Index(fields=["visitor", "-created_at"], name="analytics_audio_visitor_idx"),
            models.Index(fields=["audience", "created_at"], name="analytics_audio_audience_idx"),
            models.Index(fields=["model", "created_at"], name="analytics_audio_model_idx"),
            models.Index(fields=["status", "created_at"], name="analytics_audio_status_idx"),
            models.Index(fields=["stt_mode", "created_at"], name="analytics_audio_stt_mode_idx"),
        ]

    def __str__(self):
        return f"{self.get_operation_display()} {self.status} #{self.pk}"


class LearningUsageEvent(models.Model):
    """Append-only learning AI ledger: one row per AI call made by a learning feature (exercise generation, open-answer
    evaluation). Holds no learning content, answers or prompts."""

    class Feature(models.TextChoices):
        PRACTICE = "practice", "Personalised practice"
        OPEN_ANSWER = "open_answer", "Open-answer evaluation"
    Status = UsageEvent.Status

    audience = models.CharField(max_length=10, choices=UsageEvent.Audience.choices)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="learning_usage_events")
    visitor = models.ForeignKey(AnonymousVisitor, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="learning_usage_events")
    feature = models.CharField(max_length=12, choices=Feature.choices)
    model = models.CharField(max_length=100, blank=True)
    response_model = models.CharField(max_length=100, blank=True)
    prompt_version = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=10, choices=UsageEvent.Status.choices)
    error_code = models.CharField(max_length=40, blank=True)
    provider_calls = models.PositiveSmallIntegerField(default=0)
    input_tokens = models.PositiveIntegerField(null=True, blank=True)
    cached_input_tokens = models.PositiveIntegerField(null=True, blank=True)
    output_tokens = models.PositiveIntegerField(null=True, blank=True)
    reasoning_tokens = models.PositiveIntegerField(null=True, blank=True)
    total_tokens = models.PositiveIntegerField(null=True, blank=True)
    estimated_cost = models.DecimalField(max_digits=14, decimal_places=8, null=True, blank=True,
                                         help_text="USD at recording time; empty when pricing or tokens are unknown.")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["feature", "created_at"], name="analytics_learn_feature_idx"),
            models.Index(fields=["user", "-created_at"], name="analytics_learn_user_idx"),
            models.Index(fields=["audience", "created_at"], name="analytics_learn_audience_idx"),
            models.Index(fields=["model", "created_at"], name="analytics_learn_model_idx"),
            models.Index(fields=["status", "created_at"], name="analytics_learn_status_idx"),
        ]

    def __str__(self):
        return f"{self.get_feature_display()} {self.status} #{self.pk}"
