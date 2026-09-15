"""Lightweight spaced repetition, decided in Python: success moves a review later, failure brings it closer."""
from datetime import timedelta

from django.utils import timezone

from apps.learning.models import UserMistakePattern

from . import rules

Status = UserMistakePattern.Status


def schedule_after_practice(pattern, correct, total, now=None):
    """Updates the review step and next review for one pattern after a practice session (not saved here)."""
    if not total:
        return pattern
    now = now or timezone.now()
    accuracy = correct / total
    last = len(rules.REVIEW_SCHEDULE_DAYS) - 1
    if pattern.status == Status.MASTERED and accuracy >= rules.SESSION_SUCCESS:
        pattern.next_review_at = now + timedelta(days=rules.MASTERED_REINFORCEMENT_DAYS)
    elif accuracy >= rules.SESSION_SUCCESS:
        pattern.review_step = min(pattern.review_step + 1, last)
        pattern.next_review_at = now + timedelta(days=rules.REVIEW_SCHEDULE_DAYS[pattern.review_step])
    elif accuracy < rules.SESSION_FAILURE:
        pattern.review_step = max(pattern.review_step - 2, 0)
        pattern.next_review_at = now + timedelta(days=rules.REVIEW_SCHEDULE_DAYS[0])
    else:
        pattern.next_review_at = now + timedelta(days=rules.REVIEW_SCHEDULE_DAYS[pattern.review_step])
    pattern.last_practised_at = now
    return pattern


def due_patterns(user, now=None):
    now = now or timezone.now()
    return UserMistakePattern.objects.filter(user=user, next_review_at__lte=now).order_by("next_review_at")
