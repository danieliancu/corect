from django.conf import settings
from django.db import models


class AssistantRequest(models.Model):
    class Kind(models.TextChoices):
        CORRECTION = "correction", "Correction"
        TRANSLATION = "translation", "Translation"

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
    key = models.CharField(max_length=100, unique=True)
    count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)


class SubmissionClaim(models.Model):
    actor = models.CharField(max_length=70)
    token = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["actor", "token"], name="unique_actor_submission")]
