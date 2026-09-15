""""Pentru tine azi": which patterns to practise today and the practice sessions built from them. No AI decisions."""
from datetime import datetime, time

from django.db.models import Count, F, Q
from django.utils import timezone

from apps.learning.models import Exercise, PracticeSession, UserMistakePattern
from apps.learning.taxonomy import pattern_label

from . import rules
from .exercises import ensure_exercises
from .profile import refresh_if_stale, refresh_patterns
from .review import schedule_after_practice

Status = UserMistakePattern.Status
Kind = PracticeSession.Kind


def allocation(count):
    if count == 0:
        return []
    if count == 1:
        return [rules.TODAY_EXERCISES]
    if count == 2:
        return [3, 2]
    return list(rules.TODAY_SPLIT[:count])


def today_plan(user, now=None):
    """Up to three patterns (reviews due first, then highest priority) and how many exercises each gets."""
    now = now or timezone.now()
    refresh_if_stale(user, now)
    candidates = list(UserMistakePattern.objects.filter(user=user)
                      .exclude(Q(status=Status.MASTERED) & (Q(next_review_at__isnull=True) | Q(next_review_at__gt=now)))
                      .order_by("-priority_score", "-last_seen_at", "pattern_key")[:12])
    due = [pattern for pattern in candidates if pattern.next_review_at and pattern.next_review_at <= now]
    chosen = (due + [pattern for pattern in candidates if pattern not in due])[:rules.TOP_PATTERNS]
    counts = allocation(len(chosen))
    start_of_day = timezone.make_aware(datetime.combine(timezone.localdate(now), time.min))
    repeated = any(pattern.status in (Status.RECURRING, Status.RESURFACED) for pattern in chosen)
    return {
        "items": [{"pattern": pattern, "label": pattern_label(pattern.pattern_key), "count": count}
                  for pattern, count in zip(chosen, counts)],
        "total": sum(counts), "minutes": max(3, min(5, sum(counts))),
        "done_today": PracticeSession.objects.filter(user=user, kind=Kind.TODAY, completed_at__gte=start_of_day).exists(),
        "reason": ("Le-am ales din greșelile pe care le-ai repetat în ultima perioadă." if repeated
                   else "Le-am ales din greșelile tale recente."),
    }


def start_session(user, kind, pattern_key="", now=None):
    """Creates a session from stored exercises (generating a batch only when needed). Returns (session, error_code)."""
    now = now or timezone.now()
    ids, patterns, errors = [], [], []

    def add(target, count):
        exercises, error = ensure_exercises(user, target, count, now=now)
        errors.append(error)
        for exercise in exercises:
            if exercise.pk not in ids:
                ids.append(exercise.pk)
                patterns.append(target)

    if kind == Kind.TODAY:
        for item in today_plan(user, now)["items"]:
            add(item["pattern"].pattern_key, item["count"])
    elif kind == Kind.PATTERN and pattern_key:
        add(pattern_key, rules.TODAY_EXERCISES)
    if not ids:
        return None, next((error for error in errors if error), "no_exercises")
    session = PracticeSession.objects.create(user=user, kind=kind, pattern_key=pattern_key, exercise_ids=ids,
                                             exercise_patterns=patterns, started_at=now)
    Exercise.objects.filter(pk__in=ids).update(times_shown=F("times_shown") + 1)
    # A failed generation that stored exercises could cover is still reported, so the learner is told why.
    return session, next((error for error in errors if error), "")


def complete_session(session, now=None):
    """Applies spaced repetition once per practised pattern, then recalculates the profile."""
    now = now or timezone.now()
    results = session.attempts.filter(is_correct__isnull=False).exclude(pattern_key="").values("pattern_key").annotate(
        correct=Count("id", filter=Q(is_correct=True)), total=Count("id"))
    results = {row["pattern_key"]: row for row in results}
    patterns = list(UserMistakePattern.objects.filter(user=session.user, pattern_key__in=list(results)))
    for pattern in patterns:
        schedule_after_practice(pattern, results[pattern.pattern_key]["correct"], results[pattern.pattern_key]["total"], now)
    if patterns:
        UserMistakePattern.objects.bulk_update(patterns, ["review_step", "next_review_at", "last_practised_at"])
        refresh_patterns(session.user, [pattern.pattern_key for pattern in patterns], now=now)
    session.completed_at = now
    session.save(update_fields=["completed_at"])
    return session
