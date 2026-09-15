import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class AssistantRequest(models.Model):
    class Kind(models.TextChoices):
        """The effective internal operation (apps/assistant/languages.py), never the public action."""
        CORRECTION = "correction", "Correction (English)"
        TRANSLATION = "translation", "Translation into British English"
        UNCLASSIFIED = "unclassified", "Unclassified (failed before the language was known)"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE)
    request_type = models.CharField(max_length=12, choices=Kind.choices)
    original_text = models.TextField(blank=True)
    result_text = models.TextField(blank=True)
    detected_language = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    model_used = models.CharField(max_length=100)
    prompt_version = models.CharField(max_length=30)
    status = models.CharField(max_length=10, choices=[("success", "Success"), ("failed", "Failed")])
    error_code = models.CharField(max_length=40, blank=True)
    result_data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "-created_at"])]

    def __str__(self):
        return f"{self.get_request_type_display()} #{self.pk} ({self.status})"


class GrammarCorrection(models.Model):
    request = models.ForeignKey(AssistantRequest, related_name="corrections", on_delete=models.CASCADE)
    original = models.TextField()
    replacement = models.TextField()
    category = models.CharField(max_length=40, db_index=True)
    severity = models.CharField(max_length=15)
    explanation_ro = models.TextField()
    is_british_preference = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class RateBucket(models.Model):
    """Technical rate-limit and abuse-guardrail counters (never the plan quota): one row per key and time window."""
    key = models.CharField(max_length=100, unique=True)
    count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)


class NaturalizeUsage(models.Model):
    """The plan quota: successful „Vreau să sune natural!” uses of one actor on one London calendar day.

    `reserved` counts requests still being processed. A request reserves a use before the model call, which becomes
    `used` only when a result is produced and is released otherwise, so concurrent requests can never pass the limit and
    a failure never costs a use. The actor is the rate-limit identity (a user id or a keyed hash of the IP address): no
    text, no IP address. Rows are deleted by cleanup_assistant.
    """
    actor = models.CharField(max_length=70)
    day = models.DateField(db_index=True)
    used = models.PositiveIntegerField(default=0)
    reserved = models.PositiveIntegerField(default=0)
    reserved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["actor", "day"], name="unique_actor_naturalize_day")]


class RealtimeTranscriptionSession(models.Model):
    """Operational state of one live transcription: who opened it and whether it was accounted for.

    Holds no audio, transcript, client secret, OpenAI identifier or IP address. Removed by cleanup_assistant.
    """

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        FINISHED = "finished", "Finished"
        ABANDONED = "abandoned", "Abandoned"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    visitor = models.ForeignKey("analytics.AnonymousVisitor", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="+")
    audience = models.CharField(max_length=10)
    model = models.CharField(max_length=100)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True, db_index=True)


class SubmissionClaim(models.Model):
    actor = models.CharField(max_length=70)
    token = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["actor", "token"], name="unique_actor_submission")]
