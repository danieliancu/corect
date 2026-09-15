"""Deterministic learning priority: what this learner most needs to practise. No AI."""
from apps.learning.models import UserMistakePattern

from . import rules

Status = UserMistakePattern.Status


def score_pattern(pattern, *, recent_successes, recent_failures, now):
    score = (3 * pattern.recent_occurrence_count + min(pattern.occurrence_count, 10)
             + 2 * recent_failures - 1.5 * recent_successes)
    if pattern.status == Status.RESURFACED:
        score += 5
    if pattern.next_review_at and pattern.next_review_at <= now:
        score += 1 + 0.5 * min((now - pattern.next_review_at).days, 7)
    if pattern.last_seen_at and (now - pattern.last_seen_at).days < 7:
        score += 4
    if pattern.status == Status.MASTERED:
        score *= 0.2
    return round(max(score, 0.0), 2)


def ranked_patterns(user):
    return UserMistakePattern.objects.filter(user=user).order_by("-priority_score", "-last_seen_at", "pattern_key")


def top_pattern_keys(user, n=rules.TOP_PATTERNS):
    return list(ranked_patterns(user).exclude(status=Status.MASTERED).values_list("pattern_key", flat=True)[:n])
