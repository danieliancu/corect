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
    """Append-only ledger: one row per validated Correct/Translate submission. Holds no submitted or generated text."""

    class Audience(models.TextChoices):
        REGISTERED = "registered", "Registered"
        ANONYMOUS = "anonymous", "Anonymous"

    class Kind(models.TextChoices):
        CORRECTION = "correction", "Correction"
        TRANSLATION = "translation", "Translation"

    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        REJECTED = "rejected", "Rejected"

    audience = models.CharField(max_length=10, choices=Audience.choices)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="usage_events")
    visitor = models.ForeignKey(AnonymousVisitor, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="usage_events")
    request_type = models.CharField(max_length=12, choices=Kind.choices, help_text="The action the visitor chose.")
    auto_translated = models.BooleanField(default=False, help_text="Romanian sent to Correct and translated instead.")
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
    audio_seconds = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True,
                                        help_text="Provider-reported duration of transcribed audio.")
    estimated_cost = models.DecimalField(max_digits=14, decimal_places=8, null=True, blank=True,
                                         help_text="USD at recording time; empty when pricing or usage is unknown.")
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
        ]

    def __str__(self):
        return f"{self.get_operation_display()} {self.status} #{self.pk}"
