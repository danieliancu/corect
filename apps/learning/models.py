"""The personal learning profile: mistake patterns built from saved corrections, stored exercises, practice sessions
and attempts. Everything belongs to one user and is deleted with the account."""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

class MistakeOccurrence(models.Model):
    """One genuine mistake from a saved correction, linked to its pattern. The correction itself is never changed."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mistake_occurrences")
    correction = models.OneToOneField("assistant.GrammarCorrection", on_delete=models.CASCADE,
                                      related_name="learning_occurrence")
    category = models.CharField(max_length=40)
    pattern_key = models.CharField(max_length=60)
    occurred_at = models.DateTimeField()

    class Meta:
        indexes = [models.Index(fields=["user", "pattern_key", "occurred_at"], name="learning_occ_pattern_idx"),
                   models.Index(fields=["user", "occurred_at"], name="learning_occ_user_idx")]


class UserMistakePattern(models.Model):
    """What one learner struggles with, recalculated in Python from occurrences and practice (never by AI)."""

    class Status(models.TextChoices):
        NEW = "new", "New"
        RECURRING = "recurring", "Recurring"
        IMPROVING = "improving", "Improving"
        MASTERED = "mastered", "Mastered"
        RESURFACED = "resurfaced", "Resurfaced"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mistake_patterns")
    category = models.CharField(max_length=40)
    pattern_key = models.CharField(max_length=60)
    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()
    occurrence_count = models.PositiveIntegerField(default=0)
    recent_occurrence_count = models.PositiveIntegerField(default=0, help_text="Last 30 days.")
    previous_period_count = models.PositiveIntegerField(default=0, help_text="The 30 days before that.")
    successful_practice_count = models.PositiveIntegerField(default=0)
    failed_practice_count = models.PositiveIntegerField(default=0)
    mastery = models.PositiveSmallIntegerField(default=0, help_text="0–100, from practice accuracy and time without the mistake.")
    recurrence_score = models.FloatField(default=0)
    priority_score = models.FloatField(default=0)
    review_step = models.PositiveSmallIntegerField(default=0)
    next_review_at = models.DateTimeField(null=True, blank=True)
    last_practised_at = models.DateTimeField(null=True, blank=True)
    last_mastered_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.NEW)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "pattern_key"], name="unique_user_mistake_pattern")]
        indexes = [models.Index(fields=["user", "-priority_score"], name="learning_pattern_priority_idx"),
                   models.Index(fields=["user", "next_review_at"], name="learning_pattern_review_idx")]

    def __str__(self):
        return f"{self.user} · {self.pattern_key} ({self.status})"


class Exercise(models.Model):
    """A stored exercise: AI-generated for one learner (reused until retired) or editorial (shared, no user)."""

    class Type(models.TextChoices):
        MULTIPLE_CHOICE = "multiple_choice", "Multiple choice"
        FILL_BLANK = "fill_blank", "Fill the blank"
        CHOOSE_PHRASE = "choose_phrase", "Choose the correct phrase"
        REWRITE = "rewrite", "Rewrite the sentence"
        SHORT_CORRECTION = "short_correction", "Short correction"

    class Source(models.TextChoices):
        GENERATED = "generated", "Generated"
        EDITORIAL = "editorial", "Editorial"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE,
                             related_name="exercises")
    pattern_key = models.CharField(max_length=60, blank=True)
    exercise_type = models.CharField(max_length=20, choices=Type.choices)
    question = models.TextField()
    options = models.JSONField(default=list, blank=True)
    correct_answer = models.TextField()
    accepted_answers = models.JSONField(default=list, blank=True)
    open_ended = models.BooleanField(default=False, help_text="Many correct formulations: may need AI evaluation.")
    explanation_ro = models.TextField()
    difficulty = models.PositiveSmallIntegerField(default=1)
    uk_context = models.CharField(max_length=200, blank=True)
    source = models.CharField(max_length=10, choices=Source.choices)
    batch_id = models.UUIDField(null=True, blank=True)
    generated_at = models.DateTimeField(default=timezone.now)
    prompt_version = models.CharField(max_length=40, blank=True)
    model = models.CharField(max_length=100, blank=True)
    usage_event = models.ForeignKey("analytics.LearningUsageEvent", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="exercises", help_text="The generation call (tokens and cost).")
    times_shown = models.PositiveIntegerField(default=0)
    times_correct = models.PositiveIntegerField(default=0)
    retired_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "pattern_key", "retired_at"], name="learning_exercise_pool_idx")]

    def __str__(self):
        return f"{self.get_exercise_type_display()} · {self.pattern_key}"


class PracticeSession(models.Model):
    class Kind(models.TextChoices):
        TODAY = "today", "Today"
        PATTERN = "pattern", "Pattern"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="practice_sessions")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    pattern_key = models.CharField(max_length=60, blank=True)
    exercise_ids = models.JSONField(default=list)
    exercise_patterns = models.JSONField(default=list, help_text="The learner's pattern each exercise practises.")
    position = models.PositiveSmallIntegerField(default=0)
    correct_count = models.PositiveSmallIntegerField(default=0)
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "-started_at"], name="learning_session_user_idx")]


class ExerciseAttempt(models.Model):
    class GradedBy(models.TextChoices):
        DETERMINISTIC = "deterministic", "Deterministic"
        AI = "ai", "AI evaluation"
        UNVERIFIED = "unverified", "Not verified"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="exercise_attempts")
    session = models.ForeignKey(PracticeSession, null=True, blank=True, on_delete=models.CASCADE, related_name="attempts")
    exercise = models.ForeignKey(Exercise, on_delete=models.CASCADE, related_name="attempts")
    pattern_key = models.CharField(max_length=60, blank=True)
    answer = models.TextField(blank=True)
    is_correct = models.BooleanField(null=True, blank=True)
    graded_by = models.CharField(max_length=13, choices=GradedBy.choices)
    feedback_ro = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=["user", "pattern_key", "created_at"], name="learning_attempt_pattern_idx"),
                   models.Index(fields=["user", "created_at"], name="learning_attempt_user_idx")]
